# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from demo_ingest.store import JobStore


def entry(file_id: str, name: str, kind: str = "table") -> dict:
    return {
        "file_id": file_id,
        "file_name": name,
        "source_id": "workspace.tables" if kind == "table" else "workspace.documents",
        "sha256": file_id[2:].ljust(64, "0"),
        "size_bytes": 10,
        "kind": kind,
    }


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "ingest" / "ingest.sqlite3")


def test_the_store_runs_in_wal_mode(tmp_path: Path, store: JobStore):
    connection = sqlite3.connect(tmp_path / "ingest" / "ingest.sqlite3")
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_a_job_completes_when_every_file_is_terminal(store: JobStore):
    job_id = store.create_job("workspace", [entry("f-aaaaaaaaaaaaaaaa", "a.csv"), entry("f-bbbbbbbbbbbbbbbb", "b.pdf")])
    status = store.job_status(job_id)
    assert status["status"] == "pending"
    assert status["total_files"] == 2 and status["processed_files"] == 0
    assert {f["status"] for f in status["file_details"]} == {"ingesting"}
    assert {f["stage"] for f in status["file_details"]} == {"received"}

    store.update_file("f-aaaaaaaaaaaaaaaa", stage="ready", progress_percent=100, parser="duckdb-csv", tables=["a"])
    status = store.job_status(job_id)
    assert status["status"] == "processing"
    assert status["started_at"] is not None and status["completed_at"] is None
    first = next(f for f in status["file_details"] if f["file_id"] == "f-aaaaaaaaaaaaaaaa")
    assert first["status"] == "success"
    assert (first["parser"], first["tables"], first["kind"]) == ("duckdb-csv", ["a"], "table")

    store.update_file("f-bbbbbbbbbbbbbbbb", stage="ready", progress_percent=100, chunks=7, warnings=["text layer"])
    status = store.job_status(job_id)
    assert status["status"] == "completed"
    assert status["processed_files"] == 2 and status["completed_at"] is not None
    second = next(f for f in status["file_details"] if f["file_id"] == "f-bbbbbbbbbbbbbbbb")
    assert (second["chunks_created"], second["warnings"]) == (7, ["text layer"])
    # The AI-Q IngestionJobStatus fields
    assert {"job_id", "submitted_at", "collection_name", "backend", "error_message", "metadata"} <= status.keys()


def test_a_job_fails_only_when_every_file_failed(store: JobStore):
    job_id = store.create_job("workspace", [entry("f-aaaaaaaaaaaaaaaa", "a.csv"), entry("f-bbbbbbbbbbbbbbbb", "b.csv")])

    store.update_file("f-aaaaaaaaaaaaaaaa", stage="failed", error_code="empty_file", error_message="The file is empty.")
    assert store.job_status(job_id)["status"] == "processing"
    store.update_file("f-bbbbbbbbbbbbbbbb", stage="failed", error_code="empty_file", error_message="The file is empty.")

    status = store.job_status(job_id)
    assert status["status"] == "failed"
    assert [f["error_message"] for f in status["file_details"]] == ["The file is empty."] * 2
    assert status["file_details"][0]["error_code"] == "empty_file"


def test_one_failure_beside_a_success_completes_the_job(store: JobStore):
    job_id = store.create_job("workspace", [entry("f-aaaaaaaaaaaaaaaa", "a.csv"), entry("f-bbbbbbbbbbbbbbbb", "b.csv")])
    store.update_file("f-aaaaaaaaaaaaaaaa", stage="failed", error_message="bad")
    store.update_file("f-bbbbbbbbbbbbbbbb", stage="ready")

    assert store.job_status(job_id)["status"] == "completed"


def test_unfinished_lists_files_that_are_not_terminal(store: JobStore):
    store.create_job(
        "workspace",
        [
            entry("f-aaaaaaaaaaaaaaaa", "a.csv"),
            entry("f-bbbbbbbbbbbbbbbb", "b.csv"),
            entry("f-cccccccccccccccc", "c.pdf"),
        ],
    )
    store.update_file("f-aaaaaaaaaaaaaaaa", stage="ready")
    store.update_file("f-bbbbbbbbbbbbbbbb", stage="loading", stage_detail="sheet 1 of 2")

    assert [row["file_id"] for row in store.unfinished()] == ["f-bbbbbbbbbbbbbbbb", "f-cccccccccccccccc"]


def test_files_lists_a_collection_in_the_ai_q_file_info_shape(store: JobStore):
    store.create_job("workspace", [entry("f-aaaaaaaaaaaaaaaa", "a.csv")])
    store.update_file("f-aaaaaaaaaaaaaaaa", stage="ready", tables=["a"], parser="duckdb-csv")

    [info] = store.files("workspace")

    assert info["file_id"] == "f-aaaaaaaaaaaaaaaa"
    assert (info["collection_name"], info["status"], info["file_size"]) == ("workspace", "success", 10)
    assert info["chunk_count"] == 0 and info["ingested_at"] is not None
    assert (info["stage"], info["tables"], info["parser"], info["kind"]) == ("ready", ["a"], "duckdb-csv", "table")
    assert info["metadata"] == {}
    assert store.files("other") == []


def test_a_deduplicated_file_joins_the_new_job(store: JobStore):
    store.create_job("workspace", [entry("f-aaaaaaaaaaaaaaaa", "a.csv")])
    store.update_file("f-aaaaaaaaaaaaaaaa", stage="ready")

    again = store.create_job("workspace", [], existing=["f-aaaaaaaaaaaaaaaa"])

    status = store.job_status(again)
    assert status["status"] == "completed"
    assert [f["file_id"] for f in status["file_details"]] == ["f-aaaaaaaaaaaaaaaa"]
    assert store.find_by_sha("workspace", "a" * 16 + "0" * 48)["file_id"] == "f-aaaaaaaaaaaaaaaa"


def test_delete_files_returns_the_deleted_rows(store: JobStore):
    job_id = store.create_job("workspace", [entry("f-aaaaaaaaaaaaaaaa", "a.csv"), entry("f-bbbbbbbbbbbbbbbb", "b.csv")])

    deleted = store.delete_files("workspace", ["f-aaaaaaaaaaaaaaaa", "f-missing"])

    assert [row["file_id"] for row in deleted] == ["f-aaaaaaaaaaaaaaaa"]
    assert [f["file_id"] for f in store.files("workspace")] == ["f-bbbbbbbbbbbbbbbb"]
    assert [f["file_id"] for f in store.job_status(job_id)["file_details"]] == ["f-bbbbbbbbbbbbbbbb"]


def test_an_unknown_job_has_no_status(store: JobStore):
    assert store.job_status("nope") is None


def test_update_file_refuses_unknown_columns(store: JobStore):
    store.create_job("workspace", [entry("f-aaaaaaaaaaaaaaaa", "a.csv")])
    with pytest.raises(ValueError, match="nonsense"):
        store.update_file("f-aaaaaaaaaaaaaaaa", nonsense=1)
