# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import CATALOG_FIXTURES
from conftest import FakeEmbedder

from demo_ingest import pipeline as pipeline_module
from demo_ingest import tables
from demo_ingest.catalog import Catalog
from demo_ingest.index import KnowledgeIndex
from demo_ingest.pipeline import Pipeline
from demo_ingest.pipeline import file_entry
from demo_ingest.pipeline import source_status
from demo_ingest.settings import Settings


@pytest.fixture
def index(tmp_path: Path) -> Iterator[KnowledgeIndex]:
    index = KnowledgeIndex(str(tmp_path / "milvus.db"), "knowledge")
    yield index
    index.close()


@pytest.fixture
def pipeline(settings: Settings, catalog: Catalog, embedder: FakeEmbedder, index, tokenizer) -> Pipeline:
    return Pipeline(settings, catalog, None, embedder=embedder, index=index, tokenizer=tokenizer)


def run(pipeline: Pipeline, path: Path, name: str, **kwargs):
    updates: list[dict] = []
    outcome = pipeline.run_file(
        source_id=kwargs.pop("source_id", "workspace.tables"),
        path=path,
        file_name=name,
        file_id="f-0123456789abcdef",
        update=lambda **fields: updates.append(fields),
        **kwargs,
    )
    return outcome, updates


def test_a_table_file_reports_its_stages(pipeline: Pipeline, tmp_path: Path):
    outcome, updates = run(
        pipeline, CATALOG_FIXTURES / "tables" / "orders.csv", "orders.csv", db_path=tmp_path / "t.duckdb"
    )

    assert outcome.ready and (outcome.kind, outcome.tables, outcome.parser) == ("table", ["orders"], "duckdb-csv")
    assert [u["stage"] for u in updates][:2] == ["detected", "loading"]
    assert {"stage": "loading", "stage_detail": "sheet 1 of 1"}.items() <= updates[-1].items()


def test_a_document_reports_why_it_used_the_text_layer(pipeline: Pipeline, policy_pdf: Path):
    outcome, updates = run(pipeline, policy_pdf, "policy.pdf", source_id="workspace.documents")

    assert outcome.ready and outcome.parser == "pdf-text-layer"
    assert outcome.fallback == "disabled"  # the test settings leave PARSE_BASE_URL empty
    assert outcome.document_id == "workspace.documents:f-0123456789abcdef"
    stages = [u["stage"] for u in updates]
    assert stages[0] == "detected" and stages[-1] == "indexing"
    assert {"parsing", "chunking", "embedding"} <= set(stages)


def test_an_unreachable_parse_is_told_apart_from_a_disabled_one(pipeline: Pipeline, policy_pdf: Path):
    pipeline.probe = lambda settings: "connection refused"
    pipeline.settings = replace(pipeline.settings, parse_base_url="http://parse:8000/v1")

    outcome, _ = run(pipeline, policy_pdf, "policy.pdf", source_id="workspace.documents")

    assert outcome.fallback == "unavailable"


def test_a_stage_that_overruns_its_timeout_fails_the_file(
    pipeline: Pipeline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def slow(*args, **kwargs):
        time.sleep(3)

    monkeypatch.setattr(tables, "load_table_file", slow)
    pipeline.settings = replace(pipeline.settings, stage_timeout_s=0.5)
    started = time.monotonic()

    outcome, _ = run(pipeline, CATALOG_FIXTURES / "tables" / "orders.csv", "orders.csv", db_path=tmp_path / "t.duckdb")

    assert time.monotonic() - started < 2
    assert (outcome.stage, outcome.error_code) == ("failed", "timeout")
    assert "loading" in outcome.error_message


def test_without_an_embeddings_key_a_document_fails_before_parsing(
    settings: Settings, catalog: Catalog, index, tokenizer, policy_pdf: Path
):
    from demo_ingest.embed import Embedder

    probes: list[Settings] = []
    keyless = replace(settings, retriever_api_key=None)
    pipeline = Pipeline(
        keyless, catalog, None, embedder=Embedder(keyless), index=index, tokenizer=tokenizer, parse_probe=probes.append
    )

    outcome, _ = run(pipeline, policy_pdf, "policy.pdf", source_id="workspace.documents")

    assert (outcome.stage, outcome.error_code) == ("failed", "embedding_failed")
    assert probes == []


def test_a_file_of_the_wrong_kind_for_its_source_fails(pipeline: Pipeline, policy_pdf: Path):
    outcome, _ = run(pipeline, policy_pdf, "policy.pdf", source_id="mini.sales", expect_kind="table")

    assert (outcome.stage, outcome.error_code) == ("failed", "type_mismatch")


def test_an_unexpected_error_fails_the_file_with_a_message(
    pipeline: Pipeline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def broken(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(tables, "load_table_file", broken)

    outcome, _ = run(pipeline, CATALOG_FIXTURES / "tables" / "orders.csv", "orders.csv", db_path=tmp_path / "t.duckdb")

    assert (outcome.stage, outcome.error_code) == ("failed", "internal_error")
    assert "disk on fire" in outcome.error_message


def test_manifest_entries_and_statuses():
    row = {
        "file_id": "f-0123456789abcdef",
        "file_name": "a.pdf",
        "sha256": "a" * 64,
        "size_bytes": 3,
        "status": "success",
        "kind": "document",
        "parser": "nemotron-parse-2.0",
        "document_id": "workspace.documents:f-0123456789abcdef",
        "pages": 2,
        "chunks": 4,
        "tables": [],
        "warnings": ["x" * 900],
        "error_message": None,
    }

    entry = file_entry(row)

    assert entry["status"] == "ready" and entry["chunks"] == 4 and entry["pages"] == 2
    assert len(entry["warnings"][0]) == 500 and "tables" not in entry
    assert file_entry({**row, "status": "ingesting"})["status"] == "ingesting"
    assert source_status([]) == "empty"
    assert source_status([{"status": "ready"}, {"status": "ingesting"}]) == "ingesting"
    assert source_status([{"status": "ready"}, {"status": "failed"}]) == "ready"
    assert source_status([{"status": "failed"}]) == "failed"
    assert pipeline_module.database_alias("retail.sales") == "retail_sales"


def test_a_document_without_text_fails(pipeline: Pipeline, tmp_path: Path):
    blank = tmp_path / "blank.md"
    blank.write_text("\n\n   \n")

    outcome, _ = run(pipeline, blank, "blank.md", source_id="workspace.documents")

    assert (outcome.stage, outcome.error_code) == ("failed", "empty_file")
    assert "No text was found in blank.md" in outcome.error_message


def test_error_messages_lose_urls_paths_and_extra_lines():
    from demo_ingest.models import redact

    message = redact(
        "HTTPConnectionPool(host='milvus', port=19530): Max retries exceeded with url: http://milvus:19530/v2/x "
        "(/knowledge/sources/workspace.tables/tables.duckdb)\nTraceback (most recent call last):"
    )

    assert message == ("HTTPConnectionPool(host='milvus', port=19530): Max retries exceeded with url: <url> (<path>)")


def test_an_unexpected_error_does_not_carry_internal_paths(pipeline: Pipeline, tmp_path: Path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("cannot open /knowledge/sources/workspace.tables/tables.duckdb.building\nmore")

    monkeypatch.setattr(pipeline_module.tables, "load_table_file", broken)

    outcome, _ = run(pipeline, CATALOG_FIXTURES / "tables" / "orders.csv", "orders.csv", db_path=tmp_path / "t.duckdb")

    assert outcome.error_code == "internal_error"
    assert outcome.error_message == "Unexpected error: RuntimeError: cannot open <path>"


def test_a_documents_source_embedded_with_another_model_is_reported(
    pipeline: Pipeline, catalog: Catalog, caplog: pytest.LogCaptureFixture
):
    import json

    manifest = json.loads((CATALOG_FIXTURES / "sources" / "retail.policies.json").read_text())
    documents = {**manifest["documents"], "embed_model": "nvidia/other-embed"}
    catalog.write_source({**manifest, "documents": documents})  # a pack source: its next sync re-embeds it
    catalog.write_source({**manifest, "id": "workspace.documents", "pack_id": "workspace", "documents": documents})

    pipeline.warn_about_other_embed_models()

    assert "workspace.documents was embedded with nvidia/other-embed" in caplog.text
    assert "retail.policies" not in caplog.text
