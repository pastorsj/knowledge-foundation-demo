# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""SQLite job store: ingestion jobs, their files and each file's stage.

The database runs in WAL mode, so the API's status polls never wait on a worker's write. Every call opens its own
connection, so the store is safe to use from the event loop and from the worker threads alike. A file belongs to the
job that uploaded it; ``job_files`` also links a re-uploaded (deduplicated) file to the later job, so that job's status
lists it.

Statuses follow AI-Q's documents contract: a file is ``uploading`` until its bytes are stored, ``ingesting`` while a
stage runs, then ``success`` or ``failed``; a job is ``pending`` until a file starts, ``processing``, then
``completed`` (every file terminal, at least one success) or ``failed`` (every file failed).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path
from typing import Any

from .models import TERMINAL
from .models import FileStatus
from .models import JobState
from .models import Stage
from .models import status_for
from .models import utcnow

BACKEND = "knowledge-foundation"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS collections (
    name        TEXT PRIMARY KEY,
    description TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id        TEXT PRIMARY KEY,
    collection    TEXT NOT NULL,
    status        TEXT NOT NULL,
    submitted_at  TEXT NOT NULL,
    started_at    TEXT,
    completed_at  TEXT,
    error_message TEXT
);
CREATE TABLE IF NOT EXISTS files (
    file_id          TEXT PRIMARY KEY,
    job_id           TEXT NOT NULL,
    collection       TEXT NOT NULL,
    source_id        TEXT,
    file_name        TEXT NOT NULL,
    sha256           TEXT NOT NULL,
    size_bytes       INTEGER NOT NULL,
    kind             TEXT,
    status           TEXT NOT NULL,
    stage            TEXT,
    stage_detail     TEXT,
    progress_percent REAL NOT NULL DEFAULT 0,
    parser           TEXT,
    chunks           INTEGER NOT NULL DEFAULT 0,
    pages            INTEGER,
    document_id      TEXT,
    tables_json      TEXT NOT NULL DEFAULT '[]',
    warnings_json    TEXT NOT NULL DEFAULT '[]',
    error_code       TEXT,
    error_message    TEXT,
    uploaded_at      TEXT NOT NULL,
    ingested_at      TEXT,
    attempts         INTEGER NOT NULL DEFAULT 0,
    claimed          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_files_collection ON files(collection, sha256);
CREATE INDEX IF NOT EXISTS idx_files_source ON files(source_id);
CREATE TABLE IF NOT EXISTS job_files (
    job_id  TEXT NOT NULL,
    file_id TEXT NOT NULL,
    PRIMARY KEY (job_id, file_id)
);
CREATE INDEX IF NOT EXISTS idx_job_files_file ON job_files(file_id);
"""

# Columns update_file may set; tables and warnings are lists stored as JSON.
_UPDATABLE = frozenset(
    {
        "source_id",
        "kind",
        "status",
        "stage",
        "stage_detail",
        "progress_percent",
        "parser",
        "chunks",
        "pages",
        "document_id",
        "tables",
        "warnings",
        "error_code",
        "error_message",
        "ingested_at",
        "claimed",
    }
)
# Columns added after the first release, for stores created before them.
_ADDED_COLUMNS = {
    "attempts": "INTEGER NOT NULL DEFAULT 0",
    "claimed": "INTEGER NOT NULL DEFAULT 0",
}
_JSON_COLUMNS = {"tables": "tables_json", "warnings": "warnings_json"}


class JobStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)
            present = {row[1] for row in connection.execute("PRAGMA table_info(files)")}
            for column, definition in _ADDED_COLUMNS.items():
                if column not in present:
                    connection.execute(f"ALTER TABLE files ADD COLUMN {column} {definition}")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def _run[T](self, work: Callable[[sqlite3.Connection], T], *, write: bool = True) -> T:
        """Run ``work`` in one transaction; a write takes the lock up front, so it never fails to upgrade."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                result = work(connection)
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
            return result

    def ping(self) -> None:
        self._run(lambda connection: connection.execute("SELECT 1").fetchone(), write=False)

    # Collections

    def ensure_collection(self, name: str, description: str | None = None) -> bool:
        """Create the collection if it is new; True when it was created."""

        def insert(connection: sqlite3.Connection) -> bool:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO collections (name, description, created_at) VALUES (?, ?, ?)",
                (name, description, utcnow()),
            )
            return cursor.rowcount == 1

        return self._run(insert)

    def collections(self) -> list[dict[str, Any]]:
        def select(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = connection.execute(
                "SELECT c.name, c.description, c.created_at, COUNT(f.file_id) AS file_count, "
                "COALESCE(SUM(f.chunks), 0) AS chunk_count, MAX(COALESCE(f.ingested_at, f.uploaded_at)) AS updated_at "
                "FROM collections c LEFT JOIN files f ON f.collection = c.name GROUP BY c.name ORDER BY c.created_at"
            ).fetchall()
            return [
                {
                    "name": row["name"],
                    "description": row["description"],
                    "file_count": row["file_count"],
                    "chunk_count": row["chunk_count"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"] or row["created_at"],
                    "backend": BACKEND,
                    "metadata": {},
                }
                for row in rows
            ]

        return self._run(select, write=False)

    def collection(self, name: str) -> dict[str, Any] | None:
        return next((c for c in self.collections() if c["name"] == name), None)

    def delete_collection(self, name: str) -> None:
        self._run(lambda connection: connection.execute("DELETE FROM collections WHERE name = ?", (name,)))

    # Jobs and files

    def create_job(self, collection: str, files: Iterable[dict[str, Any]], *, existing: Iterable[str] = ()) -> str:
        """A job for ``files`` (new rows; a row with the same id is replaced) plus ``existing`` (deduplicated) ids."""
        job_id = str(uuid.uuid4())
        now = utcnow()

        def insert(connection: sqlite3.Connection) -> str:
            connection.execute(
                "INSERT INTO jobs (job_id, collection, status, submitted_at) VALUES (?, ?, ?, ?)",
                (job_id, collection, JobState.PENDING, now),
            )
            for entry in files:
                stage = entry.get("stage", Stage.RECEIVED)
                connection.execute(
                    "INSERT OR REPLACE INTO files (file_id, job_id, collection, source_id, file_name, sha256, "
                    "size_bytes, kind, status, stage, error_code, error_message, uploaded_at, ingested_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                    (
                        entry["file_id"],
                        job_id,
                        collection,
                        entry.get("source_id"),
                        entry["file_name"],
                        entry["sha256"],
                        entry["size_bytes"],
                        entry.get("kind"),
                        status_for(stage),
                        stage,
                        entry.get("error_code"),
                        entry.get("error_message"),
                        now,
                    ),
                )
                connection.execute("INSERT OR IGNORE INTO job_files VALUES (?, ?)", (job_id, entry["file_id"]))
            for file_id in existing:
                connection.execute("INSERT OR IGNORE INTO job_files VALUES (?, ?)", (job_id, file_id))
            _refresh_job(connection, job_id)
            return job_id

        return self._run(insert)

    def update_file(self, file_id: str, **fields: Any) -> None:
        """Set columns of one file; a new ``stage`` also sets its status, then its jobs' statuses follow."""
        if unknown := set(fields) - _UPDATABLE:
            raise ValueError(f"update_file: unknown fields {sorted(unknown)}")
        if "stage" in fields and "status" not in fields:
            fields["status"] = status_for(fields["stage"])
        if fields.get("stage") == Stage.READY and "ingested_at" not in fields:
            fields["ingested_at"] = utcnow()
        columns = {_JSON_COLUMNS.get(name, name): value for name, value in fields.items()}
        for name in _JSON_COLUMNS.values():
            if name in columns:
                columns[name] = json.dumps(list(columns[name] or []))

        def update(connection: sqlite3.Connection) -> None:
            if columns:
                assignments = ", ".join(f"{name} = ?" for name in columns)
                connection.execute(f"UPDATE files SET {assignments} WHERE file_id = ?", (*columns.values(), file_id))
            for (job_id,) in connection.execute("SELECT job_id FROM job_files WHERE file_id = ?", (file_id,)):
                _refresh_job(connection, job_id)

        self._run(update)

    def claim(self, file_id: str) -> dict[str, Any] | None:
        """Take an unfinished, unclaimed file for one worker and count the attempt; None if it is not to be run."""

        def update(connection: sqlite3.Connection) -> dict[str, Any] | None:
            cursor = connection.execute(
                "UPDATE files SET claimed = 1, attempts = attempts + 1 "
                "WHERE file_id = ? AND claimed = 0 AND status NOT IN (?, ?)",
                (file_id, FileStatus.SUCCESS, FileStatus.FAILED),
            )
            if cursor.rowcount != 1:
                return None
            return _file_row(connection.execute("SELECT * FROM files WHERE file_id = ?", (file_id,)).fetchone())

        return self._run(update)

    def get_file(self, file_id: str) -> dict[str, Any] | None:
        def select(connection: sqlite3.Connection) -> dict[str, Any] | None:
            row = connection.execute("SELECT * FROM files WHERE file_id = ?", (file_id,)).fetchone()
            return _file_row(row) if row else None

        return self._run(select, write=False)

    def job_status(self, job_id: str) -> dict[str, Any] | None:
        """AI-Q's IngestionJobStatus, each file with its stage, kind, parser, tables and warnings."""

        def select(connection: sqlite3.Connection) -> dict[str, Any] | None:
            job = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if job is None:
                return None
            files = [_file_row(row) for row in _job_files(connection, job_id)]
            return {
                "job_id": job["job_id"],
                "status": job["status"],
                "submitted_at": job["submitted_at"],
                "started_at": job["started_at"],
                "completed_at": job["completed_at"],
                "total_files": len(files),
                "processed_files": sum(1 for f in files if f["status"] in TERMINAL),
                "file_details": [_file_progress(f) for f in files],
                "collection_name": job["collection"],
                "backend": BACKEND,
                "error_message": job["error_message"],
                "metadata": {},
            }

        return self._run(select, write=False)

    def files(self, collection: str) -> list[dict[str, Any]]:
        """AI-Q's FileInfo for every file of the collection, oldest first."""

        def select(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = connection.execute(
                "SELECT * FROM files WHERE collection = ? ORDER BY uploaded_at, rowid", (collection,)
            ).fetchall()
            return [_file_info(_file_row(row)) for row in rows]

        return self._run(select, write=False)

    def source_files(self, source_id: str) -> list[dict[str, Any]]:
        """The rows of every file of one catalog source, oldest first."""

        def select(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = connection.execute(
                "SELECT * FROM files WHERE source_id = ? ORDER BY uploaded_at, rowid", (source_id,)
            ).fetchall()
            return [_file_row(row) for row in rows]

        return self._run(select, write=False)

    def unfinished(self) -> list[dict[str, Any]]:
        """Files a restart must re-queue: every file that is neither success nor failed, in upload order."""

        def select(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = connection.execute(
                "SELECT * FROM files WHERE status NOT IN (?, ?) ORDER BY uploaded_at, rowid",
                (FileStatus.SUCCESS, FileStatus.FAILED),
            ).fetchall()
            return [_file_row(row) for row in rows]

        return self._run(select, write=False)

    def delete_files(self, collection: str, file_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Delete the rows; returns the rows that existed, so the caller can remove their chunks and tables."""
        wanted = list(file_ids)

        def delete(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            deleted = []
            for file_id in wanted:
                row = connection.execute(
                    "SELECT * FROM files WHERE collection = ? AND file_id = ?", (collection, file_id)
                ).fetchone()
                if row is None:
                    continue
                deleted.append(_file_row(row))
                jobs = [r[0] for r in connection.execute("SELECT job_id FROM job_files WHERE file_id = ?", (file_id,))]
                connection.execute("DELETE FROM files WHERE file_id = ?", (file_id,))
                connection.execute("DELETE FROM job_files WHERE file_id = ?", (file_id,))
                for job_id in jobs:
                    _refresh_job(connection, job_id)
            return deleted

        return self._run(delete)


def _job_files(connection: sqlite3.Connection, job_id: str) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT f.* FROM job_files j JOIN files f ON f.file_id = j.file_id WHERE j.job_id = ? "
        "ORDER BY f.uploaded_at, f.rowid",
        (job_id,),
    ).fetchall()


def _refresh_job(connection: sqlite3.Connection, job_id: str) -> None:
    """Derive the job's status from its files; record when it started and when it finished."""
    files = _job_files(connection, job_id)
    statuses = [row["status"] for row in files]
    if statuses and all(status in TERMINAL for status in statuses):
        state = JobState.COMPLETED if FileStatus.SUCCESS in statuses else JobState.FAILED
    elif any(row["stage"] not in (None, Stage.RECEIVED) for row in files):
        state = JobState.PROCESSING
    elif not statuses:
        state = JobState.COMPLETED
    else:
        state = JobState.PENDING
    now = utcnow()
    connection.execute(
        "UPDATE jobs SET status = ?, "
        "started_at = CASE WHEN ? != 'pending' THEN COALESCE(started_at, ?) END, "
        "completed_at = CASE WHEN ? IN ('completed', 'failed') THEN COALESCE(completed_at, ?) END, "
        "error_message = ? WHERE job_id = ?",
        (
            state,
            state,
            now,
            state,
            now,
            "Every file failed to ingest." if state == JobState.FAILED else None,
            job_id,
        ),
    )


def _file_row(row: sqlite3.Row) -> dict[str, Any]:
    values = dict(row)
    values["tables"] = json.loads(values.pop("tables_json") or "[]")
    values["warnings"] = json.loads(values.pop("warnings_json") or "[]")
    return values


def _extras(row: dict[str, Any]) -> dict[str, Any]:
    """The fields Knowledge Foundation adds to AI-Q's FileProgress and FileInfo (its zod schemas ignore them)."""
    return {
        "kind": row["kind"],
        "stage": row["stage"],
        "stage_detail": row["stage_detail"],
        "parser": row["parser"],
        "tables": row["tables"],
        "warnings": row["warnings"],
        "error_code": row["error_code"],
        "source_id": row["source_id"],
    }


def _file_progress(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": row["file_id"],
        "file_name": row["file_name"],
        "status": row["status"],
        "progress_percent": row["progress_percent"],
        "error_message": row["error_message"],
        "chunks_created": row["chunks"],
        **_extras(row),
    }


def _file_info(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": row["file_id"],
        "file_name": row["file_name"],
        "collection_name": row["collection"],
        "status": row["status"],
        "file_size": row["size_bytes"],
        "chunk_count": row["chunks"],
        "uploaded_at": row["uploaded_at"],
        "ingested_at": row["ingested_at"],
        "expiration_date": None,
        "error_message": row["error_message"],
        "metadata": {},
        **_extras(row),
    }
