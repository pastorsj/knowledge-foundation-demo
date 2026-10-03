# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The execution-receipts plugin, driven the way Hermes calls its hooks.

Tool results come from the contract fixtures (recorded runs), rebuilt in the shape each tool returns them:
``query_tables`` and ``predict`` results carry fields their receipts leave out. Every receipt is checked against
contracts/schemas/receipt.schema.json.
"""

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
DOCUMENTS = ["retail.policies", "workspace.documents"]
STRUCTURED = "retail.sales"  # tables and predictions
UPLOADED_TABLES = "workspace.tables"  # tables only
EFFICIENT = "nvidia/nemotron-3-ultra-550b-a55b"
CAPABLE = "gpt-6-sol"
SCOPE = {
    "job_id": JOB,
    "sources": [
        {"id": "retail.policies", "capabilities": ["unstructured_retrieval"]},
        {"id": "workspace.documents", "capabilities": ["unstructured_retrieval"]},
        {"id": STRUCTURED, "capabilities": ["structured_retrieval", "structured_prediction"]},
        {"id": UPLOADED_TABLES, "capabilities": ["structured_retrieval"]},
    ],
    "database_name": "retail_sales",
    "collection": "knowledge",
    "models": {"efficient": EFFICIENT, "capable": CAPABLE},
}

# A query_tables result over two structured sources, as tools/tables returns it.
QUERY = {
    "question": "Orders and net revenue by store region",
    "sql": "SELECT s.region, count(*) AS orders, round(sum(o.net_amount), 2) AS net_revenue\n"
    "FROM retail_sales.orders AS o JOIN workspace_tables.stores AS s USING (store_id)\n"
    "GROUP BY s.region ORDER BY net_revenue DESC",
    "database_name": "knowledge",
    "databases": [
        {"source_id": STRUCTURED, "alias": "retail_sales"},
        {"source_id": UPLOADED_TABLES, "alias": "workspace_tables"},
    ],
    "columns": ["region", "orders", "net_revenue"],
    "rows": [
        {"region": "West", "orders": 3, "net_revenue": 395.0},
        {"region": "East", "orders": 2, "net_revenue": 182.5},
    ],
    "row_count": 2,
    "truncated": False,
    "elapsed_ms": 41.7,
    "warnings": [],
}
QUERY_ARGS = {"question": QUERY["question"], "sql": QUERY["sql"]}

# A predict result, as tools/prediction returns it.
PREDICTION = {
    "available": True,
    "reason": None,
    "source_id": STRUCTURED,
    "template_id": None,
    "pql": "PREDICT SUM(orders.net_amount, 0, 30, days) FOR EACH customers.customer_id",
    "task_type": "regression",
    "anchor_time": "2026-09-30T00:00:00Z",
    "horizon": {"value": 30, "unit": "days"},
    "entity_table": "customers",
    "rows": [
        {"entity_id": "C1", "probability": None, "value": 212.4, "label": None},
        {"entity_id": "C3", "probability": None, "value": 75.0, "label": None},
    ],
    "model": "kumo-relational",
    "elapsed_ms": 38120.4,
    "warnings": ["Scored the first 100 of 240 customers."],
}
UNAVAILABLE = {
    **PREDICTION,
    "available": False,
    "reason": "No Kumo endpoint is configured (KUMO_RELATIONAL_URL).",
    "task_type": None,
    "anchor_time": None,
    "rows": [],
    "elapsed_ms": 0.4,
    "warnings": [],
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
    """The tool id, arguments and result, in the tool's own shape, that produce a fixture receipt."""
    tool_id = receipt["toolName"].rsplit("__", 1)[1]
    content = snake_keys(receipt["content"], plugin.OPEN_FIELDS[receipt["artifactKind"]])
    if tool_id == "query_tables":
        rows = content["rows"]
        result = {
            "question": content["query"],
            "sql": content["sql"],
            "database_name": content["database_name"],
            "databases": [{"source_id": STRUCTURED, "alias": content["database_name"]}],
            "columns": list(rows[0]) if rows else [],
            "rows": rows,
            "row_count": content["source_row_count"],
            "truncated": content["truncated"],
            "elapsed_ms": 12.5,
            "warnings": [],
        }
        return tool_id, {"question": content["query"], "sql": content["sql"]}, result
    if tool_id == "ask_question":
        result = {key: content[key] for key in ("answer", "sql", "rows", "truncated", "resolution_lineage")}
        return tool_id, {"question": content["query"]}, {**result, "row_count": content["source_row_count"]}
    if tool_id == "predict":
        pql = f"template:{content['template_id']}" if content["template_id"] else content["pql"]
        return tool_id, {"question": "Who is likely?", "pql": pql}, {**content, "elapsed_ms": 40.1, "warnings": []}
    return tool_id, {"query": content["query"]}, content


@pytest.mark.parametrize("fixture", FIXTURE_RECEIPTS, ids=lambda r: f"{r['artifactKind']}-{r['status']}")
def test_fixture_tool_results_give_the_fixture_receipts(hooks, api, fixture):
    tool_id, args, result = fixture_call(fixture)
    call = fixture["invocationId"].removeprefix("hermes-tool:")

    output = run_tool(hooks, tool_id, args, result, job=fixture["jobId"], call=call, duration_ms=fixture["durationMs"])

    receipt = posted_receipt(api)
    # occurredAt is the plugin's clock and Hermes gives hooks no trace context. The receipt id is checked against its
    # derivation here, and against the fixture's in test_fixture_receipt_ids_are_the_plugin_evidence_ids.
    unobserved = {"occurredAt", "traceId", "spanId", "receiptId"}
    assert {k: v for k, v in receipt.items() if k not in unobserved} == {
        k: v for k, v in fixture.items() if k not in unobserved
    }
    assert receipt["receiptId"] == plugin.receipt_id(fixture["jobId"], call)
    if fixture["status"] == "completed":
        assert json.loads(output)["evidence_id"] == receipt["receiptId"]
    else:
        assert output is None


@pytest.mark.xfail(
    strict=True,
    reason="contracts/fixtures/receipts.json: five receiptIds are not sha256(jobId, tool call id) since the E2 rewrite",
)
def test_fixture_receipt_ids_are_the_plugin_evidence_ids():
    """A fixture receipt's id is the evidence id the plugin derives for its job and tool call."""
    derived = [
        plugin.receipt_id(fixture["jobId"], fixture["invocationId"].removeprefix("hermes-tool:"))
        for fixture in FIXTURE_RECEIPTS
    ]
    assert derived == [fixture["receiptId"] for fixture in FIXTURE_RECEIPTS]


@pytest.mark.parametrize(
    "order", [("transform_tool_result", "post_tool_call"), ("post_tool_call", "transform_tool_result")]
)
def test_either_hook_order_posts_one_receipt_and_cites_it(hooks, api, order):
    """Hermes' agent loop fires transform_tool_result before post_tool_call; a direct dispatch, the reverse."""
    call = {
        "tool_name": TOOLS["query_tables"]["hermes_name"],
        "args": QUERY_ARGS,
        "result": json.dumps({"result": json.dumps(QUERY)}),
        "session_id": JOB,
        "tool_call_id": "call_1",
    }
    outputs = {hook: getattr(hooks, hook)(**call) for hook in order}

    (receipt,) = [body for _, route, body in api.posts if route == "tool-receipts"]
    cited = json.loads(outputs["transform_tool_result"])
    assert list(cited)[0] == "evidence_id"  # first, so a result cut for length still carries it
    assert cited["evidence_id"] == receipt["receiptId"]


def test_a_table_query_records_its_question_and_the_database_it_ran_on(hooks, api):
    output = run_tool(hooks, "query_tables", {**QUERY_ARGS, "source_ids": ["model-supplied"]}, QUERY)

    receipt = posted_receipt(api)
    assert receipt["status"] == "completed"
    assert receipt["content"] == {
        "query": QUERY["question"],
        "databaseName": "knowledge",  # two sources attached; the scope's database_name is retail_sales
        "answer": None,
        "sql": QUERY["sql"],
        "rows": QUERY["rows"],  # row keys are column names, kept as sent
        "sourceRowCount": 2,
        "truncated": False,
        "resolutionLineage": [],
    }
    assert json.loads(output)["evidence_id"] == receipt["receiptId"]


def test_a_table_query_without_a_question_is_recorded_by_its_sql(hooks, api):
    run_tool(hooks, "query_tables", {"sql": QUERY["sql"]}, {**QUERY, "database_name": None})

    content = posted_receipt(api)["content"]
    assert (content["query"], content["databaseName"]) == (QUERY["sql"], "retail_sales")


def test_a_prediction_receipt_keeps_the_result_but_its_timing_and_warnings(hooks, api):
    output = run_tool(hooks, "predict", {"question": "Spend next month?", "pql": PREDICTION["pql"]}, PREDICTION)

    receipt = posted_receipt(api)
    assert (receipt["status"], receipt["errorType"]) == ("completed", None)
    assert receipt["content"] == {
        "available": True,
        "reason": None,
        "sourceId": STRUCTURED,
        "templateId": None,
        "pql": PREDICTION["pql"],
        "taskType": "regression",
        "anchorTime": "2026-09-30T00:00:00Z",
        "horizon": {"value": 30, "unit": "days"},
        "entityTable": "customers",
        "rows": [
            {"entityId": "C1", "probability": None, "value": 212.4, "label": None},
            {"entityId": "C3", "probability": None, "value": 75.0, "label": None},
        ],
        "model": "kumo-relational",
    }
    # The agent still reads the whole result, warnings included.
    assert json.loads(json.loads(output)["result"])["warnings"] == PREDICTION["warnings"]


def test_prediction_entity_ids_of_any_key_type_are_recorded_as_text(hooks, api):
    rows = [{"entity_id": 1042, "probability": 0.62, "value": None, "label": None}]
    run_tool(hooks, "predict", {"pql": "template:churn_90d"}, {**PREDICTION, "rows": rows})

    assert posted_receipt(api)["content"]["rows"][0]["entityId"] == "1042"


def test_an_unavailable_prediction_gives_a_failed_receipt_without_evidence_id(hooks, api):
    output = run_tool(hooks, "predict", {"question": "Spend next month?", "pql": PREDICTION["pql"]}, UNAVAILABLE)

    receipt = posted_receipt(api)
    assert (receipt["status"], receipt["errorType"], receipt["errorSummary"]) == (
        "failed",
        "evidence_unavailable",
        "No Kumo endpoint is configured (KUMO_RELATIONAL_URL).",
    )
    assert (receipt["content"]["available"], receipt["content"]["rows"]) == (False, [])
    assert output is None


def test_a_retrieval_without_a_reranker_names_no_rerank_model(hooks, api):
    retrieval = next(r for r in FIXTURE_RECEIPTS if r["artifactKind"] == "retrieval_evidence")
    _, args, result = fixture_call(retrieval)
    result["models"] = {"embed": result["models"]["embed"], "rerank": ""}

    run_tool(hooks, "retrieve_evidence", args, result)

    assert posted_receipt(api)["content"]["models"] == {"embed": result["models"]["embed"], "rerank": None}


def test_an_mcp_error_gives_a_failed_receipt_without_content(hooks, api):
    error = json.dumps({"error": "source_ids must be a non-empty subset of ['retail.policies']"})
    output = run_tool(
        hooks,
        "retrieve_evidence",
        {"query": "outages"},
        error,
        status="error",
        error_type="tool_error",
        error_message="source_ids must be a non-empty subset of ['retail.policies']",
    )

    receipt = posted_receipt(api)
    assert (receipt["status"], receipt["errorType"], receipt["content"]) == ("failed", "tool_error", None)
    assert output is None


def test_results_are_fitted_to_the_display_limits(hooks, api):
    retrieval = next(r for r in FIXTURE_RECEIPTS if r["artifactKind"] == "retrieval_evidence")
    _, _, result = fixture_call(retrieval)
    hit = result["hits"][0]
    hit.update(snippet="x" * 2400, title="Return\x03Policy\tRetail", published_at="2026-05-11T00:00:00")
    hit["metadata"].update(api_key="k", token_count=3)
    rows = [{"entity_id": f"C{i}", "probability": 0.5, "value": None, "label": "x" * 300} for i in range(120)]
    prediction = {**PREDICTION, "pql": "PREDICT " + "x" * 9000, "rows": rows}
    unavailable = {**UNAVAILABLE, "reason": "Kumo said: " + "y" * 900}

    run_tool(hooks, "retrieve_evidence", {"query": "returns"}, result, call="call_1")
    fitted_hit = posted_receipt(api)["content"]["hits"][0]
    run_tool(hooks, "predict", {"pql": "PREDICT"}, prediction, call="call_2")
    fitted_prediction = posted_receipt(api)["content"]
    run_tool(hooks, "predict", {"pql": "PREDICT"}, unavailable, call="call_3")
    failed = posted_receipt(api)

    assert len(fitted_hit["snippet"]) == 1500
    assert fitted_hit["title"] == "Return Policy\tRetail"  # a control character from PDF extraction, but not the tab
    assert fitted_hit["publishedAt"] is None  # no timezone
    assert "api_key" not in fitted_hit["metadata"] and "token_count" not in fitted_hit["metadata"]
    assert fitted_hit["metadata"]["citation"] == hit["metadata"]["citation"]
    assert len(fitted_prediction["pql"]) == 8000
    assert len(fitted_prediction["rows"]) == 100 and len(fitted_prediction["rows"][0]["label"]) == 256
    assert (len(failed["content"]["reason"]), len(failed["errorSummary"])) == (500, 600)


def test_sql_rows_are_cut_to_25_rows_of_40_columns(hooks, api):
    rows = [{"token_count": 1, **{f"column_{c}": c for c in range(45)}} for _ in range(30)]
    result = {"answer": "30 rows", "sql": "SELECT 1", "rows": rows, "row_count": 30, "resolution_lineage": []}

    run_tool(hooks, "ask_question", {"question": "Which customers?"}, result)

    content = posted_receipt(api)["content"]
    assert (len(content["rows"]), content["sourceRowCount"], content["truncated"]) == (25, 30, True)
    assert len(content["rows"][0]) == 39  # 40 columns, less the banned token_count
    assert content["databaseName"] == "retail_sales"  # Auto Ontology's database, from the job's scope


def test_a_result_too_long_to_read_whole_is_shortened(hooks, api):
    """Hermes hides an MCP result over 50,000 characters behind a preview; Auto Ontology can return 100 wide rows."""
    rows = [{f"column_{c}": f"value {r}-{c} " * 3 for c in range(12)} for r in range(100)]
    reasoning = "Resolved the question to orders. " * 400
    result = {"answer": "100 rows", "sql": "SELECT 1", "rows": rows, "row_count": 340, "truncated": True}
    result |= {"reasoning": reasoning, "resolution_lineage": []}

    output = run_tool(hooks, "ask_question", {"question": "Which customers?"}, result)

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
    output = json.loads(run_tool(hooks, "query_tables", QUERY_ARGS, QUERY))

    assert output.keys() == {"evidence_id", "result"}
    assert json.loads(output["result"]) == QUERY


def test_the_lineage_keeps_only_complete_bindings(hooks, api):
    binding = {"phrase": "net revenue", "ontology_object": "Net Amount", "table": "main.orders", "column": "net_amount"}
    # Auto Ontology leaves out a table or column it could not resolve; an empty one must not fail the receipt either.
    lineage = [binding, {**binding, "column": ""}, {"phrase": "region", "ontology_object": "Region"}]
    result = {"answer": "S-12", "sql": "SELECT 1", "rows": [], "row_count": 0, "resolution_lineage": lineage}

    run_tool(hooks, "ask_question", {"question": "Which store sold the most?"}, result)

    assert posted_receipt(api)["content"]["resolutionLineage"] == [
        {"phrase": "net revenue", "ontologyObject": "Net Amount", "table": "main.orders", "column": "net_amount"}
    ]


def test_a_whole_question_as_the_lineage_phrase_is_cut(hooks, api):
    # Auto Ontology can bind a long question as one phrase; the receipt allows 500 characters.
    binding = {"phrase": "q" * 600, "ontology_object": "Return", "table": "main.returns", "column": "reason"}
    result = {"answer": "S-12", "sql": "SELECT 1", "rows": [], "row_count": 0, "resolution_lineage": [binding]}

    run_tool(hooks, "ask_question", {"question": "Which customers returned damaged items?"}, result)

    assert len(posted_receipt(api)["content"]["resolutionLineage"][0]["phrase"]) == 500


def test_each_tool_gets_the_sources_its_family_allows(hooks, api):
    for tool in REGISTRY["tools"]:
        directive = hooks.pre_tool_call(tool_name=tool["hermes_name"], args={"source_ids": ["x"]}, session_id=JOB)
        if tool["id"] == "retrieve_evidence":
            assert directive == {"action": "modify", "args": {"source_ids": DOCUMENTS}}
        elif tool["id"] == "query_tables":
            assert directive == {"action": "modify", "args": {"source_ids": [STRUCTURED, UPLOADED_TABLES]}}
        elif tool["id"] == "predict":
            assert directive == {"action": "modify", "args": {"source_ids": [STRUCTURED]}}
        else:
            assert directive is None  # ask_question serves only its own database and takes no scope argument
    assert api.scope_reads == 1


def test_ask_question_keeps_only_the_question(hooks):
    args = {
        "question": "Which store sold the most?",
        "conversation_id": ",",
        "target_db": "other",
        "prediction": "p",
        "evidence": None,
        "source_ids": ["x"],
    }
    directive = hooks.pre_tool_call(tool_name=TOOLS["ask_question"]["hermes_name"], args=args, session_id=JOB)

    # Hermes dispatches this same dict; a modify directive could only add keys, never remove one.
    assert directive is None
    assert args == {"question": "Which store sold the most?"}


def test_other_tools_keep_their_arguments(hooks):
    args = {**QUERY_ARGS, "evidence": "kept"}
    hooks.pre_tool_call(tool_name=TOOLS["query_tables"]["hermes_name"], args=args, session_id=JOB)
    assert args == {**QUERY_ARGS, "evidence": "kept"}


@pytest.mark.parametrize(
    ("tool_name", "scope", "api_down"),
    [
        ("skill_manage", SCOPE, False),
        ("mcp__retrieval__delete_collection", SCOPE, False),
        ("mcp__market_analytics__market_scan", SCOPE, False),
        ("mcp__tables__query_tables", {**SCOPE, "sources": SCOPE["sources"][:2]}, False),
        ("mcp__prediction__predict", {**SCOPE, "sources": [SCOPE["sources"][0], SCOPE["sources"][3]]}, False),
        (
            "mcp__auto_ontology__ask_question",
            {**SCOPE, "sources": [{"id": STRUCTURED, "capabilities": ["structured_prediction"]}]},
            False,
        ),
        ("mcp__retrieval__retrieve_evidence", SCOPE, True),
        ("mcp__retrieval__retrieve_evidence", {"unexpected": "shape"}, False),
    ],
    ids=[
        "skill_manage",
        "unregistered",
        "retired-market-tool",
        "no-table-source",
        "no-prediction-source",
        "no-structured-retrieval",
        "api-down",
        "bad-scope",
    ],
)
def test_calls_are_blocked(tool_name, scope, api_down):
    api = FakeApi(scope)
    api.down = api_down
    directive = plugin.ExecutionReceipts(REGISTRY, api).pre_tool_call(tool_name=tool_name, args={}, session_id=JOB)
    assert directive["action"] == "block" and directive["message"]


def test_other_hermes_tools_pass_through(hooks):
    assert hooks.pre_tool_call(tool_name="skill_view", args={"name": "x"}, session_id=JOB) is None


def test_no_evidence_id_when_the_receipt_was_not_stored(hooks, api):
    hooks.pre_tool_call(tool_name=TOOLS["query_tables"]["hermes_name"], args=QUERY_ARGS, session_id=JOB)
    api.down = True
    assert run_tool(hooks, "query_tables", QUERY_ARGS, QUERY) is None


def test_model_calls_report_the_served_model_and_tier(hooks, api):
    usage = {"input_tokens": 120, "cache_read_tokens": 9000, "prompt_tokens": 9120, "output_tokens": 412}
    for request_id, model in (("req-1", EFFICIENT), ("req-2", CAPABLE)):
        hooks.post_api_request(
            session_id=JOB,
            api_request_id=request_id,
            turn_id="turn-1",
            model="knowledge",
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
