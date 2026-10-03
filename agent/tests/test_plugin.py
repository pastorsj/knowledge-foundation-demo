# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The execution-receipts plugin, driven the way Hermes calls its hooks.

Tool results come from the contract fixtures (recorded runs, in the current tools' result shape) and
from the market-analytics tools run on their test pack. Every receipt is checked against
contracts/schemas/receipt.schema.json.
"""

import copy
import importlib.util
import json
import re
import threading
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from typing import Any

import pytest
from common import AGENT
from common import ROOT
from common import load_yaml
from jsonschema import Draft202012Validator

PLUGIN_DIR = AGENT / "profile" / "plugins" / "execution_receipts"
_spec = importlib.util.spec_from_file_location("execution_receipts", PLUGIN_DIR / "__init__.py")
plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin)

CONTRACTS = ROOT / "contracts"
REGISTRY = json.loads((CONTRACTS / "tool-registry.json").read_text(encoding="utf-8"))
TOOLS = {tool["id"]: tool for tool in REGISTRY["tools"]}
RECEIPT_SCHEMA = Draft202012Validator(
    json.loads((CONTRACTS / "schemas" / "receipt.schema.json").read_text(encoding="utf-8")),
    format_checker=Draft202012Validator.FORMAT_CHECKER,
)
# jsonschema skips a format whose checker package is missing; date-time needs rfc3339-validator (pyproject.toml).
assert "date-time" in Draft202012Validator.FORMAT_CHECKER.checkers
FIXTURE_RECEIPTS = json.loads((CONTRACTS / "fixtures" / "receipts.json").read_text(encoding="utf-8"))

JOB = "2e1e9c6d-8c4a-4d2f-9716-1cb1e6659c78"
STRUCTURED = "market_analysis_structured"
DOCUMENTS = ["market_news", "market_regulations"]
EFFICIENT = "nvidia/nemotron-3-ultra-550b-a55b"
CAPABLE = "gpt-6-sol"
SCOPE = {
    "job_id": JOB,
    "sources": [
        {"id": "market_news", "capabilities": ["unstructured_retrieval"]},
        {"id": "market_regulations", "capabilities": ["unstructured_retrieval"]},
        {"id": STRUCTURED, "capabilities": ["structured_retrieval", "structured_prediction", "market_analytics"]},
    ],
    "database_name": "market_analysis",
    "collection": "aiq_market_intelligence_current",
    "models": {"efficient": EFFICIENT, "capable": CAPABLE},
}

# price_context and a rejected call: market-analytics tool output on tools/market-analytics/tests/fixture_pack.py.
PRICE_CONTEXT = {
    "operation_id": "price_context",
    "status": "succeeded",
    "source_id": "market_fixture_structured",
    "database_name": "market_fixture",
    "payload": {
        "frequency": "weekly",
        "summaries": [
            {
                "asset_id": "asset-alpha",
                "start_timestamp": "2026-06-05T21:00:00Z",
                "end_timestamp": "2026-06-30T21:00:00Z",
                "start_price": 100.79438572429191,
                "end_price": 99.21732905667542,
                "total_return": -0.015646274901960244,
                "minimum_price": 97.2655493120802,
                "maximum_price": 100.79438572429191,
                "average_volume": 4420869.8,
                "observation_count": 5,
            }
        ],
        "series": [
            {
                "asset_id": "asset-alpha",
                "timestamp": "2026-06-05T21:00:00Z",
                "adjusted_close": 100.79438572429191,
                "volume": 4981805.0,
            },
            {
                "asset_id": "asset-alpha",
                "timestamp": "2026-06-12T21:00:00Z",
                "adjusted_close": 97.27787115115679,
                "volume": 4777179.0,
            },
            {
                "asset_id": "asset-alpha",
                "timestamp": "2026-06-19T21:00:00Z",
                "adjusted_close": 97.2655493120802,
                "volume": 5171950.0,
            },
        ],
        "series_truncated": True,
    },
    "error": None,
    "engine": {"device": "cpu", "library": "pandas", "version": "2.3.3", "engine_id": "pandas-cpu.v1"},
    "timing": {
        "compute_ms": 8.152874994266313,
        "setup_ms": 0.6416250066831708,
        "engine_ms": 8.794500000949484,
        "total_ms": 9.425041987560689,
    },
    "rows_scanned": 22,
    "asset_count": 1,
    "warnings": ["The series is cut to the first 3 points."],
    "limitations": ["Prices are adjusted historical observations and are not investment advice."],
}
REJECTED_PRICE_CONTEXT = {
    "operation_id": "price_context",
    "status": "failed",
    "source_id": "market_fixture_structured",
    "database_name": "market_fixture",
    "payload": None,
    "error": {"code": "invalid_request", "message": "unknown asset 'NOPE'"},
    "engine": None,
    "timing": {
        "compute_ms": 0.01049999991664663,
        "setup_ms": 0.0899169989861548,
        "engine_ms": 0.10041699890280142,
        "total_ms": 0.6247919914312661,
    },
    "rows_scanned": 0,
    "asset_count": None,
    "warnings": [],
    "limitations": [],
}


class FakeApi:
    """Stands in for the job API's internal routes."""

    def __init__(self, scope: dict = SCOPE) -> None:
        self.scope = scope
        self.down = False
        self.scope_reads = 0
        self.posts: list[tuple[str, str, dict]] = []

    def get(self, job_id: str, route: str) -> dict:
        if self.down:
            raise OSError("connection refused")
        self.scope_reads += 1
        return self.scope

    def post(self, job_id: str, route: str, body: dict) -> None:
        if self.down:
            raise OSError("connection refused")
        self.posts.append((job_id, route, body))


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def hooks(api: FakeApi) -> Any:
    return plugin.ExecutionReceipts(REGISTRY, api)


def run_tool(hooks: Any, tool_id: str, args: dict, result: Any, *, job: str = JOB, call: str = "call_1", **post: Any):
    """Call one tool the way Hermes does: pre_tool_call, the MCP result envelope, post_tool_call, transform."""
    name = TOOLS[tool_id]["hermes_name"]
    directive = hooks.pre_tool_call(tool_name=name, args=args, session_id=job, tool_call_id=call)
    if directive and directive["action"] == "modify":
        args = {**args, **directive["args"]}
    # Hermes drops structuredContent when a text block repeats it, so the hooks see the text copy.
    envelope = json.dumps({"result": json.dumps(result, indent=2)}) if post.get("status", "ok") == "ok" else result
    common = {"tool_name": name, "args": args, "result": envelope, "session_id": job, "tool_call_id": call}
    post.setdefault("duration_ms", 12)
    hooks.post_tool_call(**common, turn_id="", **post)
    return hooks.transform_tool_result(**common)


def posted_receipt(api: FakeApi) -> dict:
    (job, route, receipt) = api.posts[-1]
    assert (job, route) == (receipt["jobId"], "tool-receipts")
    errors = [error.message for error in RECEIPT_SCHEMA.iter_errors(receipt)]
    assert not errors, errors
    return receipt


def snake_keys(value: Any, open_fields: set[str]) -> Any:
    """The reverse of the plugin's camelCase renaming: a receipt's content back to the tool's result."""
    if isinstance(value, dict):
        snake = {name: re.sub(r"[A-Z]", lambda m: "_" + m.group().lower(), name) for name in value}
        return {
            snake[name]: item if snake[name] in open_fields else snake_keys(item, open_fields)
            for name, item in value.items()
        }
    if isinstance(value, list):
        return [snake_keys(item, open_fields) for item in value]
    return value


def fixture_call(receipt: dict) -> tuple[str, dict, dict]:
    """The tool id, arguments and result that produce a fixture receipt."""
    tool_id = receipt["toolName"].rsplit("__", 1)[1]
    content = snake_keys(receipt["content"], plugin.OPEN_FIELDS[receipt["artifactKind"]])
    if receipt["artifactKind"] == "analytics_result":
        return tool_id, content.pop("public_parameters"), content
    if receipt["artifactKind"] == "structured_query":
        result = {key: content[key] for key in ("answer", "sql", "rows", "truncated", "resolution_lineage")}
        return tool_id, {"question": content["query"]}, {**result, "row_count": content["source_row_count"]}
    return tool_id, {}, content


@pytest.mark.parametrize("fixture", FIXTURE_RECEIPTS, ids=lambda r: f"{r['artifactKind']}-{r['status']}")
def test_fixture_tool_results_give_the_fixture_receipts(hooks, api, fixture):
    tool_id, args, result = fixture_call(fixture)
    call = fixture["invocationId"].removeprefix("hermes-tool:")

    output = run_tool(hooks, tool_id, args, result, job=fixture["jobId"], call=call, duration_ms=fixture["durationMs"])

    receipt = posted_receipt(api)
    unobserved = {"occurredAt", "traceId", "spanId"}  # the plugin's clock; Hermes gives hooks no trace context
    assert {k: v for k, v in receipt.items() if k not in unobserved} == {
        k: v for k, v in fixture.items() if k not in unobserved
    }
    if fixture["status"] == "completed":
        assert json.loads(output)["evidence_id"] == fixture["receiptId"]
    else:
        assert output is None


@pytest.mark.parametrize(
    "order", [("transform_tool_result", "post_tool_call"), ("post_tool_call", "transform_tool_result")]
)
def test_either_hook_order_posts_one_receipt_and_cites_it(hooks, api, order):
    """Hermes' agent loop fires transform_tool_result before post_tool_call; a direct dispatch, the reverse."""
    call = {
        "tool_name": TOOLS["price_context"]["hermes_name"],
        "args": {},
        "result": json.dumps({"result": json.dumps(PRICE_CONTEXT)}),
        "session_id": JOB,
        "tool_call_id": "call_1",
    }
    outputs = {hook: getattr(hooks, hook)(**call) for hook in order}

    (receipt,) = [body for _, route, body in api.posts if route == "tool-receipts"]
    cited = json.loads(outputs["transform_tool_result"])
    assert list(cited)[0] == "evidence_id"  # first, so a result cut for length still carries it
    assert cited["evidence_id"] == receipt["receiptId"]


def test_market_results_record_the_public_parameters(hooks, api):
    args = {"asset_ids": ["ALPH"], "frequency": "weekly", "point_limit": 3, "source_ids": ["model-supplied"]}
    output = run_tool(hooks, "price_context", args, PRICE_CONTEXT)

    receipt = posted_receipt(api)
    assert receipt["status"] == "completed"
    assert receipt["content"]["publicParameters"] == {"asset_ids": ["ALPH"], "frequency": "weekly", "point_limit": 3}
    assert receipt["content"]["payload"] == PRICE_CONTEXT["payload"]  # payload keys are kept as sent
    assert json.loads(output)["evidence_id"] == receipt["receiptId"]


def test_market_results_keep_what_the_receipt_card_shows(hooks, api):
    """The engine id, the asset count and the timing, to the microsecond, reach the receipt as the tool sent them."""
    run_tool(hooks, "price_context", {"asset_ids": ["ALPH"]}, PRICE_CONTEXT)

    content = posted_receipt(api)["content"]
    assert content["engine"]["engineId"] == "pandas-cpu.v1"
    assert content["assetCount"] == 1
    assert content["timing"] == {
        "computeMs": 8.152874994266313,
        "setupMs": 0.6416250066831708,
        "engineMs": 8.794500000949484,
        "totalMs": 9.425041987560689,
    }


def test_a_failed_market_result_gives_a_failed_receipt_without_evidence_id(hooks, api):
    output = run_tool(hooks, "price_context", {"asset_ids": ["NOPE"]}, REJECTED_PRICE_CONTEXT)

    receipt = posted_receipt(api)
    assert (receipt["status"], receipt["errorType"], receipt["errorSummary"]) == (
        "failed",
        "invalid_request",
        "unknown asset 'NOPE'",
    )
    assert receipt["content"]["error"] == REJECTED_PRICE_CONTEXT["error"]
    assert output is None


def test_an_mcp_error_gives_a_failed_receipt_without_content(hooks, api):
    error = json.dumps({"error": "source_ids must be a non-empty subset of ['market_news']"})
    output = run_tool(
        hooks,
        "retrieve_evidence",
        {"query": "outages"},
        error,
        status="error",
        error_type="tool_error",
        error_message="source_ids must be a non-empty subset of ['market_news']",
    )

    receipt = posted_receipt(api)
    assert (receipt["status"], receipt["errorType"], receipt["content"]) == ("failed", "tool_error", None)
    assert output is None


def test_results_are_fitted_to_the_display_limits(hooks, api):
    retrieval = next(r for r in FIXTURE_RECEIPTS if r["artifactKind"] == "retrieval_evidence")
    _, _, result = fixture_call(retrieval)
    hit = result["hits"][0]
    hit.update(snippet="x" * 2400, title="Exhibit\x0399.1\tRisk", published_at="2026-05-11T00:00:00")
    hit["metadata"].update(api_key="k", token_count=3)
    series = PRICE_CONTEXT["payload"]["series"] * 50
    prices = copy.deepcopy(PRICE_CONTEXT)
    prices["payload"].update(series=series, series_truncated=False)

    run_tool(hooks, "retrieve_evidence", {"query": "outages"}, result, call="call_1")
    fitted_hit = posted_receipt(api)["content"]["hits"][0]
    run_tool(hooks, "price_context", {"asset_ids": ["ALPH"]}, prices, call="call_2")
    payload = posted_receipt(api)["content"]["payload"]

    assert len(fitted_hit["snippet"]) == 1500
    assert fitted_hit["title"] == "Exhibit 99.1\tRisk"  # a control character from PDF extraction, but not the tab
    assert fitted_hit["publishedAt"] is None  # no timezone
    assert "api_key" not in fitted_hit["metadata"] and "token_count" not in fitted_hit["metadata"]
    assert fitted_hit["metadata"]["citation"] == hit["metadata"]["citation"]
    assert (len(payload["series"]), payload["series_truncated"]) == (100, True)


def test_sql_rows_are_cut_to_25_rows_of_40_columns(hooks, api):
    rows = [{"token_count": 1, **{f"column_{c}": c for c in range(45)}} for _ in range(30)]
    result = {"answer": "30 rows", "sql": "SELECT 1", "rows": rows, "row_count": 30, "resolution_lineage": []}

    run_tool(hooks, "ask_question", {"question": "Which assets?"}, result)

    content = posted_receipt(api)["content"]
    assert (len(content["rows"]), content["sourceRowCount"], content["truncated"]) == (25, 30, True)
    assert len(content["rows"][0]) == 39  # 40 columns, less the banned token_count
    assert content["databaseName"] == "market_analysis"


def test_a_result_too_long_to_read_whole_is_shortened(hooks, api):
    """Hermes hides an MCP result over 50,000 characters behind a preview; Auto Ontology can return 100 wide rows."""
    rows = [{f"column_{c}": f"value {r}-{c} " * 3 for c in range(12)} for r in range(100)]
    reasoning = "Resolved the question to daily_prices. " * 400
    result = {"answer": "100 rows", "sql": "SELECT 1", "rows": rows, "row_count": 340, "truncated": True}
    result |= {"reasoning": reasoning, "resolution_lineage": []}

    output = run_tool(hooks, "ask_question", {"question": "Which assets?"}, result)

    assert len(output) <= plugin.MAX_RESULT_CHARS < 50_000
    read = json.loads(output)
    assert list(read)[0] == "evidence_id" and read["evidence_id"] == posted_receipt(api)["receiptId"]
    shortened = json.loads(read["result"])
    assert 1 <= len(shortened["rows"]) < 100 and shortened["rows"] == rows[: len(shortened["rows"])]
    assert shortened["truncated"] is True and shortened["row_count"] == 340
    assert read["shortened_to_fit"].startswith(f"result.rows lists the first {len(shortened['rows'])} of 100 items")
    assert len(posted_receipt(api)["content"]["rows"]) == 25, "the receipt is built from the whole result"


def test_a_long_text_result_is_cut(hooks, api):
    output = hooks.transform_tool_result(
        tool_name=TOOLS["ask_question"]["hermes_name"],
        args={"question": "x"},
        result=json.dumps({"result": "plain text " * 10_000}),
        session_id=JOB,
        tool_call_id="call_9",
    )

    assert len(output) <= plugin.MAX_RESULT_CHARS
    assert json.loads(output)["result"].endswith("…")


def test_a_second_copy_of_a_long_result_is_left_out(hooks, api):
    rows = [{"value": "x" * 100} for _ in range(400)]
    envelope = {"result": json.dumps({"rows": rows[:5]}), "structuredContent": {"rows": rows}}

    output = hooks.transform_tool_result(
        tool_name=TOOLS["ask_question"]["hermes_name"],
        args={"question": "x"},
        result=json.dumps(envelope),
        session_id=JOB,
        tool_call_id="call_8",
    )

    read = json.loads(output)
    assert len(output) <= plugin.MAX_RESULT_CHARS and "structuredContent" not in read
    assert json.loads(read["result"]) == {"rows": rows[:5]}


def test_a_result_that_fits_is_unchanged_but_for_its_evidence_id(hooks, api):
    output = json.loads(run_tool(hooks, "price_context", {}, PRICE_CONTEXT))

    assert output.keys() == {"evidence_id", "result"}
    assert json.loads(output["result"]) == PRICE_CONTEXT


def test_the_lineage_keeps_only_complete_bindings(hooks, api):
    binding = {"phrase": "closing price", "ontology_object": "Close", "table": "main.daily_prices", "column": "close"}
    # Auto Ontology leaves out a table or column it could not resolve; an empty one must not fail the receipt either.
    lineage = [binding, {**binding, "column": ""}, {"phrase": "sector", "ontology_object": "Sector"}]
    result = {"answer": "ALPH", "sql": "SELECT 1", "rows": [], "row_count": 0, "resolution_lineage": lineage}

    run_tool(hooks, "ask_question", {"question": "Which asset closed highest?"}, result)

    assert posted_receipt(api)["content"]["resolutionLineage"] == [
        {"phrase": "closing price", "ontologyObject": "Close", "table": "main.daily_prices", "column": "close"}
    ]


def test_a_whole_question_as_the_lineage_phrase_is_cut(hooks, api):
    # Auto Ontology can bind a long question as one phrase; the receipt allows 500 characters.
    binding = {"phrase": "q" * 600, "ontology_object": "News", "table": "main.company_news", "column": "headline"}
    result = {"answer": "ALPH", "sql": "SELECT 1", "rows": [], "row_count": 0, "resolution_lineage": [binding]}

    run_tool(hooks, "ask_question", {"question": "Which issuers had negative news?"}, result)

    assert len(posted_receipt(api)["content"]["resolutionLineage"][0]["phrase"]) == 500


def test_each_tool_gets_the_sources_its_family_allows(hooks, api):
    for tool in REGISTRY["tools"]:
        directive = hooks.pre_tool_call(tool_name=tool["hermes_name"], args={"source_ids": ["x"]}, session_id=JOB)
        if tool["id"] == "retrieve_evidence":
            assert directive == {"action": "modify", "args": {"source_ids": DOCUMENTS}}
        elif tool["server"] == "market_analytics":
            assert directive == {"action": "modify", "args": {"source_ids": [STRUCTURED]}}
        else:
            assert directive is None  # ask_question serves only the pack's database and takes no scope argument
    assert api.scope_reads == 1


def test_ask_question_keeps_only_the_question(hooks):
    args = {
        "question": "Which asset closed highest?",
        "conversation_id": ",",
        "target_db": "other",
        "prediction": "p",
        "evidence": None,
        "source_ids": ["x"],
    }
    directive = hooks.pre_tool_call(tool_name=TOOLS["ask_question"]["hermes_name"], args=args, session_id=JOB)

    # Hermes dispatches this same dict; a modify directive could only add keys, never remove one.
    assert directive is None
    assert args == {"question": "Which asset closed highest?"}


def test_other_tools_keep_their_arguments(hooks):
    args = {"asset_ids": ["asset-alpha"], "evidence": "kept"}
    hooks.pre_tool_call(tool_name=TOOLS["price_context"]["hermes_name"], args=args, session_id=JOB)
    assert args == {"asset_ids": ["asset-alpha"], "evidence": "kept"}


@pytest.mark.parametrize(
    ("tool_name", "scope", "api_down"),
    [
        ("skill_manage", SCOPE, False),
        ("mcp__retrieval__delete_collection", SCOPE, False),
        ("mcp__market_analytics__market_scan", {**SCOPE, "sources": SCOPE["sources"][:2]}, False),
        (
            "mcp__auto_ontology__ask_question",
            {**SCOPE, "sources": [{"id": STRUCTURED, "capabilities": ["market_analytics"]}]},
            False,
        ),
        ("mcp__retrieval__retrieve_evidence", SCOPE, True),
        ("mcp__retrieval__retrieve_evidence", {"unexpected": "shape"}, False),
    ],
    ids=["skill_manage", "unregistered", "no-allowed-source", "no-structured-retrieval", "api-down", "bad-scope"],
)
def test_calls_are_blocked(tool_name, scope, api_down):
    api = FakeApi(scope)
    api.down = api_down
    directive = plugin.ExecutionReceipts(REGISTRY, api).pre_tool_call(tool_name=tool_name, args={}, session_id=JOB)
    assert directive["action"] == "block" and directive["message"]


def test_other_hermes_tools_pass_through(hooks):
    assert hooks.pre_tool_call(tool_name="skill_view", args={"name": "x"}, session_id=JOB) is None


def test_no_evidence_id_when_the_receipt_was_not_stored(hooks, api):
    hooks.pre_tool_call(tool_name=TOOLS["price_context"]["hermes_name"], args={}, session_id=JOB)
    api.down = True
    assert run_tool(hooks, "price_context", {}, PRICE_CONTEXT) is None


def test_model_calls_report_the_served_model_and_tier(hooks, api):
    usage = {"input_tokens": 120, "cache_read_tokens": 9000, "prompt_tokens": 9120, "output_tokens": 412}
    for request_id, model in (("req-1", EFFICIENT), ("req-2", CAPABLE)):
        hooks.post_api_request(
            session_id=JOB,
            api_request_id=request_id,
            turn_id="turn-1",
            model="market-research",
            response_model=model,
            usage=usage,
            started_at=1790571611.5,
            ended_at=1790571612.0,
        )

    assert [(route, body["served_model"], body["tier"]) for _, route, body in api.posts] == [
        ("llm-calls", EFFICIENT, "efficient"),
        ("llm-calls", CAPABLE, "capable"),
    ]
    assert api.posts[0][2] == {
        "api_request_id": "req-1",
        "turn_id": "turn-1",
        "served_model": EFFICIENT,
        "tier": "efficient",
        "input_tokens": 9120,
        "output_tokens": 412,
        "started_at": "2026-09-28T05:00:11.500000Z",
        "completed_at": "2026-09-28T05:00:12Z",
    }


def test_the_job_api_client_sends_the_receipt_key():
    seen: list[tuple[str, str, str | None, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._answer(b'{"sources": []}')

        def do_POST(self):
            self._answer(b"")

        def _answer(self, body: bytes) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            seen.append((self.command, self.path, self.headers["X-Receipt-Key"], self.rfile.read(length)))
            self.send_response(200 if body else 204)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        api = plugin.JobApi(f"http://127.0.0.1:{server.server_port}/", "placeholder-key")
        assert api.get("job 1", "execution-scope") == {"sources": []}
        api.post("job 1", "tool-receipts", {"receiptId": "r"})
    finally:
        server.shutdown()

    assert seen == [
        ("GET", "/internal/hermes/jobs/job%201/execution-scope", "placeholder-key", b""),
        ("POST", "/internal/hermes/jobs/job%201/tool-receipts", "placeholder-key", b'{"receiptId": "r"}'),
    ]


def test_the_job_api_client_retries_until_the_job_has_recorded_its_run(monkeypatch):
    answers = [503, 503, 204]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(answers.pop(0))
            self.send_header("Retry-After", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        plugin.JobApi(f"http://127.0.0.1:{server.server_port}", "key").post("job-1", "tool-receipts", {})
    finally:
        server.shutdown()
    assert answers == []


@pytest.mark.parametrize(
    ("tool_call_id", "expected"),
    [("call_1", "hermes-tool:call_1"), ("call 1", None), ("x" * 300, None)],
)
def test_the_receipt_names_the_tool_node_the_job_api_derives(tool_call_id, expected):
    derived = plugin.invocation_id(tool_call_id)
    assert derived == expected if expected else re.fullmatch(r"hermes-tool:tool-call-[0-9a-f]{32}", derived)


def test_hermes_enables_the_plugin_and_gets_its_four_hooks(monkeypatch, config):
    manifest = load_yaml(PLUGIN_DIR / "plugin.yaml")
    assert manifest["name"] in config["plugins"]["enabled"]

    registered: dict[str, Any] = {}

    class Context:
        def register_hook(self, name, callback):
            registered[name] = callback

    monkeypatch.setattr(plugin, "REGISTRY_PATH", CONTRACTS / "tool-registry.json")
    plugin.register(Context())
    assert sorted(registered) == sorted(manifest["provides_hooks"])
    assert set(plugin.CONTENT) == {tool["receipt_kind"] for tool in REGISTRY["tools"]}
