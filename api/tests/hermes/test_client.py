# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Hermes Runs client and lifecycle, against scripted HTTP responses."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from demo_api.hermes.client import HermesClient
from demo_api.hermes.client import HermesError
from demo_api.hermes.client import RunEvent
from demo_api.hermes.lifecycle import HermesRunDeadlineExceeded
from demo_api.hermes.lifecycle import HermesRunLifecycle
from demo_api.hermes.lifecycle import RunBinding
from demo_api.hermes.lifecycle import RunLimits
from demo_api.hermes.progress import HermesRunBudgetExceeded
from demo_api.hermes.progress import ProgressGuard
from demo_api.hermes.progress import ProgressLimits

SSE = (
    ": keepalive\n\n"
    'event: tool.started\ndata: {"tool": "x", "tool_call_id": "c1", "timestamp": 1.5}\n\n'
    'id: 7\ndata: {"event": "run.completed",\ndata: "run_id": "run-1"}\n\n'
    "data: [DONE]\n\n"
)


def client_for(handler, **options) -> HermesClient:
    return HermesClient(
        "http://hermes.test", "secret-key", retry_base_seconds=0, transport=httpx.MockTransport(handler), **options
    )


async def test_events_are_decoded_from_server_sent_events():
    client = client_for(lambda request: httpx.Response(200, text=SSE))

    events = [event async for event in client.stream_events("run-1")]

    assert [(e.event, e.data.get("tool_call_id"), e.timestamp, e.sse_id) for e in events] == [
        ("tool.started", "c1", 1.5, None),
        ("run.completed", None, None, "7"),
    ]
    assert events[1].is_terminal


async def test_an_event_for_another_run_is_a_protocol_error():
    client = client_for(lambda request: httpx.Response(200, text='data: {"event": "x", "run_id": "run-2"}\n\n'))
    with pytest.raises(HermesError, match="different run"):
        [event async for event in client.stream_events("run-1")]


async def test_busy_responses_are_retried_and_the_run_is_created_once_per_key():
    responses = iter([httpx.Response(503), httpx.Response(202, json={"run_id": "run-1", "status": "queued"})])
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return next(responses)

    run_id = await client_for(handle).create_run({"input": "q"}, idempotency_key="job-1")

    assert run_id == "run-1"
    assert [r.headers["Idempotency-Key"] for r in seen] == ["job-1", "job-1"]
    assert seen[0].headers["Authorization"] == "Bearer secret-key"


async def test_errors_never_echo_the_api_key():
    client = client_for(lambda request: httpx.Response(400, json={"error": {"message": "bad key secret-key"}}))
    with pytest.raises(HermesError) as error:
        await client.get_run("run-1")
    assert "secret-key" not in str(error.value)
    assert error.value.status_code == 400


async def test_a_broken_stream_falls_back_to_polling_the_status():
    statuses = iter(["running", "completed"])

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/events"):
            raise httpx.ReadError("connection reset")
        return httpx.Response(200, json={"run_id": "run-1", "status": next(statuses), "output": "answer"})

    lifecycle = HermesRunLifecycle(client_for(handle), RunLimits(poll_interval_seconds=0.01))
    status = await lifecycle.collect(RunBinding("run-1", "job-1"), on_event=_ignore)

    assert (status.status, status.output) == ("completed", "answer")


async def test_a_run_past_its_wall_deadline_is_stopped():
    stops: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stop"):
            stops.append(request.url.path)
            return httpx.Response(200, json={"run_id": "run-1", "status": "cancelled"})
        return httpx.Response(200, json={"run_id": "run-1", "status": "running"})

    lifecycle = HermesRunLifecycle(client_for(handle), RunLimits(wall_timeout_seconds=0.1, poll_interval_seconds=0.01))
    with pytest.raises(HermesRunDeadlineExceeded):
        await lifecycle.collect(RunBinding("run-1", "job-1"), on_event=_ignore)
    assert stops == ["/v1/runs/run-1/stop"]


def test_progress_guard_budgets():
    now = [0.0]
    guard = ProgressGuard("run-1", ProgressLimits(idle_timeout_seconds=10, max_tool_calls=1), clock=lambda: now[0])
    guard.observe(_event("tool.started", tool_call_id="c1"))
    now[0] = 50  # a tool may be silent while it runs
    guard.check()
    guard.observe(_event("tool.completed", tool_call_id="c1"))
    now[0] = 61
    with pytest.raises(HermesRunBudgetExceeded, match="idle"):
        guard.check()
    with pytest.raises(HermesRunBudgetExceeded, match="tool calls"):
        guard.observe(_event("tool.started", tool_call_id="c2"))


async def _ignore(event) -> None:
    await asyncio.sleep(0)


def _event(name: str, **data) -> RunEvent:
    return RunEvent(name, "run-1", {"event": name, **data})
