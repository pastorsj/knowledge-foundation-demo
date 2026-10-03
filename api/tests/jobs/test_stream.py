# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""SSE cursor semantics, as the UI client (ui/src/adapters/api/deep-research-client.ts) relies on them."""

from __future__ import annotations

import json

import pytest

from demo_api.jobs.store import JobStatus
from demo_api.jobs.store import JobStore
from demo_api.jobs.stream import job_stream
from demo_api.jobs.stream import resolve_cursor


def parse(frame: str) -> tuple[str | None, str, dict]:
    fields = dict(line.split(": ", 1) for line in frame.strip().split("\n"))
    return fields.get("id"), fields["event"], json.loads(fields["data"])


@pytest.fixture
async def store(tmp_path) -> JobStore:
    store = JobStore(tmp_path / "jobs.db")
    await store.create("a", {"question": "q"})
    return store


async def test_stream_replays_then_follows_live_and_ends_with_the_final_status(store):
    await store.transition("a", expected={JobStatus.SUBMITTED}, to=JobStatus.RUNNING)
    await store.append_event("a", {"type": "execution.v2", "n": 1})
    frames = job_stream(store, "a", poll_seconds=0.01)

    assert [parse(await anext(frames)) for _ in range(5)] == [
        (None, "stream.start", {"job_id": "a", "after": 0}),
        (None, "stream.mode", {"mode": "polling", "interval_ms": 10}),
        ("1", "execution.v2", {"n": 1}),
        (None, "stream.mode", {"mode": "live"}),
        (None, "job.status", {"status": "running"}),
    ]

    await store.append_event("a", {"type": "execution.v2", "n": 2})
    assert parse(await anext(frames)) == ("2", "execution.v2", {"n": 2})

    report = {"type": "artifact.update", "data": {"output_category": "final_report", "content": "done"}}
    await store.transition("a", expected={JobStatus.RUNNING}, to=JobStatus.SUCCESS, events=[report])
    assert [parse(frame) async for frame in frames] == [
        ("3", "artifact.update", {"data": {"output_category": "final_report", "content": "done"}}),
        (None, "job.status", {"status": "success"}),
    ]


async def test_a_resumed_stream_starts_after_its_cursor_and_says_it_reconnected(store):
    for n in (1, 2, 3):
        await store.append_event("a", {"type": "execution.v2", "n": n})
    await store.transition("a", expected={JobStatus.SUBMITTED}, to=JobStatus.FAILURE, error="boom")

    frames = [parse(frame) async for frame in job_stream(store, "a", after=2)]

    assert frames[2:] == [
        ("3", "execution.v2", {"n": 3}),
        (None, "stream.mode", {"mode": "live"}),
        (None, "job.status", {"status": "failure", "error": "boom", "reconnected": True}),
    ]


@pytest.mark.parametrize(("value", "cursor"), [("5", 5), (" 7 ", 7), ("abc", 0), (None, 0), ("-3", 0)])
def test_untrusted_cursors_fall_back_to_the_start(value, cursor):
    assert resolve_cursor(value) == cursor
