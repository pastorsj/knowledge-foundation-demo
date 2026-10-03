# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Industry packs: every ``<PACKS_DIR>/<id>/pack.yaml`` into the catalog, through the same pipeline as an upload.

A pack is synced when its digest changed: sha256 over the sorted relative paths and bytes of ``pack.yaml``,
``questions.yaml`` and ``files/**``, plus what turns those bytes into the catalog (the Parse model, or
``pdf-text-layer`` when Parse is disabled, and the embed model). A sync whose PDFs fell back to their text layer
because Parse was unreachable leaves the digest out of the manifest, so the next sync tries Parse again.

Each structured source is rebuilt into a new DuckDB file that replaces the old one in one step, so readers see the
old tables or the new ones. Each documents source is re-indexed file by file; documents that left the pack are
removed from the index.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from . import tables
from .catalog import Catalog
from .detect import stored_name
from .models import utcnow
from .pipeline import Outcome
from .pipeline import Pipeline
from .pipeline import database_alias
from .pipeline import file_entry
from .pipeline import source_status
from .settings import Settings

logger = logging.getLogger(__name__)

MAX_EXAMPLES = 12
# Failures another sync may not repeat: the pack's digest is withheld after one, so the next sync retries.
TRANSIENT_ERRORS = {"embedding_failed", "index_unavailable", "timeout"}
KINDS = {"documents": "document", "structured": "table"}


class PackError(Exception):
    """A pack that does not satisfy the pack format."""


@dataclass
class PackStatus:
    id: str
    status: str = "pending"  # pending, ingesting, ready, failed
    files_total: int = 0
    files_done: int = 0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "files_total": self.files_total,
            "files_done": self.files_done,
            "error": self.error,
        }


@dataclass
class SyncProgress:
    """What GET /v1/packs/status reports; shared between the sync thread and the API."""

    packs: dict[str, PackStatus] = field(default_factory=dict)
    running: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return [status.as_dict() for status in self.packs.values()]


# Reading a pack


def _validator(settings: Settings, name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((settings.pack_schema_dir / f"{name}.schema.json").read_text()))


def load_pack(pack_dir: Path, settings: Settings) -> tuple[dict[str, Any], dict[str, Any]]:
    """pack.yaml and questions.yaml, validated."""
    documents = {}
    for name, schema in (("pack.yaml", "pack"), ("questions.yaml", "questions")):
        path = pack_dir / name
        try:
            document = yaml.safe_load(path.read_text())
        except (OSError, yaml.YAMLError) as error:
            raise PackError(f"{pack_dir.name}/{name}: {error}") from error
        errors = sorted(_validator(settings, schema).iter_errors(document), key=lambda e: list(e.absolute_path))
        if errors:
            details = "; ".join(f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors[:5])
            raise PackError(f"{pack_dir.name}/{name}: {details}")
        documents[name] = document
        if schema == "pack" and document["id"] != pack_dir.name:
            raise PackError(f"{pack_dir.name}/pack.yaml: id {document['id']!r} is not the directory name")
    pack, questions = documents["pack.yaml"], documents["questions.yaml"]
    source_ids = {source["id"] for source in pack["sources"]}
    for entry in [*questions["questions"], *questions.get("conversations", [])]:
        if unknown := set(entry["sources"]) - source_ids:
            raise PackError(f"{pack_dir.name}/questions.yaml: {entry['id']} uses unknown sources {sorted(unknown)}")
    return pack, questions


def pack_digest(pack_dir: Path, settings: Settings) -> str:
    """The pack's inputs and the configuration that turns them into the catalog."""
    paths = [pack_dir / "pack.yaml", pack_dir / "questions.yaml"]
    paths += [path for path in (pack_dir / "files").rglob("*") if path.is_file()]
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.relative_to(pack_dir).as_posix()):
        relative = path.relative_to(pack_dir).as_posix()
        digest.update(f"{relative}\0".encode())
        with path.open("rb") as f:
            digest.update(hashlib.file_digest(f, "sha256").digest())
    configuration = {
        "parser": settings.parse_model if settings.parse_enabled else "pdf-text-layer",
        "embed_model": settings.embed_model,
    }
    digest.update(json.dumps(configuration, sort_keys=True).encode())
    return digest.hexdigest()


def source_files(pack_dir: Path, source: dict[str, Any]) -> list[Path]:
    found: dict[str, Path] = {}
    for pattern in source["files"]:
        for path in pack_dir.glob(pattern):
            if path.is_file():
                found.setdefault(path.relative_to(pack_dir).as_posix(), path)
    return [found[key] for key in sorted(found)]


def _sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


# Syncing


def sync_all(
    settings: Settings, catalog: Catalog, pipeline: Pipeline, progress: SyncProgress | None = None
) -> list[dict[str, Any]]:
    """Sync every pack under PACKS_DIR; returns each pack's final status. Never raises for one bad pack."""
    progress = progress or SyncProgress()
    pack_dirs = sorted(p.parent for p in settings.packs_dir.glob("*/pack.yaml")) if settings.packs_dir.is_dir() else []
    with progress.lock:
        progress.running = True
        progress.packs = {d.name: PackStatus(d.name) for d in pack_dirs}
    try:
        for pack_dir in pack_dirs:
            status = progress.packs[pack_dir.name]
            try:
                _sync_pack(settings, catalog, pipeline, pack_dir=pack_dir, status=status, lock=progress.lock)
            except Exception as error:
                logger.exception("pack %s failed", pack_dir.name)
                with progress.lock:
                    status.status, status.error = "failed", str(error)[:600]
    finally:
        with progress.lock:
            progress.running = False
    return progress.snapshot()


def _sync_pack(
    settings: Settings,
    catalog: Catalog,
    pipeline: Pipeline,
    *,
    pack_dir: Path,
    status: PackStatus,
    lock: threading.Lock,
) -> None:
    pack, questions = load_pack(pack_dir, settings)
    pack_id = pack["id"]
    digest = pack_digest(pack_dir, settings)
    files = {source["id"]: source_files(pack_dir, source) for source in pack["sources"]}
    with lock:
        status.files_total = sum(len(paths) for paths in files.values())
    current = catalog.read_pack(pack_id)
    if current and current.get("digest") == digest and current["status"] == "ready":
        logger.info("pack %s is up to date", pack_id)
        with lock:
            status.status, status.files_done = "ready", status.files_total
        return

    with lock:
        status.status = "ingesting"
    manifest = _pack_manifest(pack, questions, status="ingesting")
    if current and current.get("digest"):
        manifest["digest"] = current["digest"]  # what the catalog still holds until this sync finishes
    catalog.write_pack(manifest)

    outcomes: list[Outcome] = []
    source_statuses = []
    for source in pack["sources"]:
        source_id = f"{pack_id}.{source['id']}"

        def done(outcome: Outcome) -> None:
            outcomes.append(outcome)
            with lock:
                status.files_done += 1

        if source["kind"] == "documents":
            entries = _sync_documents(pipeline, catalog, source_id, files[source["id"]], done)
            extra = {"documents": _documents_summary(settings, entries)}
        else:
            entries, database = _sync_structured(
                pipeline, catalog, source=source, source_id=source_id, paths=files[source["id"]], done=done
            )
            extra = {"database": database}
            if "prediction" in source:
                extra["prediction"] = {"templates": source["prediction"]["templates"]}
        source_statuses.append(source_status(entries))
        catalog.write_source(_source_manifest(pack_id, source, entries, extra))

    # Complete unless Parse did not answer (a PDF read from its text layer, an image not read) or a failure was
    # transient. Configuration (Parse turned off) and the content itself would give the same result next time.
    retry = [o for o in outcomes if o.fallback == "unavailable" or (not o.ready and o.error_code in TRANSIENT_ERRORS)]
    skipped = [o for o in outcomes if not o.ready and o.error_code == "parser_unavailable" and o.fallback == "disabled"]
    failed = [o for o in outcomes if not o.ready and o not in skipped]
    final = "failed" if "failed" in source_statuses or "empty" in source_statuses else "ready"
    manifest = _pack_manifest(pack, questions, status=final)
    if not retry and final == "ready":
        manifest["digest"] = digest
    catalog.write_pack(manifest)
    with lock:
        status.status = final
        if failed or skipped:
            status.error = "; ".join(f"{o.error_code}: {o.error_message}" for o in failed + skipped)[:600]
        if failed:
            status.status = "failed"  # the CLI and the status report a pack with a failed file
    logger.info("pack %s: %s (%d files, %d failed)", pack_id, final, len(outcomes), len(failed))


def _stored_copy(catalog: Catalog, source_id: str, path: Path) -> tuple[str, str, Path]:
    """Copy a pack file into the source's files/ (content-addressed) and return (sha256, file_id, copy)."""
    sha = _sha256(path)
    file_id = f"f-{sha[:16]}"
    copy = catalog.source_dir(source_id) / "files" / stored_name(file_id, path.name)
    if not copy.exists():
        copy.parent.mkdir(parents=True, exist_ok=True)
        temporary = copy.with_name(f".{copy.name}.tmp")
        shutil.copyfile(path, temporary)
        temporary.chmod(0o644)
        temporary.replace(copy)
    return sha, file_id, copy


def _run(pipeline: Pipeline, source_id: str, path: Path, kind: str, **kwargs: Any) -> tuple[dict[str, Any], Outcome]:
    catalog = pipeline.catalog
    sha, file_id, copy = _stored_copy(catalog, source_id, path)
    outcome = pipeline.run_file(
        source_id=source_id,
        path=copy,
        file_name=path.name,
        file_id=file_id,
        update=lambda **fields: None,
        expect_kind=kind,
        **kwargs,
    )
    row = {
        "file_id": file_id,
        "file_name": path.name,
        "sha256": sha,
        "size_bytes": copy.stat().st_size,
        "status": "success" if outcome.ready else "failed",
        "kind": outcome.kind,
        "parser": outcome.parser,
        "pages": outcome.pages,
        "chunks": outcome.chunks,
        "tables": outcome.tables,
        "warnings": outcome.warnings,
        "document_id": outcome.document_id,
        "error_message": outcome.error_message,
    }
    return row, outcome


def _sync_documents(
    pipeline: Pipeline, catalog: Catalog, source_id: str, paths: list[Path], done: Any
) -> list[dict[str, Any]]:
    previous = catalog.read_source(source_id) or {}
    rows = []
    for path in paths:
        row, outcome = _run(pipeline, source_id, path, "document")
        rows.append(row)
        done(outcome)
    # A file still in the pack keeps its chunks even when this sync failed to re-ingest it.
    kept = {f"{source_id}:{row['file_id']}" for row in rows}
    for old in previous.get("files", []):
        if old.get("document_id") and old["document_id"] not in kept:
            pipeline.remove_outputs({"source_id": source_id, "document_id": old["document_id"]})
    _remove_stale_copies(catalog, source_id, {stored_name(row["file_id"], row["file_name"]) for row in rows})
    return [file_entry(row) for row in rows]


def _documents_summary(settings: Settings, entries: list[dict[str, Any]]) -> dict[str, Any]:
    ready = [entry for entry in entries if entry["status"] == "ready"]
    return {
        "count": len(ready),
        "chunks": sum(entry.get("chunks", 0) for entry in ready),
        "collection": settings.collection_alias,
        "embed_model": settings.embed_model,
    }


def _sync_structured(
    pipeline: Pipeline, catalog: Catalog, *, source: dict[str, Any], source_id: str, paths: list[Path], done: Any
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load every file into a new DuckDB file, profile it with the pack's declarations, then swap it in."""
    target = catalog.source_dir(source_id) / "tables.duckdb"
    building = target.with_name("tables.duckdb.building")
    for leftover in (building, building.with_name(f"{building.name}.wal")):
        leftover.unlink(missing_ok=True)
    rows = []
    taken: set[str] = set()
    for path in paths:
        row, outcome = _run(pipeline, source_id, path, "table", db_path=building, existing_tables=set(taken))
        taken.update(outcome.tables)
        rows.append(row)
        done(outcome)
    profile = tables.profile_database(building, source.get("tables")) if building.exists() else []
    with pipeline.source_lock(source_id):
        if building.exists():
            building.chmod(0o644)
            os.replace(building, target)
        else:
            target.unlink(missing_ok=True)
    _remove_stale_copies(catalog, source_id, {stored_name(row["file_id"], row["file_name"]) for row in rows})
    database = {"path": f"sources/{source_id}/tables.duckdb", "alias": database_alias(source_id), "tables": profile}
    return [file_entry(row) for row in rows], database


def _remove_stale_copies(catalog: Catalog, source_id: str, keep: set[str]) -> None:
    directory = catalog.source_dir(source_id) / "files"
    if directory.is_dir():
        for path in directory.iterdir():
            if path.name not in keep:
                path.unlink(missing_ok=True)


def _source_manifest(
    pack_id: str, source: dict[str, Any], entries: list[dict[str, Any]], extra: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "id": f"{pack_id}.{source['id']}",
        "pack_id": pack_id,
        "name": source["name"],
        "description": source["description"],
        "agent_description": source["agent_description"],
        "kind": source["kind"],
        "capabilities": list(source["capabilities"]),
        "synthetic": source["synthetic"],
        "default_enabled": source.get("default_enabled", True),
        "example_questions": list(source.get("example_questions", []))[:3],
        "status": source_status(entries),
        "updated_at": utcnow(),
        "files": entries,
        **extra,
    }


def _pack_manifest(pack: dict[str, Any], questions: dict[str, Any], *, status: str) -> dict[str, Any]:
    pack_id = pack["id"]

    def scoped(ids: list[str]) -> list[str]:
        return [f"{pack_id}.{source_id}" for source_id in ids]

    asked = [
        {
            "id": q["id"],
            "label": q["label"],
            "tag": q.get("tag"),
            "description": q.get("description"),
            "question": q["question"],
            "sources": scoped(q["sources"]),
            "tools": list(q["tools"]),
            "featured": q.get("featured", False),
        }
        for q in questions["questions"]
    ]
    examples = questions.get("examples")
    if not examples:  # the featured questions, then the others
        examples = [q["id"] for q in asked if q["featured"]] + [q["id"] for q in asked if not q["featured"]]
    return {
        "schema_version": "1",
        "id": pack_id,
        "kind": "industry",
        "title": pack["title"],
        "description": pack["description"],
        "icon": pack["icon"],
        "version": pack["version"],
        "as_of": pack["as_of"],
        "disclaimer": pack["disclaimer"],
        "status": status,
        "updated_at": utcnow(),
        "sources": scoped([source["id"] for source in pack["sources"]]),
        "questions": asked,
        "examples": examples[:MAX_EXAMPLES],
        "conversations": [
            {
                "id": c["id"],
                "label": c["label"],
                "tag": c.get("tag"),
                "description": c.get("description"),
                "sources": scoped(c["sources"]),
                "turns": list(c["turns"]),
            }
            for c in questions.get("conversations", [])
        ],
    }


def sync_cli(settings: Settings, *, pipeline: Pipeline | None = None) -> int:
    """`demo-ingest sync-packs`: 0 when every pack is ready, 1 otherwise."""
    catalog = Catalog(settings.knowledge_dir, settings.catalog_schema_dir)
    pipeline = pipeline or Pipeline(settings, catalog)
    statuses = sync_all(settings, catalog, pipeline)
    for status in statuses:
        line = f"{status['id']}: {status['status']} ({status['files_done']}/{status['files_total']} files)"
        print(line + (f": {status['error']}" if status["error"] else ""), flush=True)
    return 0 if all(status["status"] == "ready" for status in statuses) else 1
