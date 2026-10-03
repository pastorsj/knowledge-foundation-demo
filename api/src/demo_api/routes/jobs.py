# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""``/v1/jobs/async``: submit a question, follow it, read its answer, cancel it, export it.

Unknown jobs, including ones deleted by retention, answer 404; the UI shows them as unavailable.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC
from datetime import datetime
from typing import Annotated
from typing import Any
from typing import Literal

from fastapi import APIRouter
from fastapi import Header
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pydantic import Field

from demo_api.catalog import PACK_ID
from demo_api.catalog import CatalogUnavailableError
from demo_api.jobs.export import export_turn
from demo_api.jobs.runner import QueueFullError
from demo_api.jobs.runner import RunnerUnavailableError
from demo_api.jobs.store import Job
from demo_api.jobs.store import JobExistsError
from demo_api.jobs.stream import job_stream
from demo_api.jobs.stream import resolve_cursor
from demo_api.phoenix import PhoenixUnavailableError
from demo_api.phoenix import find_trace_id
from demo_api.services import Services
from demo_api.services import ServicesDep

router = APIRouter(prefix="/v1/jobs/async", tags=["jobs"])

MAX_INPUT_CHARS = 32_768
RETRY_AFTER_SECONDS = 30
_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class SubmitRequest(BaseModel):
    agent_type: Literal["hermes"] = "hermes"
    input: str
    job_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    pack_id: str | None = Field(
        default=None,
        pattern=PACK_ID,
        max_length=64,
        description="The pack the question is asked in; inferred when every selected source is of one pack.",
    )
    data_sources: list[str] | None = Field(
        default=None,
        description="Catalog source ids to use; omit for every available source of the pack (of every pack without "
        "one), [] for none.",
    )


@router.post("/submit")
async def submit(
    body: SubmitRequest,
    services: ServicesDep,
    conversation_id: Annotated[str | None, Header(alias="conversation-id")] = None,
) -> dict[str, str]:
    question = body.input.strip()
    if not question:
        raise HTTPException(422, "The question is empty.")
    if len(question) > MAX_INPUT_CHARS:
        raise HTTPException(413, f"The question is longer than {MAX_INPUT_CHARS} characters.")
    if conversation_id is not None and not _CONVERSATION_ID.fullmatch(conversation_id):
        raise HTTPException(422, "The conversation-id header is not a valid id.")
    try:
        available = {source.id: source for source in services.catalog.sources()}
        if body.pack_id is not None and body.pack_id not in {pack.id for pack in services.catalog.packs()}:
            raise HTTPException(422, f"Unknown pack: {body.pack_id}")
    except CatalogUnavailableError as error:
        raise HTTPException(503, str(error)) from error
    if body.data_sources is not None:
        source_ids = list(dict.fromkeys(body.data_sources))
    else:
        source_ids = [id_ for id_, source in available.items() if body.pack_id in (None, source.pack_id)]
    if unknown := [source_id for source_id in source_ids if source_id not in available]:
        detail = {"message": f"Unknown data source(s): {', '.join(unknown)}", "invalid_ids": unknown}
        raise HTTPException(422, detail | {"known_ids": sorted(available)})

    selected = [available[source_id] for source_id in source_ids]
    families = {family for source in selected for family in source.capabilities}
    packs = {source.pack_id for source in selected}
    request: dict[str, Any] = {
        "question": question,
        "pack_id": body.pack_id or (packs.pop() if len(packs) == 1 else None),
        "source_ids": source_ids,
        "conversation_id": conversation_id,
        "catalog": [source.catalog_entry() for source in selected],
        "toolsets": services.registry.toolsets(families, services.settings.features),
        # The agent plugin's execution scope: the first structured source's DuckDB alias, the documents' collection
        "database_name": next((source.database_name for source in selected if source.database_name), None),
        "collection": next((source.collection for source in selected if source.collection), None),
    }
    job_id = body.job_id or str(uuid.uuid4())
    try:
        await services.runner.submit(job_id, request)
    except QueueFullError as error:
        message = "The agent is busy with other questions. Try again shortly."
        raise HTTPException(429, message, headers={"Retry-After": str(RETRY_AFTER_SECONDS)}) from error
    except JobExistsError as error:
        raise HTTPException(409, f"Job already exists: {job_id}") from error
    except RunnerUnavailableError as error:
        raise HTTPException(503, "The API is starting or stopping. Try again shortly.") from error
    return {"job_id": job_id, "status": "submitted"}


@router.get("/job/{job_id}")
async def status(job_id: str, services: ServicesDep) -> dict[str, Any]:
    job = await _job(services, job_id)
    created_at = datetime.fromtimestamp(job.created_at, tz=UTC).isoformat()
    return {"job_id": job_id, "status": job.status, "error": job.error, "created_at": created_at}


@router.get("/job/{job_id}/report")
async def report(job_id: str, services: ServicesDep) -> dict[str, Any]:
    markdown = ((await _job(services, job_id)).output or {}).get("report")
    return {"job_id": job_id, "has_report": bool(markdown), "report": markdown}


@router.post("/job/{job_id}/cancel")
async def cancel(job_id: str, services: ServicesDep) -> dict[str, Any]:
    """Cancel a queued or running job. A running job's Hermes run is stopped before its stream is dropped."""
    await _job(services, job_id)
    if not await services.runner.cancel(job_id):
        job = await _job(services, job_id)
        raise HTTPException(409, f"Job already finished (status: {job.status}).")
    return {"job_id": job_id, "status": "interrupted", "cancelled": True}


@router.get("/job/{job_id}/stream")
async def stream(
    job_id: str,
    services: ServicesDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Server-Sent Events from the start, or after the ``Last-Event-ID`` a reconnecting browser sends."""
    await _job(services, job_id)
    events = job_stream(services.store, job_id, after=resolve_cursor(last_event_id))
    return StreamingResponse(events, media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/job/{job_id}/stream/{last_event_id}")
async def stream_after(job_id: str, last_event_id: str, services: ServicesDep) -> StreamingResponse:
    """Server-Sent Events after a cursor."""
    await _job(services, job_id)
    events = job_stream(services.store, job_id, after=resolve_cursor(last_event_id))
    return StreamingResponse(events, media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/job/{job_id}/trace")
async def trace(job_id: str, services: ServicesDep) -> dict[str, str]:
    """The job's trace in Phoenix; open ``path`` on the Phoenix UI."""
    await _job(services, job_id)
    settings = services.settings
    try:
        trace_id = await find_trace_id(
            services.http, base_url=settings.aiq_phoenix_internal_url, project=settings.phoenix_project, job_id=job_id
        )
    except PhoenixUnavailableError as error:
        raise HTTPException(503, "Phoenix is unavailable.") from error
    if trace_id is None:
        raise HTTPException(404, "Phoenix has no trace for this job yet.")
    return {"job_id": job_id, "trace_id": trace_id, "path": f"/redirects/traces/{trace_id}"}


@router.get("/job/{job_id}/export")
async def export(job_id: str, services: ServicesDep) -> dict[str, Any]:
    """The job as one turn of the v2 recordings bundle (see ``jobs/export.py``)."""
    return await export_turn(services.store, await _job(services, job_id))


async def _job(services: Services, job_id: str) -> Job:
    job = await services.store.get(job_id)
    if job is None:
        raise HTTPException(404, f"Job not found: {job_id}")
    return job
