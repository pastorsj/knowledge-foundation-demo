# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""One job as a recorded turn: the unit of the v2 recordings bundle the UI replays.

``GET /v1/jobs/async/job/{id}/export`` returns it, and ``demo-api record`` writes a list of them
into ``sessions/<id>.json``. Its shape::

    {jobId, question, submittedAt, completedAt, status, report: {markdown, citations[]} | null,
     events: [execution.v2, in stream order], receipts: [ReceiptV2], sourceIds}

``report`` is null until the job has an answer. ``sourceIds`` are the sources the question used.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from typing import Any

from demo_api.events import EVENT_STORE_TYPE
from demo_api.events import ExecutionEventV2

from .store import Job
from .store import JobStore

_PAGE = 10_000


async def export_turn(store: JobStore, job: Job) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    cursor = 0
    while page := await store.events(job.job_id, after_id=cursor, limit=_PAGE):
        cursor = page[-1]["_id"]
        events.extend(
            ExecutionEventV2.from_event_store_dict(event).model_dump(mode="json")
            for event in page
            if event.get("type") == EVENT_STORE_TYPE
        )
    output = job.output or {}
    return {
        "jobId": job.job_id,
        "question": job.request["question"],
        "submittedAt": _iso(job.created_at),
        "completedAt": None if job.is_active else _iso(job.updated_at),
        "status": job.status,
        "report": {"markdown": output["report"], "citations": output["citations"]} if "report" in output else None,
        "events": events,
        "receipts": await store.receipts(job.job_id),
        "sourceIds": job.request.get("source_ids", []),
    }


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")
