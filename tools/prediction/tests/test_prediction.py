# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""predict around a stubbed Kumo client (ported from the market demo's predictor), plus one live call if configured."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from conftest import ACCOUNTS
from conftest import SALES
from conftest import add_copy_of_sales
from conftest import fixture_source
from conftest import write_source
from kumo_relational_client import NimRequestError
from kumo_relational_client import RelationalError
from mcp.client import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from sqlglot import exp

from demo_prediction import catalog
from demo_prediction import entities
from demo_prediction import prediction
from demo_prediction.prediction import EVIDENCE_ID
from demo_prediction.prediction import Predictor
from demo_prediction.prediction import build_graph
from demo_prediction.server import create_server
from demo_prediction.settings import Settings

pytestmark = pytest.mark.anyio

KUMO_URL = "http://kumo-relational:8000"
CHURN = "PREDICT COUNT(orders.*, 0, 90, days) = 0 FOR EACH customers.customer_id"
TEMPLATE = fixture_source(SALES)["prediction"]["templates"][0]


class StubClient:
    """Records what predict sends and answers like RelationalClient for a binary query, or with `answer`."""

    calls: list[dict[str, Any]] = []
    error: Exception | None = None
    answer: Callable[[list[Any]], pd.DataFrame] | None = None

    def __init__(self, url: str, api_key: str | None = None, **options: Any) -> None:
        self.call = {"url": url, "api_key": api_key, **options}

    def __enter__(self) -> StubClient:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def relational(self, graph: Any) -> StubClient:
        self.call["graph"] = graph
        return self

    def predict(self, query: str, indices: list[Any], **options: Any) -> pd.DataFrame:
        self.calls.append({**self.call, "query": query, "indices": indices, **options})
        if self.error is not None:
            raise self.error
        if (answer := type(self).answer) is not None:  # read from the class, so it is not bound as a method
            return answer(indices)
        probabilities = [0.25 + 0.25 * position for position in range(len(indices))]
        return pd.DataFrame(
            {
                "ENTITY": indices,
                "ANCHOR_TIMESTAMP": pd.Timestamp("2026-09-30"),
                "PREDICTION": [p > 0.5 for p in probabilities],
                "FALSE_PROB": [1 - p for p in probabilities],
                "TRUE_PROB": probabilities,
            }
        )


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> type[StubClient]:
    monkeypatch.setattr(StubClient, "calls", [])
    monkeypatch.setattr(StubClient, "error", None)
    monkeypatch.setattr(StubClient, "answer", None)
    monkeypatch.setattr(prediction, "RelationalClient", StubClient)
    return StubClient


def server(knowledge_dir: Path, url: str | None = KUMO_URL) -> MCPServer:
    return create_server(Settings(knowledge_dir=knowledge_dir, kumo_url=url, kumo_api_key="dummy-key"))


async def predict(
    knowledge_dir: Path, pql: str, source_ids: list[str] | None = None, url: str | None = KUMO_URL, **arguments: Any
) -> CallToolResult:
    arguments = {
        "question": "Which customers will stop ordering?",
        "pql": pql,
        "source_ids": [SALES] if source_ids is None else source_ids,
        **arguments,
    }
    async with Client(server(knowledge_dir, url)) as client:
        return await client.call_tool("predict", arguments)


def without_timing(content: dict[str, Any]) -> dict[str, Any]:
    assert content["elapsed_ms"] >= 0
    return {key: value for key, value in content.items() if key != "elapsed_ms"}


def row(entity_id: str, probability: float | None = None, value: float | None = None, label: str | None = None):
    return {"entity_id": entity_id, "probability": probability, "value": value, "label": label}


async def test_a_binary_prediction_returns_sorted_probabilities(knowledge_dir: Path, stub: type[StubClient]):
    result = await predict(knowledge_dir, CHURN)

    assert not result.is_error, result.content[0].text
    assert without_timing(result.structured_content) == {
        "available": True,
        "reason": None,
        "source_id": SALES,
        "template_id": None,
        "pql": CHURN,
        "task_type": "binary_classification",
        "anchor_time": None,
        "horizon": {"value": 90, "unit": "days"},
        "entity_table": "customers",
        "rows": [row("C3", 0.75), row("C2", 0.5), row("C1", 0.25)],
        "model": "kumo-relational",
        "warnings": [],
    }
    (call,) = stub.calls
    assert (call["url"], call["api_key"], call["timeout"]) == (KUMO_URL, "dummy-key", 60)
    assert (call["max_retries"], call["num_retries"]) == (0, 0)  # one attempt, so the call ends within its timeout
    assert (call["query"], call["indices"], call["run_mode"]) == (CHURN, ["C1", "C2", "C3"], "fast")
    assert call["anchor_time"] is None  # the engine then anchors at the data's latest timestamp
    graph = call["graph"]
    assert sorted((edge.src_table, edge.fkey, edge.dst_table) for edge in graph.edges) == [
        ("orders", "customer_id", "customers")
    ]


async def test_a_template_expands_to_its_pql_and_anchor(knowledge_dir: Path, stub: type[StubClient]):
    result = await predict(knowledge_dir, "template:churn_90d")

    content = result.structured_content
    assert (content["template_id"], content["pql"]) == ("churn_90d", TEMPLATE["pql"])
    assert content["anchor_time"] == "2026-09-30T00:00:00Z"
    assert content["horizon"] == {"value": 90, "unit": "days"}
    (call,) = stub.calls
    assert call["query"] == TEMPLATE["pql"]
    assert call["anchor_time"] == pd.Timestamp("2026-09-30 00:00:00")  # UTC, as the data's naive timestamps


async def test_an_explicit_anchor_time_wins_over_the_template_anchor(knowledge_dir: Path, stub: type[StubClient]):
    result = await predict(knowledge_dir, "template:churn_90d", anchor_time="2026-08-01T12:00:00+02:00")

    assert result.structured_content["anchor_time"] == "2026-08-01T10:00:00Z"
    assert stub.calls[0]["anchor_time"] == pd.Timestamp("2026-08-01 10:00:00")


def test_the_graph_has_the_catalog_keys_time_columns_and_links(knowledge_dir: Path) -> None:
    (source,) = catalog.structured_sources(knowledge_dir, [SALES])
    graph, warnings = build_graph(source)

    assert graph["customers"].primary_key.name == "customer_id"
    assert graph["orders"].primary_key.name == "order_id"
    assert graph["orders"].time_column.name == "ordered_at"
    assert graph["customers"].time_column is None
    assert [(edge.src_table, edge.fkey, edge.dst_table) for edge in graph.edges] == [
        ("orders", "customer_id", "customers")
    ]
    assert warnings == []
    graph.validate()


@pytest.mark.parametrize(
    ("pql", "source_ids", "message"),
    [
        ("PREDICT COUNT(returns.*, 0, 30, days) > 0 FOR EACH customers.customer_id", [SALES], "returns"),
        (CHURN, [ACCOUNTS], "No selected source has every table"),
        ("PREDICT accounts.segment = 'smb' FOR EACH accounts.account_id", [SALES], "accounts"),
    ],
    ids=["unknown-table", "other-source", "table-of-an-unselected-source"],
)
async def test_a_pql_naming_a_table_no_selected_source_has_is_refused(
    knowledge_dir: Path, stub: type[StubClient], pql: str, source_ids: list[str], message: str
):
    result = await predict(knowledge_dir, pql, source_ids)

    assert result.is_error
    assert message in result.content[0].text
    assert stub.calls == []


async def test_the_source_is_the_one_whose_tables_the_pql_names(knowledge_dir: Path, stub: type[StubClient]):
    result = await predict(knowledge_dir, CHURN, [ACCOUNTS, SALES])

    assert result.structured_content["source_id"] == SALES


async def test_two_selected_sources_with_the_tables_are_refused(knowledge_dir: Path, stub: type[StubClient]):
    add_copy_of_sales(knowledge_dir, "retail.archive", "retail_archive")

    result = await predict(knowledge_dir, CHURN, [SALES, "retail.archive"])

    assert result.is_error
    assert "Several selected sources" in result.content[0].text
    assert stub.calls == []


async def test_without_an_endpoint_the_prediction_is_unavailable(knowledge_dir: Path, stub: type[StubClient]):
    async with Client(server(knowledge_dir, url=None)) as client:
        names = [tool.name for tool in (await client.list_tools()).tools]
    result = await predict(knowledge_dir, CHURN, url=None)

    assert names == ["predict"]  # registered all the same
    assert without_timing(result.structured_content) == {
        "available": False,
        "reason": "No Kumo endpoint is configured (KUMO_RELATIONAL_URL).",
        "source_id": SALES,
        "template_id": None,
        "pql": CHURN,
        "task_type": "binary_classification",
        "anchor_time": None,
        "horizon": {"value": 90, "unit": "days"},
        "entity_table": "customers",
        "rows": [],
        "model": "kumo-relational",
        "warnings": [],
    }
    assert stub.calls == []


async def test_an_unreachable_endpoint_makes_the_prediction_unavailable(knowledge_dir: Path, stub: type[StubClient]):
    stub.error = ConnectionError("connection refused")

    result = await predict(knowledge_dir, "template:churn_90d")

    content = result.structured_content
    assert (content["available"], content["reason"], content["rows"]) == (
        False,
        prediction.UNREACHABLE,
        [],
    )
    assert content["horizon"] == {"value": 90, "unit": "days"}
    assert stub.calls[0]["indices"] == ["C1", "C2", "C3"]


async def test_the_real_client_takes_this_call_and_reports_a_closed_port(knowledge_dir: Path):
    """No stub: the graph is built and the request sent, so the client accepts every argument the tool passes."""
    result = await predict(knowledge_dir, "template:churn_90d", url="http://127.0.0.1:9")

    content = result.structured_content
    assert content["available"] is False
    assert content["reason"] == prediction.UNREACHABLE, content["reason"]
    assert content["anchor_time"] == "2026-09-30T00:00:00Z"


async def test_a_regression_returns_values(knowledge_dir: Path, stub: type[StubClient]):
    stub.answer = lambda ids: pd.DataFrame({"ENTITY": ids, "PREDICTION": [10.0, 300.5, 42.0]})

    result = await predict(knowledge_dir, "PREDICT SUM(orders.net_amount, 0, 30, days) FOR EACH customers.customer_id")

    content = result.structured_content
    assert content["task_type"] == "regression"
    assert content["rows"] == [row("C2", value=300.5), row("C3", value=42.0), row("C1", value=10.0)]
    assert content["horizon"] == {"value": 30, "unit": "days"}


async def test_a_multiclass_prediction_returns_each_entitys_class(knowledge_dir: Path, stub: type[StubClient]):
    def answer(ids: list[str]) -> pd.DataFrame:
        scores = {"C1": (0.7, 0.3), "C2": (0.4, 0.6), "C3": (0.9, 0.1)}
        return pd.DataFrame(
            [
                {"ENTITY": entity, "CLASS": tier, "SCORE": score, "PREDICTED": score == max(scores[entity])}
                for entity in ids
                for tier, score in zip(("gold", "silver"), scores[entity], strict=True)
            ]
        )

    stub.answer = answer
    result = await predict(knowledge_dir, "PREDICT customers.tier FOR EACH customers.customer_id")

    content = result.structured_content
    assert content["task_type"] == "multiclass_classification"
    assert content["horizon"] is None
    assert content["rows"] == [
        row("C3", 0.9, label="gold"),
        row("C1", 0.7, label="gold"),
        row("C2", 0.6, label="silver"),
    ]


async def test_a_static_entity_filter_selects_the_entities(knowledge_dir: Path, stub: type[StubClient]):
    await predict(knowledge_dir, f"{CHURN} WHERE customers.tier = 'gold'")

    assert stub.calls[0]["indices"] == ["C1", "C3"]


async def test_a_temporal_entity_filter_is_left_to_a_warning(knowledge_dir: Path, stub: type[StubClient]):
    result = await predict(knowledge_dir, f"{CHURN} WHERE COUNT(orders.*, -90, 0, days) > 0")

    assert stub.calls[0]["indices"] == ["C1", "C2", "C3"]
    (warning,) = result.structured_content["warnings"]
    assert "could not be applied" in warning


@pytest.mark.parametrize(
    "hostile",
    [
        "customers.tier = 'gold' AND CAST((SELECT content FROM read_text('/etc/hostname')) AS INT) > 0",
        "customers.tier = 'gold'; DROP TABLE customers",
        "customers.tier = 'gold' UNION SELECT customer_id FROM orders",
        "customers.tier = 'gold' OR customers.customer_id IN (SELECT customer_id FROM orders)",
        "list_contains(range(20000000), 1)",
        "read_text('/etc/hostname') IS NOT NULL",
        "orders.net_amount > 10",
        "customers.secret = 1",
        "customers.tier",
        # Re-emitted without escaping its backslash, an escape string means something else to DuckDB.
        "customers.tier = E'gold\\' OR customers.tier = ''silver'",
        "customers.tier = $$gold$$",
        "customers.tier = $tag$gold$tag$",
        "customers.tier = 'go\\ld'",
    ],
    ids=[
        "subquery-file",
        "two-statements",
        "union",
        "in-subquery",
        "range",
        "file",
        "other-table",
        "no-column",
        "bare",
        "escape-string",
        "dollar-quoted",
        "tagged-dollar-quoted",
        "backslash",
    ],
)
async def test_an_entity_filter_that_is_not_one_plain_condition_never_runs(
    knowledge_dir: Path, stub: type[StubClient], monkeypatch: pytest.MonkeyPatch, hostile: str
):
    conditions = []
    select = entities.select

    def spy(path, table, key, condition, limit, **options):
        conditions.append(condition)
        return select(path, table, key, condition, limit, **options)

    monkeypatch.setattr(entities, "select", spy)
    result = await predict(knowledge_dir, f"{CHURN} WHERE {hostile}")

    assert conditions == [None]  # only the unfiltered selection ran
    assert stub.calls[0]["indices"] == ["C1", "C2", "C3"]
    (warning,) = result.structured_content["warnings"]
    assert "could not be applied" in warning
    assert Path("/etc/hostname").read_text().strip() not in str(result.structured_content)


def test_a_plain_condition_is_regenerated_from_its_parse():
    columns = ["customer_id", "tier", "joined_at"]

    sql = entities.condition_sql(
        "Customers.tier IN ('gold', 'silver') AND NOT joined_at < DATE '2025-01-01'", "customers", columns
    )

    assert sql == "Customers.tier IN ('gold', 'silver') AND NOT joined_at < CAST('2025-01-01' AS DATE)"


def test_a_condition_whose_sql_does_not_parse_back_the_same_is_refused(monkeypatch: pytest.MonkeyPatch):
    """Belt and braces: the SQL that runs must parse to the tree that was checked."""
    real = exp.Expression.sql

    def drift(node, *args, **kwargs):
        text = real(node, *args, **kwargs)
        return text.replace("'gold'", "'silver'") if isinstance(node, exp.EQ) else text

    monkeypatch.setattr(exp.Expression, "sql", drift)
    with pytest.raises(entities.FilterRejected, match="parse back"):
        entities.condition_sql("customers.tier = 'gold'", "customers", ["customer_id", "tier"])


async def test_duckdb_error_text_does_not_reach_the_agent(knowledge_dir: Path, stub: type[StubClient]):
    """A filter that parses but fails in DuckDB (here a cast of the tier text) is reported in written words."""
    result = await predict(knowledge_dir, f"{CHURN} WHERE CAST(customers.tier AS INTEGER) > 0")

    (warning,) = result.structured_content["warnings"]
    assert "could not be applied" in warning and "DuckDB could not apply it" in warning
    assert "gold" not in warning and "Conversion Error" not in warning
    assert stub.calls[0]["indices"] == ["C1", "C2", "C3"]


def test_at_most_two_entity_workers_run_at_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    running, peak = [0], [0]
    lock = threading.Lock()

    def slow_run(*args, **kwargs):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.2)
        with lock:
            running[0] -= 1
        return subprocess.CompletedProcess(args, 0, stdout=b'{"ids": [], "population": 0}', stderr=b"")

    monkeypatch.setattr(entities.subprocess, "run", slow_run)
    threads = [
        threading.Thread(target=entities.select, args=(tmp_path / "x.duckdb", "t", "k", None, 10)) for _ in range(6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak[0] == entities.MAX_CONCURRENT_WORKERS == 2


async def test_at_most_1000_entities_in_primary_key_order_and_the_25_best(knowledge_dir: Path, stub: type[StubClient]):
    """The tool's limit: the first 1,000 of 1,200 are scored, and the warning names the population."""
    result = await predict(knowledge_dir, "PREDICT accounts.segment = 'smb' FOR EACH accounts.account_id", [ACCOUNTS])

    (call,) = stub.calls
    assert call["indices"] == list(range(1, 1001))
    content = result.structured_content
    assert [r["entity_id"] for r in content["rows"]] == [str(i) for i in range(1000, 975, -1)]
    population, rows = content["warnings"]
    assert "1,200 entities" in population and "at most 1,000" in population
    assert "FOR EACH accounts.account_id WHERE" in population
    assert "25 highest of 1000" in rows


async def test_a_filter_under_the_limit_scores_the_whole_population(knowledge_dir: Path, stub: type[StubClient]):
    pql = "PREDICT accounts.segment = 'smb' FOR EACH accounts.account_id WHERE accounts.segment = 'enterprise'"
    result = await predict(knowledge_dir, pql, [ACCOUNTS])

    (call,) = stub.calls
    assert len(call["indices"]) == 400 and call["indices"][:3] == [3, 6, 9]  # every third account is enterprise
    assert result.structured_content["warnings"] == ["Showing the 25 highest of 400 predictions."]


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            NimRequestError(
                422, code="INVALID_REQUEST", message="Unknown column 'foo' (see http://kumo:8000/v1 /opt/x.py)"
            ),
            "Kumo rejected the query: Unknown column 'foo' (see <url> <path>)",
        ),
        (NimRequestError(401, code=None, message="no key"), prediction.REFUSED_KEY),
        (NimRequestError(503, code=None, message="warming up at http://kumo:8000"), "HTTP 503"),
        (RelationalError("Read timed out at http://kumo:8000", code="TRANSPORT_ERROR"), prediction.TIMED_OUT),
        (RelationalError("refused", code="TRANSPORT_ERROR"), prediction.UNREACHABLE),
        (TimeoutError("slow"), prediction.TIMED_OUT),
        (RuntimeError("boom in /knowledge/sources/retail.sales/tables.duckdb"), prediction.UNEXPECTED),
    ],
    ids=["invalid-query", "credentials", "server-error", "timeout", "unreachable", "builtin-timeout", "unexpected"],
)
async def test_kumo_failures_get_written_reasons(
    knowledge_dir: Path, stub: type[StubClient], error: Exception, reason: str
):
    stub.error = error

    result = await predict(knowledge_dir, CHURN)

    content = result.structured_content
    assert content["available"] is False
    assert reason in content["reason"]
    assert "http://" not in content["reason"] and "/knowledge/" not in content["reason"]


async def test_the_result_fits_30000_characters_however_its_ids_escape(knowledge_dir: Path, stub: type[StubClient]):
    stub.answer = lambda ids: pd.DataFrame(
        {"ENTITY": [f'{i}"\\' * 100 for i in range(25)], "CLASS": ['"\\' * 100] * 25, "SCORE": [0.5] * 25}
    )

    result = await predict(knowledge_dir, CHURN)

    assert len(json.dumps({"evidence_id": EVIDENCE_ID, "result": result.content[0].text})) <= 30_000
    content = result.structured_content
    assert 0 < len(content["rows"]) < 25
    assert any("fit in 30,000 characters" in warning for warning in content["warnings"])


async def test_a_template_longer_than_a_query_may_be_is_refused(knowledge_dir: Path, stub: type[StubClient]):
    manifest = fixture_source(SALES)
    manifest["prediction"]["templates"][0]["pql"] = CHURN + " " * 2000
    write_source(knowledge_dir, manifest)

    result = await predict(knowledge_dir, "template:churn_90d")

    assert result.is_error and "longer than 2000 characters" in result.content[0].text


async def test_a_manifest_must_carry_its_own_id(knowledge_dir: Path, stub: type[StubClient]):
    sources = knowledge_dir / "catalog" / "sources"
    (sources / f"{ACCOUNTS}.json").replace(sources / f"{SALES}.json")

    result = await predict(knowledge_dir, CHURN)

    assert result.is_error and "names another source" in result.content[0].text


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"pql": "template:no_such_template"}, "churn_90d"),
        ({"pql": "template:churn_90d", "anchor_time": "next tuesday"}, "anchor_time"),
        ({"pql": "SELECT * FROM customers"}, "PREDICT"),
        ({"pql": "PREDICT COUNT(orders.*, 0, 30, days) > 0"}, "FOR EACH"),
        ({"pql": "PREDICT COUNT(orders.*, 0, 30, days) > 0 FOR EACH orders.customer_id"}, "primary key"),
        ({"pql": CHURN, "source_ids": []}, "select at least one structured source"),
        ({"pql": CHURN, "source_ids": ["retail.policies"]}, "not a structured source"),
        ({"pql": CHURN, "source_ids": ["retail.unknown"]}, "not in the knowledge catalog"),
    ],
    ids=[
        "unknown-template",
        "bad-anchor",
        "not-pql",
        "no-entity",
        "not-a-primary-key",
        "empty",
        "documents",
        "unknown",
    ],
)
async def test_invalid_requests_are_refused(
    knowledge_dir: Path, stub: type[StubClient], arguments: dict[str, Any], message: str
):
    result = await predict(knowledge_dir, **{"pql": CHURN, **arguments})

    assert result.is_error
    assert message in result.content[0].text
    assert stub.calls == []


@pytest.mark.live
def test_live_prediction(knowledge_dir: Path) -> None:
    """KUMO_RELATIONAL_URL (and KUMO_API_KEY for a hosted gateway): the fixture's churn template, for real."""
    if not os.environ.get("KUMO_RELATIONAL_URL"):
        pytest.skip("set KUMO_RELATIONAL_URL")
    predictor = Predictor(Settings.from_env({**os.environ, "KNOWLEDGE_DIR": str(knowledge_dir)}))

    result = predictor.predict("template:churn_90d", [SALES])

    assert result.available, result.reason
    assert {row.entity_id for row in result.rows} == {"C1", "C2", "C3"}
    assert all(0 <= row.probability <= 1 for row in result.rows)


async def test_a_request_over_kumos_size_limit_says_how_to_narrow_the_population(
    knowledge_dir: Path, stub: type[StubClient]
):
    stub.error = RelationalError(
        "Context size exceeds the 30MB limit. Current context contains 482,994 nodes ...\nPlease reduce either the "
        "number of tables (see 'https://github.com/kumo-ai/kumo-relational-client')",
        code="INVALID_REQUEST",
    )

    result = await predict(knowledge_dir, "template:churn_90d")

    content = result.structured_content
    assert content["available"] is False
    assert content["reason"] == (
        f"Kumo's request for 3 customers entities ({prediction.KUMO_BATCH} per request) is larger than its 30 MB "
        "limit: narrow the population with FOR EACH customers.customer_id WHERE <a condition on customers's own "
        "columns> to a few hundred entities."
    )


@pytest.mark.parametrize(
    ("pql", "batch"),
    [
        (CHURN, prediction.KUMO_BATCH),
        ("PREDICT LIST_DISTINCT(orders.product_id, 0, 30, days) RANK TOP 3 FOR EACH customers.customer_id", 200),
    ],
    ids=["binary", "ranking"],
)
async def test_the_entities_go_to_kumo_in_batches(knowledge_dir: Path, stub: type[StubClient], pql: str, batch: int):
    await predict(knowledge_dir, pql)

    (call,) = stub.calls
    assert call["batch_size"] == batch


async def test_a_ranking_score_is_a_value_not_a_probability(knowledge_dir: Path, stub: type[StubClient]):
    stub.answer = lambda ids: pd.DataFrame({"ENTITY": ["C1", "C1"], "CLASS": ["P7", "P2"], "SCORE": [3.2, -0.4]})

    result = await predict(
        knowledge_dir, "PREDICT LIST_DISTINCT(orders.product_id, 0, 30, days) RANK TOP 2 FOR EACH customers.customer_id"
    )

    content = result.structured_content
    assert content["task_type"] == "temporal_link_prediction"
    assert content["rows"] == [row("C1", value=3.2, label="P7"), row("C1", value=-0.4, label="P2")]


async def test_a_ranking_scores_two_requests_worth_of_entities(knowledge_dir: Path, stub: type[StubClient]):
    stub.answer = lambda ids: pd.DataFrame({"ENTITY": ids, "CLASS": ["P1"] * len(ids), "SCORE": [1.0] * len(ids)})
    pql = "PREDICT LIST_DISTINCT(accounts.segment, 0, 30, days) RANK TOP 1 FOR EACH accounts.account_id"

    result = await predict(knowledge_dir, pql, [ACCOUNTS])

    (call,) = stub.calls
    assert (len(call["indices"]), call["batch_size"]) == (prediction.KUMO_MAX_RANKED, prediction.KUMO_RANK_BATCH)
    assert "at most 400 per prediction" in result.structured_content["warnings"][0]
