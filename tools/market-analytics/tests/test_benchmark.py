# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""POST /benchmark: matched CPU and GPU runs of one tool call, and how their payloads are compared."""

import time
from typing import Any

import pytest
from starlette.testclient import TestClient

from market_analytics import benchmark
from market_analytics import tools
from market_analytics.data import MarketData
from market_analytics.data import Pack
from market_analytics.server import create_server

SCAN = {
    "universe_id": "reviewed_assets",
    "start": "2026-06-01T00:00:00Z",
    "end": "2026-06-30T23:59:59Z",
    "metrics": ["return"],
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class RecordingWorker:
    """Runs the tools in this process, as `device`, and records the order of the calls it gets."""

    def __init__(self, data: MarketData, device: str, calls: list[str]) -> None:
        self.data, self.device, self.calls = data, device, calls
        self.alive, self.pid = True, 4242

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(self.device)
        result = tools.run(self.data, tool, arguments)
        if result["engine"]:
            result["engine"]["device"] = self.device
        return result


def test_payloads_match_within_gpu_rounding() -> None:
    def rows(asset: str, score: float) -> dict[str, Any]:
        return {"rows": [{"asset_id": asset, "score": score, "rank": 1}], "count": 3}

    cpu = rows("a", 0.1234567)
    assert benchmark.payload_mismatch(rows("a", 0.1234568), cpu) is None
    assert benchmark.payload_mismatch(rows("b", 0.1234567), cpu) == "payload.rows[0].asset_id"
    assert benchmark.payload_mismatch({"rows": [], "count": 3}, cpu) == "payload.rows"
    assert benchmark.payload_mismatch(rows("a", 0.2), cpu) == "payload.rows[0].score"


def result(ms: float, payload: Any = None, status: str = "succeeded") -> dict[str, Any]:
    return {
        "status": status,
        "payload": payload if payload is not None else {"value": 1.0},
        "error": {"message": "bad window"} if status == "failed" else None,
        "engine": {"device": "x", "library": "lib", "version": "1"},
        "timing": {"compute_ms": ms},
    }


@pytest.mark.anyio
async def test_pairs_alternate_after_one_untimed_call_each() -> None:
    order: list[str] = []

    async def call(target: str) -> dict[str, Any]:
        order.append(target)
        return result(10.0 if target == "cpu" else 2.0)

    outcome = await benchmark.compare(call, pairs=3, budget_seconds=60)

    assert order == ["gpu", "cpu", "gpu", "cpu", "cpu", "gpu", "gpu", "cpu"]
    assert outcome["status"] == "completed" and outcome["parity"] is True
    assert outcome["cpu"]["trials_ms"] == [10.0, 10.0, 10.0]
    assert outcome["gpu"]["trials_ms"] == [2.0, 2.0, 2.0]


@pytest.mark.anyio
async def test_the_budget_stops_after_a_whole_pair() -> None:
    async def call(target: str) -> dict[str, Any]:
        time.sleep(0.02)
        return result(1.0)

    outcome = await benchmark.compare(call, pairs=10, budget_seconds=0.01)

    assert len(outcome["cpu"]["trials_ms"]) == len(outcome["gpu"]["trials_ms"]) == 1


@pytest.mark.anyio
async def test_different_payloads_are_a_mismatch_and_failures_are_reported() -> None:
    async def differs(target: str) -> dict[str, Any]:
        return result(1.0, {"top": "a" if target == "cpu" else "b"})

    async def fails(target: str) -> dict[str, Any]:
        return result(1.0, status="failed")

    mismatch = await benchmark.compare(differs, pairs=2, budget_seconds=60)
    failed = await benchmark.compare(fails, pairs=2, budget_seconds=60)

    assert (mismatch["status"], mismatch["parity"]) == ("mismatch", False)
    assert "payload.top" in mismatch["reason"]
    assert (failed["status"], failed["reason"], failed["cpu"]) == ("failed", "bad window", None)


def test_the_route_runs_the_mcp_tool_on_both_workers(pack: Pack, data: MarketData) -> None:
    calls: list[str] = []
    gpu, cpu = RecordingWorker(data, "gpu", calls), RecordingWorker(data, "cpu", calls)
    http = TestClient(create_server(pack, gpu, cpu_worker=lambda: cpu).streamable_http_app())

    response = http.post("/benchmark", json={"tool": "market_scan", "arguments": SCAN, "pairs": 2})

    body = response.json()
    assert response.status_code == 200
    assert (body["available"], body["status"], body["parity"]) == (True, "completed", True)
    assert body["gpu"]["device"] == "gpu" and body["cpu"]["device"] == "cpu"
    assert body["cpu"]["library"] == "pandas"
    assert len(body["cpu"]["trials_ms"]) == len(body["gpu"]["trials_ms"]) == 2
    assert calls == ["gpu", "cpu", "gpu", "cpu", "cpu", "gpu"]


def test_the_cpu_service_and_bad_requests(pack: Pack, data: MarketData) -> None:
    calls: list[str] = []
    worker = RecordingWorker(data, "cpu", calls)
    cpu_only = TestClient(create_server(pack, worker).streamable_http_app())
    gpu = TestClient(create_server(pack, worker, cpu_worker=lambda: worker).streamable_http_app())

    assert cpu_only.post("/benchmark", json={"tool": "market_scan", "arguments": SCAN}).json() == {
        "available": False,
        "reason": benchmark.CPU_ONLY,
    }
    assert gpu.post("/benchmark", json={"tool": "delete_everything"}).status_code == 422
    assert gpu.post("/benchmark", json={"tool": "market_scan", "pairs": 0}).status_code == 422
    missing = gpu.post("/benchmark", json={"tool": "market_scan", "arguments": {"universe_id": "reviewed_assets"}})
    assert missing.json()["status"] == "failed"
    assert calls == []
