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

Two locks per source. The build lock makes one writer of a structured source's DuckDB copy at a time; a table file
holds it while it loads and profiles, which can take minutes. The manifest lock is held only while a manifest is
rewritten from the job store, so an upload or a delete never waits for another file's load.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import threading
from collections import defaultdict
from collections.abc import Callable
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
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
from .models import redact
from .models import status_for
from .models import utcnow
from .settings import Settings
from .store import JobStore

logger = logging.getLogger(__name__)

WORKSPACE = "workspace"
# A file whose ingestion was cut short this many times (the service died with it) fails instead of running again.
MAX_ATTEMPTS = 2
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
    # Why Parse was not used: "disabled" (PARSE_BASE_URL empty) or "unavailable" (it did not answer, for the whole
    # file or some pages: down, restarting or slow, so another try may read it). A Parse that answered with an error
    # or no content leaves it None: another try would end the same way.
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
        self._build_locks: defaultdict[str, threading.RLock] = defaultdict(threading.RLock)
        self._manifest_locks: defaultdict[str, threading.RLock] = defaultdict(threading.RLock)
        # The profile of each workspace source's committed DuckDB, every table of it; set under the build lock when a
        # copy is swapped in, so the last one set is the live file's. Manifests list the tables of finished files.
        self._profiles: dict[str, list[dict[str, Any]]] = {}
        self._workspace_lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=max(1, settings.workers), thread_name_prefix="ingest")
        self._queue: asyncio.Queue[str] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
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
                    raise IngestError("index_unavailable", f"Milvus did not answer: {redact(error)}") from error
            return self._index

    @property
    def tokenizer(self) -> Any:
        if self._tokenizer is None:
            self._tokenizer = documents.load_tokenizer(self.settings.tokenizer_dir)
        return self._tokenizer

    def build_lock(self, source_id: str) -> threading.RLock:
        """Held by the one writer of the source's DuckDB copy, from begin_database to end_database."""
        return self._build_locks[source_id]

    def manifest_lock(self, source_id: str) -> threading.RLock:
        """Held while the source's manifest is rewritten; never across a load."""
        return self._manifest_locks[source_id]

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
            fallback = getattr(error, "fallback", None)
            return Outcome(
                stage=Stage.FAILED, kind=kind, error_code=error.code, error_message=error.message, fallback=fallback
            )
        except Exception as error:
            logger.exception("%s (%s) failed unexpectedly", file_name, file_id)
            message = f"Unexpected error: {type(error).__name__}: {redact(error)}"
            return Outcome(stage=Stage.FAILED, kind=kind, error_code="internal_error", error_message=message)

    def _run_document(
        self, source_id: str, path: Path, *, file_name: str, file_id: str, fmt: str, update: Update
    ) -> Outcome:
        document_id = f"{source_id}:{file_id}"
        fallback: dict[str, str] = {}
        if (check := getattr(self.embedder, "check", None)) is not None:
            check()  # no embeddings key: fail before spending Parse time

        def probe(settings: Settings) -> str | None:
            # Only a Parse that is off, or that does not answer at all, is recorded: HTTP errors, empty output and
            # slowness fall back too, but another try would end the same way.
            reason = self.probe(settings)
            if reason is not None:
                fallback["kind"] = "unavailable" if settings.parse_enabled else "disabled"
            return reason

        def on_stage(stage: str, detail: str | None, percent: float) -> None:
            update(stage=stage, stage_detail=detail, progress_percent=round(percent, 1))

        try:
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
        except IngestError as error:
            # An image (or a PDF without text) that Parse could not read says why.
            error.fallback = fallback.get("kind") or ("unavailable" if error.transient else None)
            raise
        return Outcome(
            stage=Stage.READY,
            kind="document",
            parser=result.parser,
            pages=result.pages,
            chunks=result.chunks,
            warnings=result.warnings,
            document_id=document_id,
            fallback=fallback.get("kind") or ("unavailable" if result.transient else None),
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
        self._loop = asyncio.get_running_loop()
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
            elif row["attempts"] >= MAX_ATTEMPTS:  # a file that takes the service down must not do so forever
                self.store.update_file(
                    row["file_id"],
                    stage=Stage.FAILED,
                    claimed=0,
                    error_code="interrupted",
                    error_message="Ingestion was interrupted twice (the service stopped while it ran). "
                    "Upload the file again to retry.",
                )
            else:
                self.store.update_file(
                    row["file_id"], stage=Stage.RECEIVED, stage_detail=None, progress_percent=0, claimed=0
                )
                self._queue.put_nowait(row["file_id"])
            if row["source_id"]:
                sources.add(row["source_id"])
        for source_id in sources:
            self.refresh_workspace_source(source_id)
        self.refresh_workspace_pack()
        self.warn_about_other_embed_models()

    def warn_about_other_embed_models(self) -> None:
        """Uploads embedded with another model are not re-embedded (a pack is: its digest covers the model). Say so,
        as retrieval compares their vectors with the new model's."""
        for manifest in self.catalog.sources():
            model = (manifest.get("documents") or {}).get("embed_model")
            if manifest.get("pack_id") == WORKSPACE and model and model != self.settings.embed_model:
                logger.warning(
                    "%s was embedded with %s, not RETRIEVER_EMBED_MODEL %s: upload its files again to re-embed them",
                    manifest["id"],
                    model,
                    self.settings.embed_model,
                )

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._pool.shutdown(wait=False, cancel_futures=True)

    def enqueue(self, file_ids: list[str]) -> None:
        """Queue files; safe from any thread (uploads are accepted in a worker thread)."""
        assert self._queue is not None and self._loop is not None, "start() the pipeline first"
        for file_id in file_ids:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, file_id)

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
        row = self.store.claim(file_id)  # None: finished, deleted, or another worker has it
        if row is None:
            return
        source_id = row["source_id"]
        path = self.stored_path(row)
        assert path is not None and source_id is not None

        def update(**fields: Any) -> None:
            self.store.update_file(file_id, **fields)

        try:
            if row["kind"] == "table":
                self._process_table(row, path, update)
            else:
                self._finish(row, self._run_upload(row, path, update), update)
        except Exception as error:  # the database copy, the catalog or the store failed: the file ends here
            # A table's copy was already discarded under the build lock: another file may be writing a new one now.
            logger.exception("%s (%s) could not be finished", row["file_name"], file_id)
            message = f"Ingestion failed: {type(error).__name__}: {redact(error)}"
            update(stage=Stage.FAILED, error_code="internal_error", error_message=message, claimed=0)
            try:
                self.refresh_workspace_source(source_id)
            except Exception:
                logger.exception("could not rewrite %s", source_id)

    def _run_upload(self, row: dict[str, Any], path: Path, update: Update, **kwargs: Any) -> Outcome:
        return self.run_file(
            source_id=row["source_id"],
            path=path,
            file_name=row["file_name"],
            file_id=row["file_id"],
            update=update,
            **kwargs,
        )

    def _process_table(self, row: dict[str, Any], path: Path, update: Update) -> None:
        """Copy-on-write: readers attach tables.duckdb read-only, so a table file loads into a private copy that is
        swapped in whole (os.replace) once the file has loaded and profiled. The build lock is held until the copy is
        committed or discarded and the store has the file's tables, which the next writer names its own around."""
        file_id, source_id = row["file_id"], row["source_id"]
        with self.build_lock(source_id):
            building = self.begin_database(source_id)
            ended = False
            try:
                # Names taken by the source's other files; this file's own earlier tables are replaced.
                others = [r for r in self.store.source_files(source_id) if r["file_id"] != file_id]
                existing = {name for r in others for name in r["tables"]}
                tables.drop_tables(building, row["tables"])
                outcome = self._run_upload(row, path, update, db_path=building, existing_tables=existing)
                profile = None
                if outcome.ready:
                    update(stage=Stage.PROFILING, stage_detail=None, progress_percent=85)
                    try:
                        profile = self.run_stage(Stage.PROFILING, lambda: tables.profile_database(building, None))
                    except IngestError as error:
                        outcome = Outcome(
                            stage=Stage.FAILED, kind="table", error_code=error.code, error_message=error.message
                        )
                self.end_database(source_id, building, commit=outcome.ready)
                ended = True
                if profile is not None:
                    self._profiles[source_id] = profile
                self._finish(row, outcome, update)
            finally:
                if not ended:
                    self.end_database(source_id, building, commit=False)

    def _finish(self, row: dict[str, Any], outcome: Outcome, update: Update) -> None:
        file_id, source_id = row["file_id"], row["source_id"]
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
        # The catalog first, then the job store: a client that sees the file finished finds it in the manifest. The
        # manifest lock spans both, so no other rewrite can list the file as still ingesting after this one.
        with self.manifest_lock(source_id):
            self.refresh_workspace_source(source_id, final={file_id: status_for(outcome.stage)})
            update(
                stage=outcome.stage,
                stage_detail=None,
                progress_percent=100 if outcome.ready else row["progress_percent"],
                claimed=0,
            )
        if self.store.get_file(file_id) is None:  # deleted while it ran: undo what it wrote
            self.remove_outputs({**row, "tables": outcome.tables, "document_id": outcome.document_id})
            self.refresh_workspace_source(source_id)

    def delete_uploads(self, file_ids: list[str]) -> list[dict[str, Any]]:
        """Delete files without waiting for another file's load: a deleted file's tables leave the manifest (so the
        tables tool) at once, and the DuckDB file now or, while another table file is loading, right after it."""
        assert self.store is not None
        deleted = self.store.delete_files(WORKSPACE, file_ids)
        drops: defaultdict[str, set[str]] = defaultdict(set)
        for row in deleted:
            self.remove_outputs({**row, "tables": []})
            if row["source_id"] and row["tables"]:
                drops[row["source_id"]].update(row["tables"])
            path = self.stored_path(row)
            if path is not None:
                path.unlink(missing_ok=True)
        later: dict[str, set[str]] = {}
        for source_id, names in drops.items():
            lock = self.build_lock(source_id)
            if lock.acquire(blocking=False):
                try:
                    self.drop_deleted_tables(source_id, names)
                finally:
                    lock.release()
            else:
                later[source_id] = names
        for source_id in {row["source_id"] for row in deleted if row["source_id"]}:
            self.refresh_workspace_source(source_id)
        for source_id, names in later.items():
            threading.Thread(
                target=self._drop_later, args=(source_id, names), name=f"drop-{source_id}", daemon=True
            ).start()
        return deleted

    def _drop_later(self, source_id: str, names: set[str]) -> None:
        try:
            self.drop_deleted_tables(source_id, names)
            self.refresh_workspace_source(source_id)
        except Exception:
            logger.exception("could not drop the deleted tables %s of %s", sorted(names), source_id)

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
            self.drop_deleted_tables(source_id, row["tables"])

    def drop_deleted_tables(self, source_id: str, names: Iterable[str]) -> None:
        """Drop the tables no file of the source holds any more (a file loaded meanwhile may have reused a name), and
        profile what is left; waits for the build lock."""
        with self.build_lock(source_id):
            in_use = {name for r in self.store.source_files(source_id) for name in r["tables"]} if self.store else set()
            names = set(names) - in_use
            if not names or not database_path(self.catalog, source_id).exists():
                return
            building = self.begin_database(source_id)
            ended = False
            try:
                tables.drop_tables(building, names)
                profile = tables.profile_database(building, None)
                self.end_database(source_id, building, commit=True)
                ended = True
            finally:
                if not ended:
                    self.end_database(source_id, building, commit=False)
            self._profiles[source_id] = profile

    def begin_database(self, source_id: str) -> Path:
        """A private copy of the source's DuckDB to write; the caller holds the build lock."""
        target = database_path(self.catalog, source_id)
        building = self.discard_database(source_id)
        if target.exists():
            try:
                shutil.copyfile(target, building)
            except BaseException:
                self.discard_database(source_id)
                raise
        return building

    def discard_database(self, source_id: str) -> Path:
        """Remove an unfinished copy (and its WAL); returns its path. The caller holds the build lock."""
        target = database_path(self.catalog, source_id)
        building = target.with_name(f"{target.name}.building")
        for leftover in (building, building.with_name(f"{building.name}.wal")):
            leftover.unlink(missing_ok=True)
        return building

    def end_database(self, source_id: str, building: Path, *, commit: bool) -> None:
        """Swap the copy in (readers keep the file they opened), or throw it away."""
        if commit and building.exists():
            building.chmod(0o644)
            os.replace(building, database_path(self.catalog, source_id))
        else:
            self.discard_database(source_id)

    # Workspace catalog entries

    def refresh_workspace_source(self, source_id: str, *, final: dict[str, str] | None = None) -> None:
        """Rewrite the source manifest from the job store (``final``: statuses about to be stored); remove it with
        its last file."""
        assert self.store is not None
        spec = next(spec for spec in WORKSPACE_SOURCES.values() if spec["id"] == source_id)
        with self.manifest_lock(source_id):
            rows = [
                {**row, "status": (final or {}).get(row["file_id"], row["status"])}
                for row in self.store.source_files(source_id)
            ]
            if not rows:
                self.catalog.delete_source(source_id)
                self._profiles.pop(source_id, None)
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
                    profile = self._profiles.get(source_id)
                    if profile is None:  # since a restart: the tables the manifest lists are all still there
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
