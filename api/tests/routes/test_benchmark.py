# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Benchmark routes: a finished job's market calls replayed on market analytics' CPU and GPU engines, and the
Milvus CPU/GPU index comparison that applies to its retrieval calls."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from support import event
from support import load_contract
from support import receipt_for

from demo_api.benchmark import Benchmark
from demo_api.benchmark import RetrievalBenchmark
from demo_api.benchmark.retrieval import RetrievalProfile
from demo_api.benchmark.runner import stage_from
from demo_api.settings import Settings

ANOMALY = "mcp__market_analytics__market_anomaly_scan"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.market_analytics_url = "http://market.test"
    return settings


def engine(device: str, trials: list[float]) -> dict[str, Any]:
    library = "cuml.accel" if device == "gpu" else "scikit-learn"
    return {"device": device, "library": library, "version": "26.06", "trials_ms": trials}


def outcome(cpu: list[float], gpu: list[float], *, status: str = "completed") -> dict[str, Any]:
    parity = status == "completed"
    reason = None if parity else "The CPU and GPU results differ at payload.observations[0].asset_id."
    return {
        "available": True,
        "status": status,
        "parity": parity,
        "reason": reason,
        "cpu": engine("cpu", cpu),
        "gpu": engine("gpu", gpu),
    }


async def finished_job(api: httpx.AsyncClient, app, fake_hermes, post_receipt, *, market: bool = True) -> str:
    kind, tool = (
        ("analytics_result", ANOMALY) if market else ("retrieval_evidence", "mcp__retrieval__retrieve_evidence")
    )
    await api.post("/v1/jobs/async/submit", json={"input": "Which sessions were unusual?", "job_id": "job-1"})
    run_id = await fake_hermes.wait_for_run()
    while (await app.state.services.store.get("job-1")).hermes_run_id is None:
        await asyncio.sleep(0.01)
    receipt = receipt_for("job-1", kind=kind)
    await post_receipt(receipt)
    events = [
        event("tool.started", tool=tool, tool_call_id="call_1"),
        event("tool.completed", tool=tool, tool_call_id="call_1", duration=0.2, error=False),
        event("run.completed", usage={"input_tokens": 10, "output_tokens": 5}),
    ]
    fake_hermes.finish(run_id, events=events, output=f"Unusual sessions [evidence:{receipt['receiptId']}].")
    for _ in range(500):
        if (await api.get("/v1/jobs/async/job/job-1")).json()["status"] == "success":
            return "job-1"
        await asyncio.sleep(0.01)
    raise AssertionError("the job did not finish")


async def test_a_finished_run_is_compared_stored_and_exported(app, api, fake_hermes, post_receipt, upstreams):
    requests: list[dict[str, Any]] = []

    def market(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=outcome([100.0, 98.0, 104.0, 99.0, 101.0], [40.0, 41.0, 39.0, 40.0, 42.0]))

    upstreams["market.test"] = market
    job_id = await finished_job(api, app, fake_hermes, post_receipt)
    assert (await api.get(f"/v1/jobs/async/job/{job_id}/benchmark")).status_code == 404

    response = await api.post(f"/v1/jobs/async/job/{job_id}/benchmark")

    body = response.json()
    assert response.status_code == 200
    Benchmark.model_validate(body)
    assert (body["status"], body["pairs"]) == ("completed", 5)
    [stage] = body["stages"]
    assert (stage["toolName"], stage["outcome"], stage["parity"], stage["qualified"]) == (
        "market_anomaly_scan",
        "completed",
        True,
        True,
    )
    assert (stage["cpu"]["medianMs"], stage["gpu"]["medianMs"]) == (100.0, 40.0)
    assert stage["speedup"] == pytest.approx(2.5)
    assert stage["speedupRange"] == {"low": pytest.approx(98 / 41), "high": pytest.approx(104 / 39)}
    receipt = receipt_for(job_id, kind="analytics_result")
    assert requests == [
        {
            "tool": "market_anomaly_scan",
            "arguments": receipt["content"]["publicParameters"],
            "pairs": 5,
            "budget_seconds": 20.0,
        }
    ]
    # Stored: later requests do not rerun it, and the export carries it
    assert (await api.post(f"/v1/jobs/async/job/{job_id}/benchmark")).json() == body
    assert (await api.get(f"/v1/jobs/async/job/{job_id}/benchmark")).json() == body
    assert (await api.get(f"/v1/jobs/async/job/{job_id}/export")).json()["benchmark"] == body
    assert len(requests) == 1


async def test_a_cpu_only_service_is_unavailable_and_not_stored(app, api, fake_hermes, post_receipt, upstreams):
    upstreams["market.test"] = lambda request: httpx.Response(200, json={"available": False, "reason": "CPU only."})
    job_id = await finished_job(api, app, fake_hermes, post_receipt)

    body = (await api.post(f"/v1/jobs/async/job/{job_id}/benchmark")).json()

    assert (body["status"], body["reason"], body["stages"]) == ("unavailable", "CPU only.", [])
    assert (await api.get(f"/v1/jobs/async/job/{job_id}/benchmark")).status_code == 404


async def test_an_unreachable_service_is_unavailable(app, api, fake_hermes, post_receipt, upstreams):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    upstreams["market.test"] = down
    job_id = await finished_job(api, app, fake_hermes, post_receipt)

    body = (await api.post(f"/v1/jobs/async/job/{job_id}/benchmark")).json()

    assert body["status"] == "unavailable"
    assert "not reachable" in body["reason"]


async def test_runs_without_market_calls_and_unknown_jobs_are_refused(app, api, fake_hermes, post_receipt):
    job_id = await finished_job(api, app, fake_hermes, post_receipt, market=False)

    assert (await api.post(f"/v1/jobs/async/job/{job_id}/benchmark")).status_code == 422
    assert (await api.post("/v1/jobs/async/job/nope/benchmark")).status_code == 404


def test_a_speedup_is_claimed_only_when_every_pair_was_faster_on_the_gpu() -> None:
    receipt = receipt_for("job-1", kind="analytics_result")

    one_slow_pair = stage_from(receipt, outcome([100.0] * 5, [40.0, 40.0, 40.0, 40.0, 120.0]))
    too_few = stage_from(receipt, outcome([100.0] * 4, [40.0] * 4))
    mismatch = stage_from(receipt, outcome([100.0] * 5, [40.0] * 5, status="mismatch"))
    gpu_slower = stage_from(receipt, outcome([10.0] * 5, [40.0] * 5))

    for stage in (one_slow_pair, too_few, mismatch, gpu_slower):
        assert (stage.qualified, stage.speedup, stage.speedup_range) == (False, None, None)
    assert (one_slow_pair.cpu.median_ms, one_slow_pair.gpu.median_ms) == (100.0, 40.0)
    assert mismatch.outcome == "mismatch" and mismatch.parity is False


async def test_the_milvus_comparison_applies_to_runs_on_the_same_index_build(
    app, api, fake_hermes, post_receipt, data_dir
):
    job_id = await finished_job(api, app, fake_hermes, post_receipt, market=False)
    route = f"/v1/jobs/async/job/{job_id}/retrieval-benchmark"

    # A CPU-only stack has no GPU index, so no comparison
    response = await api.get(route)
    assert response.status_code == 404
    assert "CPU index only" in response.json()["detail"]
    assert (await api.get(f"/v1/jobs/async/job/{job_id}/export")).json()["retrievalBenchmark"] is None

    [measured] = load_contract("retrieval-benchmarks.json")
    (data_dir / "retrieval-benchmark.json").write_text(json.dumps(measured))
    body = (await api.get(route)).json()
    assert body == RetrievalBenchmark.model_validate(measured).model_dump(mode="json")
    assert (await api.get(f"/v1/jobs/async/job/{job_id}/export")).json()["retrievalBenchmark"] == body

    # Measured on another build of the index: it does not apply to this run
    (data_dir / "retrieval-benchmark.json").write_text(json.dumps(measured | {"collectionVersion": "x__2"}))
    assert (await api.get(route)).status_code == 409
    assert (await api.get(f"/v1/jobs/async/job/{job_id}/export")).json()["retrievalBenchmark"] is None


async def test_the_milvus_comparison_needs_a_run_that_searched_documents(app, api, fake_hermes, post_receipt, data_dir):
    [measured] = load_contract("retrieval-benchmarks.json")
    (data_dir / "retrieval-benchmark.json").write_text(json.dumps(measured))
    job_id = await finished_job(api, app, fake_hermes, post_receipt)

    assert (await api.get(f"/v1/jobs/async/job/{job_id}/retrieval-benchmark")).status_code == 422
    assert (await api.get("/v1/jobs/async/job/nope/retrieval-benchmark")).status_code == 404


def test_a_retrieval_speedup_is_claimed_only_with_passing_quality() -> None:
    [measured] = load_contract("retrieval-benchmarks.json")
    profile = measured["profiles"][1]
    assert profile["claim"]["decision"] == "gpu_speedup"

    failing = profile | {"quality": profile["quality"] | {"passed": False, "failureReasons": ["recall_below_gate"]}}
    small = profile | {"claim": profile["claim"] | {"gpuSpeedupFactor": 1.05, "observedCpuOverGpuRatio": 1.05}}
    for invalid in (failing, small):
        with pytest.raises(ValidationError):
            RetrievalProfile.model_validate(invalid)
    unclaimed = profile["claim"] | {"decision": "cpu_faster_or_equal"}
    with pytest.raises(ValidationError):  # a factor belongs to a claim only
        RetrievalProfile.model_validate(profile | {"claim": unclaimed})
