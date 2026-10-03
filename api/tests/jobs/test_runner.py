# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Parity tests: the in-process runner keeps every job capability the prototype got from Dask and NAT's JobStore.

Each test names the prototype behavior it pins. The fake lifecycle mirrors ``HermesRunLifecycle``:
``collect`` issues its own best-effort stop when it is cancelled, and ``cancel`` asks Hermes to stop
the run. The executor has the real executor's shape: start, bind, race, then write success.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from demo_api.jobs.hermes_run import run_bound
from demo_api.jobs.runner import API_RESTARTED
from demo_api.jobs.runner import CANCELLED_BY_USER
from demo_api.jobs.runner import JobFailed
from demo_api.jobs.runner import JobRunner
from demo_api.jobs.runner import QueueFullError
from demo_api.jobs.runner import RunnerUnavailableError
from demo_api.jobs.store import Job
from demo_api.jobs.store import JobExistsError
from demo_api.jobs.store import JobStatus
from demo_api.jobs.store import JobStore


@dataclass(frozen=True)
class Binding:
    run_id: str


class FakeLifecycle:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.running: list[str] = []
        self.start_errors: dict[str, Exception] = {}
        self.stop_error: Exception | None = None
        self._outcomes: dict[str, asyncio.Future[str]] = {}

    async def start(self, job_id: str) -> Binding:
        if error := self.start_errors.get(job_id):
            raise error
        self._outcomes[job_id] = asyncio.get_running_loop().create_future()
        return Binding(run_id=f"run-{job_id}")

    async def collect(self, binding: Binding, *, on_event) -> str:
        self.running.append(binding.run_id)
        try:
            await on_event({"type": "run.created"})
            return await self._outcomes[binding.run_id.removeprefix("run-")]
        except asyncio.CancelledError:
            self.calls.append("collector-cancelled")
            self.calls.append("stop:collector cancelled")  # HermesRunLifecycle._best_effort_stop
            raise
        finally:
            self.running.remove(binding.run_id)

    async def cancel(self, binding: Binding, *, reason: str) -> str:
        self.calls.append(f"stop:{reason}")
        if self.stop_error is not None:
            raise self.stop_error
        return "cancelled"

    def finish(self, job_id: str, status: str = "completed") -> None:
        self._outcomes[job_id].set_result(status)


class FakeHermesExecutor:
    def __init__(self, store: JobStore, lifecycle: FakeLifecycle, *, heartbeat_seconds: float = 15.0) -> None:
        self.store = store
        self.lifecycle = lifecycle
        self.heartbeat_seconds = heartbeat_seconds
        self.heartbeat_error: Exception | None = None
        self.started: list[str] = []
        self.stopped_detached: list[str] = []

    async def run(self, job: Job, cancelled: asyncio.Event) -> None:
        self.started.append(job.job_id)
        binding = await self.lifecycle.start(job.job_id)
        await self.store.bind_run(job.job_id, binding.run_id)

        async def on_event(event: dict) -> None:
            await self.store.append_event(job.job_id, event)

        async def on_heartbeat() -> None:
            if self.heartbeat_error is not None:
                raise self.heartbeat_error
            await self.store.append_event(job.job_id, {"type": "run.heartbeat"})

        status = await run_bound(
            self.lifecycle,
            binding,
            cancelled=cancelled,
            on_event=on_event,
            on_heartbeat=on_heartbeat,
            heartbeat_seconds=self.heartbeat_seconds,
        )
        if status is None:
            return
        if status != "completed":
            raise JobFailed("Hermes could not complete this request; please retry", "HermesRunFailure")
        final_report = {"type": "artifact.update", "data": {"output_category": "final_report", "content": "ok"}}
        await self.store.transition(
            job.job_id,
            expected={JobStatus.RUNNING},
            to=JobStatus.SUCCESS,
            output={"report": "ok"},
            events=[final_report],
        )

    async def stop_detached(self, job: Job) -> None:
        self.stopped_detached.append(job.hermes_run_id)


@dataclass
class Harness:
    store: JobStore
    lifecycle: FakeLifecycle
    executor: FakeHermesExecutor
    runner: JobRunner

    async def status(self, job_id: str) -> JobStatus:
        return (await self.store.get(job_id)).status

    async def event_types(self, job_id: str) -> list[str]:
        return [event["type"] for event in await self.store.events(job_id)]


async def eventually(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


async def eventually_status(harness: Harness, job_id: str, status: JobStatus) -> Job:
    async with asyncio.timeout(5.0):
        while (job := await harness.store.get(job_id)) is None or job.status != status:
            await asyncio.sleep(0.01)
    return job


async def make_harness(tmp_path, **runner_options) -> Harness:
    store = JobStore(tmp_path / "jobs.db")
    lifecycle = FakeLifecycle()
    executor = FakeHermesExecutor(store, lifecycle, heartbeat_seconds=runner_options.pop("heartbeat_seconds", 15.0))
    options = {"max_active": 1, "max_queued": 4, "job_deadline_seconds": 5.0, "shutdown_grace_seconds": 2.0}
    runner = JobRunner(store, executor, **(options | runner_options))
    await runner.start()
    return Harness(store, lifecycle, executor, runner)


@pytest.fixture
async def harness(tmp_path):
    harness = await make_harness(tmp_path)
    yield harness
    await harness.runner.shutdown()


# Concurrency and queuing (prototype: DASK_NWORKERS=1, DASK_NTHREADS=1; anonymous admission cap of 5)


async def test_jobs_run_one_at_a_time_in_submission_order(harness):
    for job_id in ("a", "b", "c"):
        await harness.runner.submit(job_id, {"question": job_id})

    await eventually(lambda: harness.lifecycle.running == ["run-a"])
    assert await harness.status("b") == JobStatus.SUBMITTED
    harness.lifecycle.finish("a")
    await eventually(lambda: harness.lifecycle.running == ["run-b"])
    harness.lifecycle.finish("b")
    await eventually(lambda: harness.lifecycle.running == ["run-c"])
    harness.lifecycle.finish("c")

    await eventually_status(harness, "c", JobStatus.SUCCESS)
    assert harness.executor.started == ["a", "b", "c"]
    assert (await harness.event_types("c"))[-1] == "artifact.update"  # committed with the success status


async def test_sixth_live_job_is_rejected_until_a_slot_frees(harness):
    for n in range(5):
        await harness.runner.submit(f"job-{n}", {})

    with pytest.raises(QueueFullError):
        await harness.runner.submit("job-5", {})
    assert await harness.store.get("job-5") is None

    assert await harness.runner.cancel("job-4")
    await harness.runner.submit("job-5", {})


async def test_duplicate_job_id_is_rejected_without_touching_the_existing_job(harness):
    await harness.runner.submit("a", {"question": "original"})
    with pytest.raises(JobExistsError):
        await harness.runner.submit("a", {"question": "impostor"})

    await eventually(lambda: harness.lifecycle.running == ["run-a"])
    harness.lifecycle.finish("a")
    await eventually_status(harness, "a", JobStatus.SUCCESS)
    with pytest.raises(JobExistsError):
        await harness.runner.submit("a", {"question": "impostor"})

    job = await harness.store.get("a")
    assert (job.status, job.request) == (JobStatus.SUCCESS, {"question": "original"})


# Cancellation (prototype: cancel route + 1 s DB-polled monitor + lifecycle.cancel; Dask cancel only drops queued work)


async def test_cancelled_queued_job_never_starts(harness):
    await harness.runner.submit("a", {})
    await harness.runner.submit("b", {})
    await eventually(lambda: harness.lifecycle.running == ["run-a"])

    assert await harness.runner.cancel("b")
    harness.lifecycle.finish("a")
    await harness.runner.submit("c", {})
    await eventually(lambda: harness.lifecycle.running == ["run-c"])

    assert harness.executor.started == ["a", "c"]
    job = await harness.store.get("b")
    assert (job.status, job.error) == (JobStatus.INTERRUPTED, CANCELLED_BY_USER)
    assert await harness.event_types("b") == ["job.cancellation_requested"]


async def test_cancelling_a_running_job_stops_hermes_before_abandoning_its_stream(harness):
    await harness.runner.submit("a", {})
    await eventually(lambda: harness.lifecycle.running == ["run-a"])

    assert await harness.runner.cancel("a")
    await eventually(lambda: "collector-cancelled" in harness.lifecycle.calls)

    calls = harness.lifecycle.calls
    assert calls.index("stop:stop requested") < calls.index("collector-cancelled")
    job = await harness.store.get("a")
    assert (job.status, job.error) == (JobStatus.INTERRUPTED, CANCELLED_BY_USER)
    assert "job.cancellation_requested" in await harness.event_types("a")


async def test_cancel_stays_authoritative_when_hermes_does_not_confirm_the_stop(harness):
    harness.lifecycle.stop_error = RuntimeError("gateway unavailable")
    await harness.runner.submit("a", {})
    await eventually(lambda: harness.lifecycle.running == ["run-a"])

    assert await harness.runner.cancel("a")
    await harness.runner.submit("b", {})
    await eventually(lambda: harness.lifecycle.running == ["run-b"])

    assert await harness.status("a") == JobStatus.INTERRUPTED


async def test_finished_job_cannot_be_cancelled(harness):
    await harness.runner.submit("a", {})
    await eventually(lambda: harness.lifecycle.running == ["run-a"])
    harness.lifecycle.finish("a")
    await eventually_status(harness, "a", JobStatus.SUCCESS)

    assert not await harness.runner.cancel("a")
    assert await harness.status("a") == JobStatus.SUCCESS
    assert "job.cancellation_requested" not in await harness.event_types("a")


async def test_late_success_cannot_resurrect_a_cancelled_job(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    await store.create("a", {})
    assert await store.transition("a", expected={JobStatus.SUBMITTED}, to=JobStatus.RUNNING)
    assert await store.transition("a", expected={JobStatus.RUNNING}, to=JobStatus.INTERRUPTED, error=CANCELLED_BY_USER)

    assert not await store.transition(
        "a", expected={JobStatus.RUNNING}, to=JobStatus.SUCCESS, output={"report": "late"}
    )
    job = await store.get("a")
    assert (job.status, job.output) == (JobStatus.INTERRUPTED, None)


# Timeouts, budgets and failures (prototype: Hermes wall/idle budgets, ghost reaper, generic except in the Dask task)


async def test_job_past_its_deadline_fails_and_its_hermes_run_is_stopped(tmp_path):
    harness = await make_harness(tmp_path, job_deadline_seconds=0.2)
    try:
        await harness.runner.submit("a", {})
        job = await eventually_status(harness, "a", JobStatus.FAILURE)

        assert job.error == "The job exceeded its deadline; please retry."
        assert "stop:collector cancelled" in harness.lifecycle.calls
        assert (await harness.event_types("a"))[-1] == "job.error"
    finally:
        await harness.runner.shutdown()


async def test_progress_budget_failure_stops_the_run_and_fails_the_job(tmp_path):
    harness = await make_harness(tmp_path, heartbeat_seconds=0.02)
    harness.executor.heartbeat_error = JobFailed(
        "Hermes stopped after exceeding the configured idle budget", "HermesRunBudgetExceeded"
    )
    try:
        await harness.runner.submit("a", {})
        job = await eventually_status(harness, "a", JobStatus.FAILURE)

        assert job.error == "Hermes stopped after exceeding the configured idle budget"
        assert "stop:progress check failed" in harness.lifecycle.calls
    finally:
        await harness.runner.shutdown()


async def test_silent_run_emits_progress_heartbeats(tmp_path):
    harness = await make_harness(tmp_path, heartbeat_seconds=0.02)
    try:
        await harness.runner.submit("a", {})
        async with asyncio.timeout(5.0):
            while (await harness.event_types("a")).count("run.heartbeat") < 2:
                await asyncio.sleep(0.01)

        harness.lifecycle.finish("a")
        await eventually_status(harness, "a", JobStatus.SUCCESS)
    finally:
        await harness.runner.shutdown()


async def test_failed_hermes_run_is_recorded_with_its_public_message(harness):
    await harness.runner.submit("a", {})
    await eventually(lambda: harness.lifecycle.running == ["run-a"])
    harness.lifecycle.finish("a", status="failed")

    job = await eventually_status(harness, "a", JobStatus.FAILURE)
    assert job.error == "Hermes could not complete this request; please retry"


async def test_unexpected_error_fails_the_job_safely_and_the_worker_keeps_going(harness):
    harness.lifecycle.start_errors["a"] = ValueError("internal detail that must not leak")
    await harness.runner.submit("a", {})
    await harness.runner.submit("b", {})

    job = await eventually_status(harness, "a", JobStatus.FAILURE)
    assert job.error == "Job failed (ValueError); please retry."
    await eventually(lambda: harness.lifecycle.running == ["run-b"])


# Restart recovery and shutdown (prototype: ghost reaper + reconcile_stale_hermes_job; Dask worker close)


async def test_restart_fails_orphaned_jobs_immediately_and_stops_their_hermes_runs(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    await store.create("was-running", {})
    await store.transition("was-running", expected={JobStatus.SUBMITTED}, to=JobStatus.RUNNING)
    await store.bind_run("was-running", "run-was-running")
    await store.create("was-queued", {})
    await store.create("finished", {})
    await store.transition("finished", expected={JobStatus.SUBMITTED}, to=JobStatus.RUNNING)
    await store.transition("finished", expected={JobStatus.RUNNING}, to=JobStatus.SUCCESS, output={"report": "ok"})

    lifecycle = FakeLifecycle()
    executor = FakeHermesExecutor(store, lifecycle)
    runner = JobRunner(store, executor)
    await runner.start()
    try:
        assert executor.stopped_detached == ["run-was-running"]
        for job_id in ("was-running", "was-queued"):
            job = await store.get(job_id)
            assert (job.status, job.error) == (JobStatus.FAILURE, API_RESTARTED)
            assert (await store.events(job_id))[-1]["data"]["error_type"] == "ApiRestarted"
        assert (await store.get("finished")).status == JobStatus.SUCCESS
        assert executor.started == []

        await runner.submit("new", {})
        await eventually(lambda: lifecycle.running == ["run-new"])
    finally:
        await runner.shutdown()


async def test_shutdown_stops_the_running_hermes_run_and_fails_queued_jobs(tmp_path):
    harness = await make_harness(tmp_path)
    await harness.runner.submit("a", {})
    await harness.runner.submit("b", {})
    await eventually(lambda: harness.lifecycle.running == ["run-a"])

    await harness.runner.shutdown()

    assert "stop:stop requested" in harness.lifecycle.calls
    assert harness.executor.started == ["a"]
    for job_id in ("a", "b"):
        job = await harness.store.get(job_id)
        assert (job.status, job.error) == (JobStatus.FAILURE, API_RESTARTED)
    assert not harness.runner.healthy
    with pytest.raises(RunnerUnavailableError):
        await harness.runner.submit("c", {})


# Isolation and retention (prototype: separate Dask worker process; NAT expiry + event cleanup loop)


async def test_store_io_runs_off_the_event_loop(tmp_path, monkeypatch):
    store = JobStore(tmp_path / "jobs.db")
    run_sync = store._run_sync

    def slow_run_sync(operation):
        time.sleep(0.5)  # a blocking call on the loop would freeze the ticker for this long
        return run_sync(operation)

    monkeypatch.setattr(store, "_run_sync", slow_run_sync)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticking = asyncio.create_task(ticker())
    await store.create("a", {})
    ticking.cancel()

    assert ticks >= 5


async def test_retention_deletes_finished_jobs_with_their_events_but_never_live_ones(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    await store.create("finished", {})
    await store.append_event("finished", {"type": "run.created"})
    await store.transition("finished", expected={JobStatus.SUBMITTED}, to=JobStatus.FAILURE, error="x")
    await store.create("live", {})

    assert await store.delete_finished_before(time.time() + 1) == 1
    assert await store.get("finished") is None
    assert await store.events("finished") == []
    assert (await store.get("live")).status == JobStatus.SUBMITTED
