# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The HTTP API: AI-Q's documents contract (collections, uploads, job status) plus pack sync.

Uploads stream straight to disk: each multipart part is hashed and written as it arrives, and a part over the size
limit stops being written (it fails ``too_large``) while the rest of the request is read. Identical bytes in the
workspace are one file (``file_id = "f-" + sha256[:16]``); re-uploading them returns the existing file, except a
failed one, which is ingested again. Errors are ``{"error": {"code", "message"}}``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import urllib.parse
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import BinaryIO

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pydantic import Field
from python_multipart.multipart import MultipartParser
from python_multipart.multipart import parse_options_header

from . import packs
from .catalog import Catalog
from .detect import detect
from .detect import kind_of
from .detect import stored_name
from .models import FileStatus
from .models import IngestError
from .models import Stage
from .pipeline import WORKSPACE
from .pipeline import WORKSPACE_SOURCES
from .pipeline import Pipeline
from .settings import Settings
from .store import JobStore

logger = logging.getLogger(__name__)

HEALTH_TIMEOUT = 3.0
MULTIPART_OVERHEAD = 2**20  # boundaries and part headers


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class CollectionCreate(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    description: str | None = Field(default=None, max_length=600)


class FileIds(BaseModel):
    file_ids: list[str] = Field(max_length=1000)


@dataclass
class Received:
    file_name: str
    path: Path
    sha256: str
    size: int
    too_large: bool


def _file_name(disposition: dict[bytes, bytes]) -> str | None:
    """The part's file name without any directory, from filename* (RFC 5987) or filename."""
    if b"filename*" in disposition:
        raw = disposition[b"filename*"].decode("latin-1")
        charset, _, rest = raw.partition("'")
        name = urllib.parse.unquote(rest.partition("'")[2], encoding=charset or "utf-8", errors="replace")
    elif b"filename" in disposition:
        name = disposition[b"filename"].decode("utf-8", errors="replace")
    else:
        return None
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c.isprintable()).strip()[:255]
    return name or "upload"


async def receive_uploads(request: Request, directory: Path, *, max_files: int, max_bytes: int) -> list[Received]:
    """Stream every ``files`` part of a multipart body to ``directory``, off the event loop.

    The whole body is bounded (every file at its limit, plus room for the multipart framing); the parser bounds each
    part's headers.
    """
    content_type, params = parse_options_header(request.headers.get("content-type", ""))
    if content_type != b"multipart/form-data" or b"boundary" not in params:
        raise ApiError(400, "bad_request", "Send the files as multipart/form-data, in a field named files.")
    max_request = max_files * max_bytes + MULTIPART_OVERHEAD
    too_big = ApiError(413, "request_too_large", f"An upload may total at most {max_request / 2**20:.0f} MB.")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_request:
        raise too_big
    directory.mkdir(parents=True, exist_ok=True)
    received: list[Received] = []
    state: dict[str, Any] = {"headers": {}, "field": b"", "value": b"", "part": None, "count": 0}

    def on_part_begin() -> None:
        state["headers"] = {}

    def on_header_field(data: bytes, start: int, end: int) -> None:
        state["field"] += data[start:end]

    def on_header_value(data: bytes, start: int, end: int) -> None:
        state["value"] += data[start:end]

    def on_header_end() -> None:
        state["headers"][state["field"].lower()] = state["value"]
        state["field"], state["value"] = b"", b""

    def on_headers_finished() -> None:
        _, disposition = parse_options_header(state["headers"].get(b"content-disposition", b""))
        name = _file_name(disposition)
        if disposition.get(b"name") != b"files" or name is None:
            return
        state["count"] += 1
        if state["count"] > max_files:
            return
        path = directory / f"{uuid.uuid4().hex}.part"
        state["part"] = {"name": name, "path": path, "file": path.open("wb"), "hash": hashlib.sha256(), "size": 0}

    def on_part_data(data: bytes, start: int, end: int) -> None:
        part = state["part"]
        if part is None:
            return
        chunk = data[start:end]
        part["hash"].update(chunk)
        part["size"] += len(chunk)
        file: BinaryIO | None = part["file"]
        if file is not None:
            if part["size"] > max_bytes:  # keep hashing for a stable id; stop storing
                file.close()
                part["file"] = None
            else:
                file.write(chunk)

    def on_part_end() -> None:
        part = state["part"]
        if part is None:
            return
        if part["file"] is not None:
            part["file"].close()
        too_large = part["size"] > max_bytes
        if too_large:
            part["path"].unlink(missing_ok=True)
        received.append(Received(part["name"], part["path"], part["hash"].hexdigest(), part["size"], too_large))
        state["part"] = None

    parser = MultipartParser(
        params[b"boundary"],
        {
            "on_part_begin": on_part_begin,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_headers_finished": on_headers_finished,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
        },
    )
    total = 0
    try:
        async for chunk in request.stream():
            total += len(chunk)
            if total > max_request:
                raise too_big
            await asyncio.to_thread(parser.write, chunk)
        await asyncio.to_thread(parser.finalize)
    except Exception as error:
        _discard(received, state)
        if isinstance(error, ApiError):
            raise
        raise ApiError(400, "bad_request", f"The upload could not be read: {error}") from error
    if state["count"] > max_files:
        _discard(received, state)
        raise ApiError(
            400, "too_many_files", f"Upload at most {max_files} files at a time; this request had {state['count']}."
        )
    return received


def _discard(received: list[Received], state: dict[str, Any]) -> None:
    part = state.get("part")
    if part is not None:
        if part["file"] is not None:
            part["file"].close()
        part["path"].unlink(missing_ok=True)
    for item in received:
        item.path.unlink(missing_ok=True)


def create_app(
    settings: Settings,
    *,
    embedder: Any = None,
    index: Any = None,
    parse_probe: Any = None,
    tokenizer: Any = None,
) -> FastAPI:
    catalog = Catalog(settings.knowledge_dir, settings.catalog_schema_dir)
    store = JobStore(settings.db_path)
    pipeline = Pipeline(
        settings, catalog, store, embedder=embedder, index=index, parse_probe=parse_probe, tokenizer=tokenizer
    )
    progress = packs.SyncProgress()
    uploads_dir = settings.knowledge_dir / "ingest" / "uploads"

    def start_pack_sync() -> bool:
        with progress.lock:
            if progress.running:
                return False
            progress.running = True  # claimed here, so two requests cannot both start one

        def run() -> None:
            try:
                packs.sync_all(settings, catalog, pipeline, progress)
            except Exception:
                logger.exception("pack sync failed")
                with progress.lock:
                    progress.running = False

        # A daemon thread, not the loop's executor: shutting down never waits for a sync to finish.
        threading.Thread(target=run, name="pack-sync", daemon=True).start()
        return True

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store.ensure_collection(WORKSPACE, "Your uploaded files")
        for leftover in uploads_dir.glob("*.part") if uploads_dir.is_dir() else []:
            leftover.unlink(missing_ok=True)
        pipeline.start()
        if settings.packs_dir.is_dir():
            start_pack_sync()
        yield
        await pipeline.stop()

    app = FastAPI(title="NVIDIA Knowledge Foundation ingest", lifespan=lifespan)
    app.state.pipeline = pipeline

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse({"error": {"code": error.code, "message": error.message}}, status_code=error.status)

    @app.get("/health")
    async def health() -> JSONResponse:
        def ping() -> bool:  # connecting to Milvus happens in the thread too
            return pipeline.index.ping()

        catalog_ok = await asyncio.to_thread(catalog.writable)
        try:
            milvus_ok = await asyncio.wait_for(asyncio.to_thread(ping), HEALTH_TIMEOUT)
        except Exception:
            milvus_ok = False
        ready = catalog_ok and milvus_ok
        body = {"status": "ready" if ready else "starting", "catalog": catalog_ok, "milvus": milvus_ok}
        return JSONResponse(body, status_code=200 if ready else 503)

    # Collections

    def collection_or_404(name: str) -> dict[str, Any]:
        collection = store.collection(name)
        if collection is None:
            raise ApiError(404, "not_found", f"No collection named {name!r}.")
        return collection

    @app.get("/v1/collections")
    async def list_collections() -> dict[str, Any]:
        return {"collections": store.collections()}

    @app.post("/v1/collections")
    async def create_collection(body: CollectionCreate) -> JSONResponse:
        created = store.ensure_collection(body.name, body.description)
        return JSONResponse(collection_or_404(body.name), status_code=201 if created else 200)

    @app.get("/v1/collections/{name}")
    async def get_collection(name: str) -> dict[str, Any]:
        return collection_or_404(name)

    @app.delete("/v1/collections/{name}")
    async def delete_collection(name: str) -> dict[str, Any]:
        collection_or_404(name)
        if name == WORKSPACE:  # the upload collection stays; its files go
            file_ids = [f["file_id"] for f in store.files(WORKSPACE)]
            await asyncio.to_thread(pipeline.delete_uploads, file_ids)
            return {"message": f"Deleted {len(file_ids)} files from {name}."}
        store.delete_collection(name)
        return {"message": f"Deleted collection {name}."}

    # Files

    @app.post("/v1/collections/{name}/documents")
    async def upload_files(name: str, request: Request) -> dict[str, Any]:
        collection_or_404(name)
        if name != WORKSPACE:
            raise ApiError(400, "bad_request", f"Only the {WORKSPACE} collection accepts uploads.")
        received = await receive_uploads(
            request, uploads_dir, max_files=settings.max_files, max_bytes=settings.max_file_mb * 2**20
        )
        if not received:
            raise ApiError(400, "bad_request", "The request has no files (send them in a field named files).")
        return await asyncio.to_thread(_accept, received)

    def _accept(received: list[Received]) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        existing: list[str] = []
        file_ids: list[str] = []
        for part in received:
            file_id = f"f-{part.sha256[:16]}"
            if file_id in file_ids:  # the same bytes twice in one request
                part.path.unlink(missing_ok=True)
                continue
            file_ids.append(file_id)
            previous = store.get_file(file_id)
            if previous is not None and previous["status"] != FileStatus.FAILED:
                part.path.unlink(missing_ok=True)
                existing.append(file_id)
                continue
            if previous is not None:  # a failed file uploaded again is ingested again
                pipeline.delete_uploads([file_id])
            entries.append(_store_part(part, file_id))
        job_id = store.create_job(WORKSPACE, entries, existing=existing)
        pipeline.enqueue([e["file_id"] for e in entries if e["stage"] == Stage.RECEIVED])
        for source_id in {e["source_id"] for e in entries if e["source_id"]}:
            pipeline.refresh_workspace_source(source_id)
        queued = sum(1 for e in entries if e["stage"] == Stage.RECEIVED)
        return {
            "job_id": job_id,
            "file_ids": file_ids,
            "message": f"{queued} queued, {len(existing)} already in the workspace, {len(entries) - queued} refused.",
        }

    def _store_part(part: Received, file_id: str) -> dict[str, Any]:
        kind = kind_of(part.file_name)
        entry: dict[str, Any] = {
            "file_id": file_id,
            "file_name": part.file_name,
            "sha256": part.sha256,
            "size_bytes": part.size,
            "kind": kind,
            "source_id": WORKSPACE_SOURCES[kind]["id"] if kind else None,
            "stage": Stage.RECEIVED,
        }
        try:
            if kind is None:
                detect(part.path, part.file_name)  # raises unsupported_type with the accepted list
            if part.too_large:
                limit = settings.max_file_mb
                raise IngestError("too_large", f"{part.file_name} is larger than the {limit} MB upload limit.")
        except IngestError as error:
            part.path.unlink(missing_ok=True)
            return {**entry, "stage": Stage.FAILED, "error_code": error.code, "error_message": error.message}
        target = catalog.source_dir(entry["source_id"]) / "files" / stored_name(file_id, part.file_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(part.path, 0o644)
        os.replace(part.path, target)
        return entry

    @app.get("/v1/collections/{name}/documents")
    async def list_files(name: str) -> dict[str, Any]:
        collection_or_404(name)
        return {"files": store.files(name)}

    @app.delete("/v1/collections/{name}/documents")
    async def delete_files(name: str, body: FileIds) -> dict[str, Any]:
        collection_or_404(name)
        deleted = await asyncio.to_thread(pipeline.delete_uploads, body.file_ids) if name == WORKSPACE else []
        return {"message": f"Deleted {len(deleted)} files.", "deleted": [row["file_id"] for row in deleted]}

    @app.get("/v1/documents/{job_id}/status")
    async def job_status(job_id: str) -> dict[str, Any]:
        status = store.job_status(job_id)
        if status is None:
            raise ApiError(404, "not_found", f"No ingestion job {job_id!r}.")
        return status

    # Packs

    @app.post("/v1/packs/sync")
    async def sync_packs() -> JSONResponse:
        if not settings.packs_dir.is_dir():
            raise ApiError(404, "not_found", f"{settings.packs_dir} does not exist.")
        started = start_pack_sync()
        return JSONResponse({"status": "started" if started else "running"}, status_code=202)

    @app.get("/v1/packs/status")
    async def packs_status() -> dict[str, Any]:
        return {"packs": progress.snapshot(), "running": progress.running}

    return app
