# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""query_tables end to end: the catalog, the guard, the worker process and the result budget."""

from __future__ import annotations

import asyncio
import json
import signal
import time
from pathlib import Path

import httpx
import pytest
from conftest import SALES
from conftest import TARGETS
from conftest import query
from conftest import targets_source
from conftest import write_source
from mcp.client import Client
from mcp.server.mcpserver import MCPServer
from test_guard import ESCAPES

from demo_tables import query as worker
from demo_tables.server import create_server
from demo_tables.settings import Settings

pytestmark = pytest.mark.anyio

EVIDENCE_ID = "hermes-receipt:" + "0" * 64
REVENUE_BY_TIER = """
SELECT c.tier, count(*) AS orders, sum(o.net_amount) AS revenue
FROM retail_sales.orders AS o JOIN retail_sales.customers AS c ON o.customer_id = c.customer_id
GROUP BY c.tier ORDER BY c.tier
"""


def as_hermes_reads_it(text: str) -> int:
    return len(json.dumps({"evidence_id": EVIDENCE_ID, "result": text}, ensure_ascii=False))


async def test_tool_schema(server: MCPServer):
    async with Client(server) as client:
        (tool,) = (await client.list_tools()).tools

    assert tool.name == "query_tables"
    assert tool.annotations.read_only_hint
    assert tool.input_schema["properties"].keys() == {"question", "sql", "source_ids"}
    assert set(tool.input_schema["required"]) == {"question", "sql", "source_ids"}
    assert {
        "question",
        "sql",
        "database_name",
        "databases",
        "columns",
        "rows",
        "row_count",
        "truncated",
        "elapsed_ms",
        "warnings",
    } == tool.output_schema["properties"].keys()
    assert "<alias>.<table>" in tool.description


async def test_a_join_across_the_fixture_tables(server: MCPServer):
    result = await query(server, REVENUE_BY_TIER, question="Revenue by loyalty tier?")

    assert not result.is_error, result.content[0].text
    content = result.structured_content
    assert content["rows"] == [
        {"tier": "gold", "orders": 4, "revenue": 515.5},
        {"tier": "silver", "orders": 1, "revenue": 45.25},
    ]
    assert content["columns"] == ["tier", "orders", "revenue"]
    assert (content["row_count"], content["truncated"], content["warnings"]) == (2, False, [])
    assert content["question"] == "Revenue by loyalty tier?"
    assert content["sql"] == REVENUE_BY_TIER
    assert content["database_name"] == "retail_sales"
    assert content["databases"] == [{"source_id": SALES, "alias": "retail_sales"}]
    assert content["elapsed_ms"] > 0


async def test_two_sources_attach_under_two_aliases(server: MCPServer):
    sql = """
    SELECT c.customer_id, c.tier, c.joined_at, t.target, t.set_on
    FROM retail_sales.customers AS c JOIN workspace_tables.targets AS t USING (customer_id) ORDER BY 1
    """
    result = await query(server, sql, [TARGETS, SALES])

    assert not result.is_error, result.content[0].text
    content = result.structured_content
    assert content["database_name"] == "knowledge"
    assert content["databases"] == [
        {"source_id": SALES, "alias": "retail_sales"},
        {"source_id": TARGETS, "alias": "workspace_tables"},
    ]
    # Dates come back in ISO 8601 and decimals as numbers.
    assert content["rows"] == [
        {"customer_id": "C1", "tier": "gold", "joined_at": "2024-01-05", "target": 250.0, "set_on": "2026-07-01"},
        {"customer_id": "C3", "tier": "gold", "joined_at": "2025-02-01", "target": 500.5, "set_on": "2026-07-01"},
    ]


async def test_values_are_json_safe(server: MCPServer):
    sql = """
    SELECT ordered_at, TIMESTAMPTZ '2026-06-01 10:00:00+00' AS at_utc, INTERVAL 3 DAY AS gap, 'x'::BLOB AS raw,
           [1, 2] AS list, {'a': 1.5::DECIMAL(4, 2)} AS struct, 'NaN'::DOUBLE AS nan,
           1152921504606846976::HUGEINT AS huge, NULL AS nothing, true AS flag
    FROM retail_sales.orders WHERE order_id = 'O1'
    """
    result = await query(server, sql)

    assert not result.is_error, result.content[0].text
    (row,) = result.structured_content["rows"]
    assert row == {
        "ordered_at": "2026-06-01T10:00:00",
        "at_utc": "2026-06-01T10:00:00+00:00",
        "gap": "3 days, 0:00:00",
        "raw": "[binary: 1 bytes]",
        "list": [1, 2],
        "struct": {"a": 1.5},
        "nan": "nan",
        "huge": "1152921504606846976",  # past 2**53, where JSON numbers lose precision
        "nothing": None,
        "flag": True,
    }


async def test_repeated_column_names_stay_apart(server: MCPServer):
    sql = """
    SELECT o.customer_id, c.customer_id
    FROM retail_sales.orders o JOIN retail_sales.customers c USING (customer_id) LIMIT 1
    """
    result = await query(server, sql)

    assert result.structured_content["columns"] == ["customer_id", "customer_id_2"]
    assert result.structured_content["rows"][0].keys() == {"customer_id", "customer_id_2"}


@pytest.mark.parametrize("sql", ESCAPES.values(), ids=ESCAPES.keys())
async def test_escapes_are_refused_before_anything_runs(server: MCPServer, monkeypatch: pytest.MonkeyPatch, sql: str):
    async def never(*args, **kwargs):
        raise AssertionError("the worker ran a query the guard should have refused")

    monkeypatch.setattr(worker, "run", never)
    result = await query(server, sql, [SALES, TARGETS])

    assert result.is_error
    assert "the worker ran" not in result.content[0].text


@pytest.mark.parametrize(
    ("sql", "refusal"),
    [
        ("SELECT * FROM read_csv('/etc/passwd')", "disabled by configuration"),
        ("SELECT * FROM glob('/etc/*')", "disabled by configuration"),
        ("SELECT * FROM '/etc/passwd'", "does not exist"),
        ("SELECT * FROM retail_sales.orders, (SELECT * FROM read_text('/etc/hostname'))", "disabled by configuration"),
        ("COPY (SELECT 1) TO '/tmp/out.csv'", "exactly one SELECT"),
        ("SET enable_external_access = true", "exactly one SELECT"),
        ("ATTACH '/tmp/evil.duckdb' AS evil", "exactly one SELECT"),
        ("INSTALL httpfs", "exactly one SELECT"),
        ("SELECT 1; SELECT 2", "exactly one SELECT"),
    ],
)
async def test_the_worker_holds_even_without_the_guard(knowledge_dir: Path, sql: str, refusal: str):
    """DuckDB itself refuses: external access off, the configuration locked, one SELECT statement."""
    databases = [worker.Attachment(alias="retail_sales", path=knowledge_dir / "sources" / SALES / "tables.duckdb")]

    with pytest.raises(worker.QueryFailed, match=refusal):
        await worker.run(databases, sql, timeout=10)


async def test_the_attached_databases_are_read_only(knowledge_dir: Path):
    databases = [worker.Attachment(alias="retail_sales", path=knowledge_dir / "sources" / SALES / "tables.duckdb")]
    sql = "WITH gone AS (DELETE FROM retail_sales.orders RETURNING *) SELECT count(*) FROM gone"

    with pytest.raises(worker.QueryFailed):
        await worker.run(databases, sql, timeout=10)
    rows = await worker.run(databases, "SELECT count(*) FROM retail_sales.orders", timeout=10)
    assert rows.rows == [[5]]


async def test_a_long_query_is_stopped_at_the_timeout(knowledge_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """DuckDB 1.5.5 counts range(10_000_000_000) in about a second, so this counts a thousand times as many rows."""
    workers = []
    start_worker = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        workers.append(await start_worker(*args, **kwargs))
        return workers[-1]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    server = create_server(Settings(knowledge_dir=knowledge_dir, timeout_seconds=2))
    started = time.monotonic()

    result = await query(server, "SELECT count(*) FROM range(10000000000000)")

    assert result.is_error
    assert "took longer than 2 seconds" in result.content[0].text
    assert time.monotonic() - started < 6
    (process,) = workers
    assert process.returncode == -signal.SIGKILL  # the worker was killed, not left running


def test_the_timeout_is_ten_seconds():
    assert Settings().timeout_seconds == 10.0


async def test_at_most_200_rows(server: MCPServer):
    result = await query(server, "SELECT range AS n FROM range(500)")

    content = result.structured_content
    assert (content["row_count"], content["truncated"]) == (200, True)
    assert content["rows"][-1] == {"n": 199}
    assert any("first 200 rows" in warning for warning in content["warnings"])


async def test_a_wide_result_drops_rows_to_stay_under_30000_characters(server: MCPServer):
    result = await query(server, "SELECT n, repeat('x\"\\', 300) AS filler FROM range(150) t(n)")

    assert not result.is_error, result.content[0].text
    content = result.structured_content
    assert as_hermes_reads_it(result.content[0].text) <= 30_000
    assert 0 < content["row_count"] < 150 and content["row_count"] == len(content["rows"])
    assert content["truncated"]
    assert any("30,000 characters" in warning for warning in content["warnings"])


async def test_a_long_value_is_cut(server: MCPServer):
    result = await query(server, "SELECT repeat('y', 5000) AS long")

    (row,) = result.structured_content["rows"]
    assert len(row["long"]) <= worker.MAX_CELL_CHARS and row["long"].endswith("…")
    assert any("cut" in warning for warning in result.structured_content["warnings"])


async def test_a_huge_list_is_cut_in_the_worker(server: MCPServer):
    """One cell of 8 million numbers: the worker writes a short list, not 44 MB."""
    result = await query(server, "SELECT range(8000000) AS r")

    assert not result.is_error, result.content[0].text
    (row,) = result.structured_content["rows"]
    assert 0 < len(row["r"]) <= worker.MAX_ITEMS and row["r"][:3] == [0, 1, 2]
    assert any("cut" in warning for warning in result.structured_content["warnings"])


async def test_many_big_rows_stay_small_and_fast(server: MCPServer):
    """200 rows of 150,000 numbers each once took 2.8 GB and blocked the event loop for 5.7 s."""
    started = time.monotonic()

    result = await query(server, "SELECT range(150000) AS r FROM range(200)")

    assert not result.is_error, result.content[0].text
    assert as_hermes_reads_it(result.content[0].text) <= 30_000
    assert result.structured_content["truncated"]
    assert time.monotonic() - started < 5


async def test_the_worker_stops_adding_rows_at_its_byte_budget(knowledge_dir: Path):
    databases = [worker.Attachment(alias="retail_sales", path=knowledge_dir / "sources" / SALES / "tables.duckdb")]

    rows = await worker.run(databases, "SELECT repeat('x', 1000) AS s FROM range(100)", timeout=10, max_bytes=10_000)

    assert 0 < len(rows.rows) < 10 and rows.truncated and rows.byte_limited


async def test_a_worker_writing_past_the_cap_is_stopped(knowledge_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Should the worker ever write more than its budget, the server stops reading and kills it."""
    monkeypatch.setattr(worker, "OUTPUT_OVERHEAD_BYTES", -9_000)  # read at most 1,000 of the budget's 10,000 bytes
    databases = [worker.Attachment(alias="retail_sales", path=knowledge_dir / "sources" / SALES / "tables.duckdb")]

    with pytest.raises(worker.QueryFailed, match="too large"):
        await worker.run(databases, "SELECT repeat('x', 1000) AS s FROM range(100)", timeout=10, max_bytes=10_000)


async def test_a_long_sql_echo_is_shortened_to_fit(server: MCPServer):
    """A query whose text JSON escapes twice over (quotes in a comment) is echoed shortened, not past the budget."""
    sql = "SELECT 1 AS a /* " + '"\\' * 4900 + " */"
    result = await query(server, sql, question='"\\' * 499)

    assert not result.is_error, result.content[0].text
    assert as_hermes_reads_it(result.content[0].text) <= 30_000
    content = result.structured_content
    assert content["sql"] != sql and content["sql"].startswith("SELECT 1 AS a /*") and content["sql"].endswith("…")
    assert any("SQL" in warning and "shortened" in warning for warning in content["warnings"])


async def test_a_manifest_must_carry_its_own_id(server: MCPServer, knowledge_dir: Path):
    write_source(knowledge_dir, {**targets_source(), "id": "retail.other"})
    (knowledge_dir / "catalog" / "sources" / "retail.other.json").rename(
        knowledge_dir / "catalog" / "sources" / f"{TARGETS}.json"
    )

    result = await query(server, "SELECT 1", [TARGETS])

    assert result.is_error
    assert "names another source" in result.content[0].text


async def test_a_database_error_keeps_its_class_but_no_path(knowledge_dir: Path):
    databases = [worker.Attachment(alias="retail_sales", path=knowledge_dir / "sources" / SALES / "tables.duckdb")]

    with pytest.raises(worker.QueryFailed) as refused:
        await worker.run(databases, "SELECT * FROM read_csv('/etc/passwd')", timeout=10)

    message = str(refused.value)
    assert message.startswith("Permission Error:") and "<path>" in message
    assert "/etc/passwd" not in message


def test_error_text_loses_database_files_and_absolute_paths():
    message = worker.redact_error(
        'IO Error: Could not read "/knowledge/sources/retail.sales/tables.duckdb": busy\n'
        "Candidate bindings: tables.duckdb at /tmp/x\n\nLINE 1: SELECT 1"
    )

    assert message.startswith("IO Error: Could not read")
    assert "/knowledge" not in message and "tables.duckdb" not in message and "/tmp/x" not in message
    assert "LINE 1" not in message  # the first two lines only


async def test_a_database_error_reaches_the_agent(server: MCPServer):
    result = await query(server, "SELECT nope FROM retail_sales.orders")

    assert result.is_error
    assert "nope" in result.content[0].text


@pytest.mark.parametrize(
    ("source_ids", "message"),
    [
        ([], "select at least one structured source"),
        (["retail.unknown"], "retail.unknown is not in the knowledge catalog"),
        (["retail.policies"], "retail.policies is a documents source, not a structured source"),
        (["retail.staging"], "retail.staging has status 'empty'"),
        (["retail.missing"], "retail.missing has no tables yet"),
        (["../../etc/passwd"], "'../../etc/passwd' is not a source id"),
    ],
    ids=["empty", "unknown", "documents", "empty-status", "no-database-file", "path"],
)
async def test_sources_that_cannot_be_queried_are_refused(server: MCPServer, source_ids: list[str], message: str):
    result = await query(server, "SELECT 1", source_ids)

    assert result.is_error
    assert message in result.content[0].text


async def test_the_catalog_is_read_on_every_call(server: MCPServer, knowledge_dir: Path):
    sql = "SELECT count(*) AS n FROM workspace_tables.targets"
    write_source(knowledge_dir, targets_source(status="failed"))
    failed = await query(server, sql, [TARGETS])
    write_source(knowledge_dir, targets_source(status="ingesting"))
    ingesting = await query(server, sql, [TARGETS])

    assert failed.is_error
    assert ingesting.structured_content["rows"] == [{"n": 2}]


async def test_health(server: MCPServer):
    transport = httpx.ASGITransport(app=server.streamable_http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://tables") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_settings_defaults():
    settings = Settings.from_env({})

    assert (settings.knowledge_dir, settings.port, settings.timeout_seconds) == (Path("/knowledge"), 8321, 10.0)
    assert Settings.from_env({"KNOWLEDGE_DIR": "/srv/kb", "TABLES_PORT": "9001"}).port == 9001
