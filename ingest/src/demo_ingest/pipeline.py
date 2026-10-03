# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The pipeline per file, the worker pool that runs uploads through it, and the workspace's catalog entries.

``Pipeline.run_file`` takes one stored file through its stages (detect, then convert/chunk/embed/index for a document,
or load for a table) and reports each stage through a callback; uploads and industry packs share it. Each stage runs
under ``INGEST_STAGE_TIMEOUT_SECONDS``; every stage is idempotent (delete-then-insert, CREATE OR REPLACE), so a file
can always run again from the start.

Uploads: ``INGEST_WORKERS`` asyncio tasks take file ids from a queue in upload order and run each file in a thread
pool. After each file, its source manifest is rewritten. The workspace's two sources materialize with their first
file (``workspace.documents``, ``workspace.tables``) and disappear with their last.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from . import documents
from . import tables
from .catalog import Catalog
from .detect import detect
from .detect import stored_name
from .documents import probe_parse
from .embed import Embedder
from .models import FileStatus
from .models import IngestError
from .models import Stage
from .models import status_for
from .models import utcnow
from .settings import Settings
from .store import JobStore

logger = logging.getLogger(__name__)

WORKSPACE = "workspace"
WORKSPACE_SOURCES: dict[str, dict[str, Any]] = {
    "document": {
        "id": "workspace.documents",
        "name": "Your documents",
        "description": "Documents you uploaded, parsed with NVIDIA Nemotron Parse.",
        "agent_description": (
            "Documents the user uploaded in this workspace (PDFs, scans, Office files, web pages and text), parsed "
            "with NVIDIA Nemotron Parse and docling. Cite them by file name and page."
        ),
        "kind": "documents",
        "capabilities": ["unstructured_retrieval"],
    },
    "table": {
        "id": "workspace.tables",
        "name": "Your tables",
        "description": "Spreadsheets and data files you uploaded, loaded into DuckDB.",
        "agent_description": (
            "Tables the user uploaded in this workspace (CSV, Excel, Parquet, JSON), one DuckDB table per file or "
            "sheet, with profiled keys and time columns."
        ),
        "kind": "structured",
        "capabilities": ["structured_retrieval"],
    },
}
WORKSPACE_PACK = {
    "schema_version": "1",
    "id": WORKSPACE,
    "kind": "workspace",
    "title": "Your data",
    "description": "Files you upload, parsed with NVIDIA Nemotron Parse and loaded into DuckDB.",
    "icon": "Upload",
    "questions": [],
    "examples": [],
    "conversations": [],
}
Update = Callable[..., None]


@dataclass
class Outcome:
    """How one file ended: ``stage`` is ready or failed."""

    stage: str
    kind: str | None = None
    parser: str | None = None
    pages: int | None = None
    chunks: int = 0
    tables: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    document_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    # Why a PDF used its text layer: "disabled" (PARSE_BASE_URL empty) or "unavailable" (down, failing or slow).
    fallback: str | None = None

    @property
    def ready(self) -> bool:
        return self.stage == Stage.READY


def database_path(catalog: Catalog, source_id: str) -> Path:
    return catalog.source_dir(source_id) / "tables.duckdb"


def database_alias(source_id: str) -> str:
    return source_id.replace(".", "_").replace("-", "_")


def file_entry(row: dict[str, Any]) -> dict[str, Any]:
    """A FileEntry of the source manifest, from a job-store row or an Outcome-shaped dict."""
    status = {FileStatus.SUCCESS: "ready", FileStatus.FAILED: "failed"}.get(row.get("status"), "ingesting")
    entry: dict[str, Any] = {
        "file_id": row["file_id"],
        "file_name": row["file_name"][:255],
        "sha256": row["sha256"],
        "size_bytes": row["size_bytes"],
        "status": status,
        "warnings": [w[:500] for w in row.get("warnings") or []][:20],
        "error_message": (row.get("error_message") or None) and row["error_message"][:600],
    }
    for key in ("parser", "document_id", "pages"):
        if row.get(key) is not None:
            entry[key] = row[key]
    if row.get("kind") == "document":
        entry["chunks"] = row.get("chunks") or 0
    if row.get("tables"):
        entry["tables"] = list(row["tables"])
    return entry


def source_status(entries: list[dict[str, Any]]) -> str:
    statuses = {entry["status"] for entry in entries}
    if not statuses:
        return "empty"
    if "ingesting" in statuses:
        return "ingesting"
    return "ready" if "ready" in statuses else "failed"


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        catalog: Catalog,
        store: JobStore | None = None,
        *,
        embedder: Any = None,
        index: Any = None,
        parse_probe: Callable[[Settings], str | None] | None = None,
        tokenizer: Any = None,
    ) -> None:
        self.settings = settings
        self.catalog = catalog
        self.store = store
        self.embedder = embedder or Embedder(settings)
        self.probe = parse_probe or probe_parse
        self._index = index
        self._tokenizer = tokenizer
        self._index_lock = threading.Lock()
        self._source_locks: defaultdict[str, threading.RLock] = defaultdict(threading.RLock)
        self._workspace_lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=max(1, settings.workers), thread_name_prefix="ingest")
        self._queue: asyncio.Queue[str] | None = None
        self._workers: list[asyncio.Task] = []

    # Lazily built clients: Milvus and the tokenizer are only needed for documents.

    @property
    def index(self) -> Any:
        with self._index_lock:
            if self._index is None:
                from .index import KnowledgeIndex

                try:
                    self._index = KnowledgeIndex(self.settings.milvus_uri, self.settings.collection_alias)
                except Exception as error:
                    raise IngestError("index_unavailable", f"Milvus did not answer: {error}"[:600]) from error
            return self._index

    @property
    def tokenizer(self) -> Any:
        if self._tokenizer is None:
            self._tokenizer = documents.load_tokenizer(self.settings.tokenizer_dir)
        return self._tokenizer

    def source_lock(self, source_id: str) -> threading.RLock:
        return self._source_locks[source_id]

    # One file

    def run_stage(self, stage: str, work: Callable[[], Any]) -> Any:
        """Run one stage in a thread of its own and give up on it after the stage timeout."""
        outcome: dict[str, Any] = {}

        def run() -> None:
            try:
                outcome["value"] = work()
            except BaseException as error:  # handed back to the caller's thread
                outcome["error"] = error

        worker = threading.Thread(target=run, name=f"stage-{stage}", daemon=True)
        worker.start()
        worker.join(self.settings.stage_timeout_s)
        if worker.is_alive():
            raise IngestError(
                "timeout", f"The {stage} stage did not finish within {self.settings.stage_timeout_s:.0f} s."
            )
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("value")

    def run_file(
        self,
        *,
        source_id: str,
        path: Path,
        file_name: str,
        file_id: str,
        update: Update,
        expect_kind: str | None = None,
        db_path: Path | None = None,
        existing_tables: set[str] | None = None,
    ) -> Outcome:
        """Take one stored file through its stages; never raises for a file that cannot be ingested."""
        kind = None
        try:
            detected = self.run_stage(
                Stage.DETECTED, lambda: detect(path, file_name, max_bytes=self.settings.max_file_mb * 2**20)
            )
            kind = detected.kind
            update(stage=Stage.DETECTED, stage_detail=None, progress_percent=5, kind=kind)
            if expect_kind is not None and kind != expect_kind:
                raise IngestError(
                    "type_mismatch", f"{file_name} is a {kind}, but {source_id} only holds {expect_kind}s."
                )
            if kind == "document":
                return self._run_document(
                    source_id, path, file_name=file_name, file_id=file_id, fmt=detected.format, update=update
                )
            return self._run_table(
                path, file_name, update, db_path or database_path(self.catalog, source_id), existing_tables or set()
            )
        except IngestError as error:
            return Outcome(stage=Stage.FAILED, kind=kind, error_code=error.code, error_message=error.message)
        except Exception as error:
            logger.exception("%s (%s) failed unexpectedly", file_name, file_id)
            message = f"Unexpected error: {type(error).__name__}: {error}"[:600]
            return Outcome(stage=Stage.FAILED, kind=kind, error_code="internal_error", error_message=message)

    def _run_document(
        self, source_id: str, path: Path, *, file_name: str, file_id: str, fmt: str, update: Update
    ) -> Outcome:
        document_id = f"{source_id}:{file_id}"
        fallback: dict[str, str] = {}
        if (check := getattr(self.embedder, "check", None)) is not None:
            check()  # no embeddings key: fail before spending Parse time

        def probe(settings: Settings) -> str | None:
            reason = self.probe(settings)
            if reason is not None:
                fallback["kind"] = "unavailable" if settings.parse_enabled else "disabled"
            return reason

        def on_stage(stage: str, detail: str | None, percent: float) -> None:
            update(stage=stage, stage_detail=detail, progress_percent=round(percent, 1))

        result = documents.ingest_document(
            settings=self.settings,
            catalog=self.catalog,
            embedder=self.embedder,
            index=self.index,
            tokenizer=self.tokenizer,
            source_id=source_id,
            document_id=document_id,
            file_id=file_id,
            file_path=path,
            file_name=file_name,
            fmt=fmt,
            on_stage=on_stage,
            run_stage=self.run_stage,
            probe=probe,
        )
        if result.parser == documents.TEXT_LAYER_PARSER and "kind" not in fallback:
            fallback["kind"] = "unavailable"  # Parse answered the probe, then failed or was too slow
        return Outcome(
            stage=Stage.READY,
            kind="document",
            parser=result.parser,
            pages=result.pages,
            chunks=result.chunks,
            warnings=result.warnings,
            document_id=document_id,
            fallback=fallback.get("kind"),
        )

    def _run_table(self, path: Path, file_name: str, update: Update, db_path: Path, existing: set[str]) -> Outcome:
        def on_progress(done: int, total: int) -> None:
            update(
                stage=Stage.LOADING, stage_detail=f"sheet {done} of {total}", progress_percent=10 + 70 * done / total
            )

        update(stage=Stage.LOADING, stage_detail=None, progress_percent=10)
        loaded = self.run_stage(
            Stage.LOADING, lambda: tables.load_table_file(db_path, path, file_name, existing, on_progress)
        )
        return Outcome(
            stage=Stage.READY, kind="table", parser=loaded.parser, tables=loaded.tables, warnings=loaded.warnings
        )

    # Uploads

    def start(self) -> None:
        """Start the workers and re-queue every file a restart interrupted."""
        assert self.store is not None
        self._queue = asyncio.Queue()
        self._workers = [
            asyncio.create_task(self._work(), name=f"ingest-worker-{i}") for i in range(self.settings.workers)
        ]
        sources: set[str] = set()
        for row in self.store.unfinished():
            path = self.stored_path(row)
            if path is None or not path.exists():
                self.store.update_file(
                    row["file_id"],
                    stage=Stage.FAILED,
                    error_code="internal_error",
                    error_message="The upload was interrupted.",
                )
            else:
                self.store.update_file(row["file_id"], stage=Stage.RECEIVED, stage_detail=None, progress_percent=0)
                self._queue.put_nowait(row["file_id"])
            if row["source_id"]:
                sources.add(row["source_id"])
        for source_id in sources:
            self.refresh_workspace_source(source_id)
        self.refresh_workspace_pack()

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._pool.shutdown(wait=False, cancel_futures=True)

    def enqueue(self, file_ids: list[str]) -> None:
        assert self._queue is not None, "start() the pipeline first"
        for file_id in file_ids:
            self._queue.put_nowait(file_id)

    async def idle(self) -> None:
        """Wait until every queued file is done (tests)."""
        assert self._queue is not None
        await self._queue.join()

    async def _work(self) -> None:
        assert self._queue is not None
        loop = asyncio.get_running_loop()
        while True:
            file_id = await self._queue.get()
            try:
                await loop.run_in_executor(self._pool, self.process_upload, file_id)
            except Exception:
                logger.exception("worker failed on %s", file_id)
            finally:
                self._queue.task_done()

    def stored_path(self, row: dict[str, Any]) -> Path | None:
        """``sources/<source>/files/<file_id><ext>``; an original stored without its extension is renamed to it."""
        if not row.get("source_id"):
            return None
        directory = self.catalog.source_dir(row["source_id"]) / "files"
        path = directory / stored_name(row["file_id"], row["file_name"])
        legacy = directory / row["file_id"]
        if path != legacy and not path.exists() and legacy.exists():
            os.replace(legacy, path)
        return path

    def process_upload(self, file_id: str) -> None:
        assert self.store is not None
        row = self.store.get_file(file_id)
        if row is None or row["status"] in (FileStatus.SUCCESS, FileStatus.FAILED):
            return
        source_id = row["source_id"]
        path = self.stored_path(row)
        assert path is not None and source_id is not None

        def update(**fields: Any) -> None:
            self.store.update_file(file_id, **fields)

        with self.source_lock(source_id) if row["kind"] == "table" else nullcontext():
            existing: set[str] = set()
            if row["kind"] == "table":
                # Names taken by the source's other files; this file's own earlier tables are replaced.
                others = [r for r in self.store.source_files(source_id) if r["file_id"] != file_id]
                existing = {name for r in others for name in r["tables"]}
                tables.drop_tables(database_path(self.catalog, source_id), row["tables"])
            outcome = self.run_file(
                source_id=source_id,
                path=path,
                file_name=row["file_name"],
                file_id=file_id,
                update=update,
                existing_tables=existing,
            )
            profile = None
            if outcome.ready and outcome.kind == "table":
                update(stage=Stage.PROFILING, stage_detail=None, progress_percent=85)
                try:
                    # This thread holds the source lock; the stage's own thread profiles under it.
                    db_path = database_path(self.catalog, source_id)
                    profile = self.run_stage(Stage.PROFILING, lambda: tables.profile_database(db_path, None))
                except IngestError as error:
                    tables.drop_tables(db_path, outcome.tables)
                    outcome = Outcome(
                        stage=Stage.FAILED, kind="table", error_code=error.code, error_message=error.message
                    )
            update(
                parser=outcome.parser,
                pages=outcome.pages,
                chunks=outcome.chunks,
                tables=outcome.tables,
                warnings=outcome.warnings,
                document_id=outcome.document_id,
                error_code=outcome.error_code,
                error_message=outcome.error_message,
            )
            # The catalog first, then the job store: a client that sees the file finished finds it in the manifest.
            self.refresh_workspace_source(source_id, profile=profile, final={file_id: status_for(outcome.stage)})
            update(
                stage=outcome.stage,
                stage_detail=None,
                progress_percent=100 if outcome.ready else row["progress_percent"],
            )
            if self.store.get_file(file_id) is None:  # deleted while it ran: undo what it wrote
                self.remove_outputs({**row, "tables": outcome.tables, "document_id": outcome.document_id})
                self.refresh_workspace_source(source_id)

    def profile_workspace_tables(self) -> list[dict[str, Any]]:
        source_id = WORKSPACE_SOURCES["table"]["id"]
        with self.source_lock(source_id):
            return tables.profile_database(database_path(self.catalog, source_id), None)

    def delete_uploads(self, file_ids: list[str]) -> list[dict[str, Any]]:
        assert self.store is not None
        deleted = self.store.delete_files(WORKSPACE, file_ids)
        for row in deleted:
            self.remove_outputs(row)
            path = self.stored_path(row)
            if path is not None:
                path.unlink(missing_ok=True)
        for source_id in {row["source_id"] for row in deleted if row["source_id"]}:
            reprofile = source_id == WORKSPACE_SOURCES["table"]["id"]
            self.refresh_workspace_source(source_id, profile=self.profile_workspace_tables() if reprofile else None)
        return deleted

    def remove_outputs(self, row: dict[str, Any]) -> None:
        """Remove what a file put in the index, the parsed documents and the database."""
        source_id = row.get("source_id")
        if not source_id:
            return
        if row.get("document_id"):
            try:
                self.index.delete_document(row["document_id"])
            except IngestError:
                logger.warning("could not remove %s from the index", row["document_id"])
            directory = self.catalog.source_dir(source_id)
            (directory / "documents" / f"{row['document_id']}.md").unlink(missing_ok=True)
            (directory / "chunks" / f"{row['document_id']}.jsonl").unlink(missing_ok=True)
        if row.get("tables"):
            with self.source_lock(source_id):
                tables.drop_tables(database_path(self.catalog, source_id), row["tables"])

    # Workspace catalog entries

    def refresh_workspace_source(
        self,
        source_id: str,
        *,
        profile: list[dict[str, Any]] | None = None,
        final: dict[str, str] | None = None,
    ) -> None:
        """Rewrite the source manifest from the job store (``final``: statuses about to be stored); remove it with
        its last file."""
        assert self.store is not None
        spec = next(spec for spec in WORKSPACE_SOURCES.values() if spec["id"] == source_id)
        with self.source_lock(source_id):
            rows = [
                {**row, "status": (final or {}).get(row["file_id"], row["status"])}
                for row in self.store.source_files(source_id)
            ]
            if not rows:
                self.catalog.delete_source(source_id)
            else:
                entries = [file_entry(row) for row in rows]
                manifest: dict[str, Any] = {
                    "schema_version": "1",
                    "pack_id": WORKSPACE,
                    **{key: spec[key] for key in ("id", "name", "description", "agent_description", "kind")},
                    "capabilities": list(spec["capabilities"]),
                    "synthetic": False,
                    "default_enabled": True,
                    "example_questions": [],
                    "status": source_status(entries),
                    "updated_at": utcnow(),
                    "files": entries,
                }
                ready = [row for row in rows if row["status"] == FileStatus.SUCCESS]
                if spec["kind"] == "documents":
                    manifest["documents"] = {
                        "count": len(ready),
                        "chunks": sum(row["chunks"] for row in ready),
                        "collection": self.settings.collection_alias,
                        "embed_model": self.settings.embed_model,
                    }
                else:
                    owned = {name for row in ready for name in row["tables"]}
                    if profile is None:
                        previous = self.catalog.read_source(source_id) or {}
                        profile = (previous.get("database") or {}).get("tables", [])
                    manifest["database"] = {
                        "path": f"sources/{source_id}/tables.duckdb",
                        "alias": database_alias(source_id),
                        "tables": [info for info in profile if info["name"] in owned],
                    }
                self.catalog.write_source(manifest)
        self.refresh_workspace_pack()

    def refresh_workspace_pack(self) -> None:
        with self._workspace_lock:
            ids = [spec["id"] for spec in WORKSPACE_SOURCES.values()]
            sources = [m for source_id in ids if (m := self.catalog.read_source(source_id)) is not None]
            statuses = {m["status"] for m in sources}
            if not sources:
                status = "empty"
            elif "ingesting" in statuses:
                status = "ingesting"
            else:
                status = "ready" if "ready" in statuses else "failed"
            self.catalog.write_pack(
                {**WORKSPACE_PACK, "sources": [m["id"] for m in sources], "status": status, "updated_at": utcnow()}
            )
