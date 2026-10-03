# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The MCP surface: tools/list, annotations, results, failures as results, responsiveness and /health."""

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from starlette.testclient import TestClient

from market_analytics import tools
from market_analytics.data import MarketData
from market_analytics.data import Pack
from market_analytics.server import create_server
from market_analytics.worker import Worker
from market_analytics.worker import WorkerError

pytestmark = pytest.mark.anyio

SCAN = {
    "universe_id": "reviewed_assets",
    "start": "2026-06-01T00:00:00Z",
    "end": "2026-06-30T23:59:59Z",
    "metrics": ["return"],
    "limit": 2,
}
TOOLS = [
    "market_scan",
    "market_anomaly_scan",
    "price_context",
    "sentiment_timeline",
    "analyze_news_price_relationship",
    "analyze_market_relationships",
    "intraday_scan",
]


class FakeWorker:
    """Runs the tools in this process after an optional delay, or raises `error`, like Worker.call."""

    def __init__(self, data: MarketData, *, delay: float = 0.0, error: Exception | None = None) -> None:
        self.data, self.delay, self.error = data, delay, error
        self.alive, self.pid = True, 4242

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return tools.run(self.data, tool, arguments)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_tools_are_listed_read_only_with_typed_schemas(pack: Pack, data: MarketData) -> None:
    async with Client(create_server(pack, FakeWorker(data))) as client:
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert list(listed) == TOOLS
    for tool in listed.values():
        hints = tool.annotations.model_dump(by_alias=True, exclude_none=True)
        assert hints == {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}
        assert "source_ids" in tool.input_schema["properties"]
        assert "source_ids" not in tool.input_schema.get("required", [])
        assert "scope_grant" not in tool.input_schema["properties"]
        assert {"status", "payload", "engine", "timing", "rows_scanned"} <= set(tool.output_schema["properties"])
    assert listed["market_scan"].input_schema["properties"]["universe_id"]["enum"] == ["reviewed_assets", "all_assets"]
    assert (
        "from 2026-06-01 to 2026-08-06, whose links join every pair"
        in listed["analyze_market_relationships"].description
    )


async def test_tools_without_their_data_say_so_in_their_descriptions(pack: Pack, daily_only: MarketData) -> None:
    async with Client(create_server(daily_only.pack, FakeWorker(daily_only))) as client:
        descriptions = {tool.name: tool.description for tool in (await client.list_tools()).tools}
    async with Client(create_server(pack, FakeWorker(daily_only))) as client:
        full = {tool.name: tool.description for tool in (await client.list_tools()).tools}

    unavailable = {
        name for name, text in descriptions.items() if text.startswith("Unavailable in the active data pack")
    }
    assert unavailable == {"sentiment_timeline", "analyze_news_price_relationship", "intraday_scan"}
    assert not any(text.startswith("Unavailable") for text in full.values())


async def test_a_call_returns_the_result_as_structured_content(pack: Pack, data: MarketData) -> None:
    async with Client(create_server(pack, FakeWorker(data))) as client:
        result = await client.call_tool("market_scan", SCAN)

    assert not result.is_error
    content = result.structured_content
    assert content["status"] == "succeeded"
    assert (content["source_id"], content["database_name"]) == ("market_fixture_structured", "market_fixture")
    assert len(content["payload"]["observations"]) == 2
    assert content["timing"]["total_ms"] >= content["timing"]["compute_ms"] > 0


async def test_tools_list_stays_responsive_during_long_calls(pack: Pack, data: MarketData) -> None:
    async with Client(create_server(pack, FakeWorker(data, delay=1.0))) as client:
        async with anyio.create_task_group() as calls:
            calls.start_soon(client.call_tool, "market_scan", SCAN)
            calls.start_soon(client.call_tool, "market_scan", SCAN)
            await anyio.sleep(0.1)
            started = time.perf_counter()
            await client.list_tools()
            assert time.perf_counter() - started < 0.5


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (TimeoutError("market_scan exceeded its 120 s deadline"), "deadline_exceeded"),
        (WorkerError("the analytics worker exited during market_scan"), "execution_failed"),
    ],
)
async def test_worker_failures_become_failed_results(pack: Pack, data: MarketData, error: Exception, code: str) -> None:
    async with Client(create_server(pack, FakeWorker(data, error=error))) as client:
        result = await client.call_tool("market_scan", SCAN)

    assert not result.is_error  # a result, so the receipt still records the failed call
    assert result.structured_content["status"] == "failed"
    assert result.structured_content["error"] == {"code": code, "message": str(error)}


async def test_failure_messages_fit_the_receipt(pack: Pack, data: MarketData) -> None:
    error = WorkerError("the analytics worker failed to start: " + "x" * 5_000)
    async with Client(create_server(pack, FakeWorker(data, error=error))) as client:
        result = await client.call_tool("market_scan", SCAN)

    message = result.structured_content["error"]["message"]
    assert len(message) == 1_000
    assert message.startswith("the analytics worker failed to start: x")
    assert message.endswith("x…")


async def test_calls_outside_the_selected_sources_are_refused(pack: Pack, data: MarketData) -> None:
    async with Client(create_server(pack, FakeWorker(data))) as client:
        refused = await client.call_tool("market_scan", {**SCAN, "source_ids": ["market_news"]})
        allowed = await client.call_tool("market_scan", {**SCAN, "source_ids": ["market_news", pack.source_id]})

    assert refused.structured_content["status"] == "failed"
    assert refused.structured_content["error"] == {
        "code": "source_not_selected",
        "message": "market_fixture_structured is not one of the selected sources",
    }
    assert allowed.structured_content["status"] == "succeeded"


async def test_timestamps_without_a_timezone_are_rejected(pack: Pack, data: MarketData) -> None:
    async with Client(create_server(pack, FakeWorker(data))) as client:
        result = await client.call_tool("market_scan", {**SCAN, "start": "2026-06-01T00:00:00"})

    assert result.is_error
    assert "timezone" in result.content[0].text


def test_health_reports_whether_the_worker_is_up(pack: Pack, data: MarketData) -> None:
    worker = FakeWorker(data)
    http = TestClient(create_server(pack, worker).streamable_http_app())
    assert http.get("/health").json() == {"worker": 4242, "alive": True}

    worker.alive = False
    assert http.get("/health").status_code == 503


@pytest.fixture
def worker(pack_root: Path) -> Iterator[Worker]:
    worker = Worker(pack_root, timeout=60)
    worker.start()
    yield worker
    worker.close()


async def test_calls_run_in_the_worker_process(pack: Pack, worker: Worker) -> None:
    async with Client(create_server(pack, worker)) as client:
        result = await client.call_tool(
            "price_context", {"asset_ids": ["GAMA"], "start": SCAN["start"], "end": SCAN["end"]}
        )

    content = result.structured_content
    assert content["status"] == "succeeded"
    assert content["engine"]["device"] == "cpu"
    assert content["payload"]["summaries"][0]["asset_id"] == "asset-gamma"
