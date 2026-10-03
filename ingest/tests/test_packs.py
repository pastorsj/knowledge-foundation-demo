# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from conftest import FIXTURES
from conftest import REPO
from conftest import FakeEmbedder
from conftest import closed_port_url
from conftest import write_awkward_xlsx
from conftest import write_policy_pdf
from jsonschema import Draft202012Validator

from demo_ingest import packs
from demo_ingest.catalog import Catalog
from demo_ingest.index import KnowledgeIndex
from demo_ingest.pipeline import Pipeline
from demo_ingest.settings import Settings

SCHEMAS = REPO / "data" / "schemas"


@pytest.fixture
def packs_dir(tmp_path: Path) -> Path:
    path = tmp_path / "packs"
    shutil.copytree(FIXTURES / "packs", path)
    return path


@pytest.fixture
def pack_settings(settings: Settings, packs_dir: Path) -> Settings:
    return replace(settings, packs_dir=packs_dir)


@pytest.fixture
def index(tmp_path: Path) -> Iterator[KnowledgeIndex]:
    index = KnowledgeIndex(str(tmp_path / "milvus.db"), "knowledge")
    yield index
    index.close()


@pytest.fixture
def pipeline(pack_settings: Settings, catalog: Catalog, embedder: FakeEmbedder, index, tokenizer) -> Pipeline:
    return Pipeline(pack_settings, catalog, None, embedder=embedder, index=index, tokenizer=tokenizer)


def sync(settings: Settings, catalog: Catalog, pipeline: Pipeline) -> dict[str, dict]:
    pipeline.settings = settings
    return {status["id"]: status for status in packs.sync_all(settings, catalog, pipeline)}


@pytest.mark.parametrize(("name", "schema"), [("pack.yaml", "pack"), ("questions.yaml", "questions")])
def test_the_fixture_pack_satisfies_the_pack_format(name: str, schema: str):
    validator = Draft202012Validator(yaml.safe_load((SCHEMAS / f"{schema}.schema.json").read_text()))
    document = yaml.safe_load((FIXTURES / "packs" / "mini" / name).read_text())

    assert list(validator.iter_errors(document)) == []


def test_sync_writes_the_sources_and_the_pack(
    pack_settings: Settings, catalog: Catalog, pipeline: Pipeline, index: KnowledgeIndex
):
    statuses = sync(pack_settings, catalog, pipeline)

    assert statuses["mini"]["status"] == "ready"
    assert statuses["mini"]["files_total"] == statuses["mini"]["files_done"] == 3
    pack = catalog.read_pack("mini")
    assert (pack["kind"], pack["status"], pack["title"], pack["icon"]) == ("industry", "ready", "Mini Retail", "Store")
    assert (pack["as_of"], pack["version"]) == ("2026-09-30", "1.0.0")
    assert pack["disclaimer"].startswith("Synthetic data")
    assert pack["sources"] == ["mini.policies", "mini.sales"]
    assert len(pack["digest"]) == 64
    questions = {q["id"]: q for q in pack["questions"]}
    assert questions["fee-exposure"]["sources"] == ["mini.policies", "mini.sales"]
    assert questions["electronics-fee"]["featured"] is True and questions["top-customers"]["featured"] is False
    assert pack["examples"] == ["electronics-fee", "top-customers"]
    assert pack["conversations"][0]["sources"] == ["mini.policies", "mini.sales"]

    policies = catalog.read_source("mini.policies")
    assert (policies["pack_id"], policies["kind"], policies["status"]) == ("mini", "documents", "ready")
    assert policies["example_questions"] == ["What restocking fee applies to electronics?"]
    assert policies["documents"]["count"] == 1 and policies["documents"]["chunks"] > 0
    [document] = policies["files"]
    assert (document["parser"], document["status"]) == ("docling-md", "ready")
    assert index.count(source_id="mini.policies") == policies["documents"]["chunks"]
    assert (catalog.source_dir("mini.policies") / "files" / f"{document['file_id']}.md").exists()

    sales = catalog.read_source("mini.sales")
    assert sales["capabilities"] == ["structured_retrieval", "structured_prediction"]
    assert sales["database"]["path"] == "sources/mini.sales/tables.duckdb"
    assert sales["database"]["alias"] == "mini_sales"
    assert (catalog.root / sales["database"]["path"]).exists()
    tables = {t["name"]: t for t in sales["database"]["tables"]}
    assert tables["orders"]["description"] == "One row per order."
    assert tables["orders"]["foreign_keys"] == [
        {
            "column": "customer_id",
            "references_table": "customers",
            "references_column": "customer_id",
            "inferred": False,
        }
    ]
    assert {c["name"]: c["description"] for c in tables["orders"]["columns"]}["net_amount"].startswith("Order value")
    assert tables["customers"]["primary_key"] == "customer_id"
    assert sales["prediction"]["templates"][0]["id"] == "churn_90d"
    assert {f["file_name"]: f["tables"] for f in sales["files"]} == {
        "customers.csv": ["customers"],
        "orders.csv": ["orders"],
    }


def test_a_second_sync_is_a_no_op(pack_settings: Settings, catalog: Catalog, pipeline: Pipeline, embedder):
    sync(pack_settings, catalog, pipeline)
    first = catalog.read_pack("mini")
    embedder.calls.clear()

    statuses = sync(pack_settings, catalog, pipeline)

    assert statuses["mini"]["status"] == "ready"
    assert embedder.calls == []
    assert catalog.read_pack("mini") == first


def test_a_changed_file_is_ingested_again(
    pack_settings: Settings, catalog: Catalog, pipeline: Pipeline, index: KnowledgeIndex, packs_dir: Path
):
    sync(pack_settings, catalog, pipeline)
    [old] = catalog.read_source("mini.policies")["files"]
    (packs_dir / "mini" / "files" / "policies" / "returns.md").write_text("# Returns\n\nEverything is final sale.\n")

    sync(pack_settings, catalog, pipeline)

    [new] = catalog.read_source("mini.policies")["files"]
    assert new["file_id"] != old["file_id"]
    assert index.count(document_id=old["document_id"]) == 0
    assert index.count(source_id="mini.policies") == new["chunks"]
    assert [p.name for p in (catalog.source_dir("mini.policies") / "files").iterdir()] == [f"{new['file_id']}.md"]


def test_the_digest_covers_the_parser_and_embed_configuration(pack_settings: Settings, packs_dir: Path):
    pack_dir = packs_dir / "mini"
    disabled = packs.pack_digest(pack_dir, replace(pack_settings, parse_base_url=""))
    enabled = packs.pack_digest(pack_dir, replace(pack_settings, parse_base_url="http://parse:8000/v1"))
    other_model = packs.pack_digest(pack_dir, replace(pack_settings, parse_base_url="", embed_model="other/embed"))

    assert len({disabled, enabled, other_model}) == 3
    # The URL alone does not change what Parse produces.
    assert enabled == packs.pack_digest(pack_dir, replace(pack_settings, parse_base_url="http://elsewhere:8000/v1"))


def test_turning_parse_on_ingests_the_pack_again(
    pack_settings: Settings, catalog: Catalog, pipeline: Pipeline, embedder
):
    sync(replace(pack_settings, parse_base_url=""), catalog, pipeline)
    embedder.calls.clear()

    sync(replace(pack_settings, parse_base_url=closed_port_url()), catalog, pipeline)

    assert embedder.calls != []


def test_a_text_layer_fallback_from_an_unreachable_parse_is_retried_next_sync(
    pack_settings: Settings, catalog: Catalog, pipeline: Pipeline, embedder, packs_dir: Path
):
    write_policy_pdf(packs_dir / "mini" / "files" / "policies" / "policy.pdf")
    settings = replace(pack_settings, parse_base_url=closed_port_url())

    statuses = sync(settings, catalog, pipeline)

    pack = catalog.read_pack("mini")
    assert statuses["mini"]["status"] == pack["status"] == "ready"
    assert "digest" not in pack
    pdf = next(f for f in catalog.read_source("mini.policies")["files"] if f["file_name"] == "policy.pdf")
    assert pdf["parser"] == "pdf-text-layer" and pdf["warnings"][0].startswith("Parsed from the PDF's text layer")
    embedder.calls.clear()

    sync(settings, catalog, pipeline)

    assert embedder.calls != []


def test_a_text_layer_fallback_with_parse_disabled_is_complete(
    pack_settings: Settings, catalog: Catalog, pipeline: Pipeline, embedder, packs_dir: Path
):
    write_policy_pdf(packs_dir / "mini" / "files" / "policies" / "policy.pdf")
    settings = replace(pack_settings, parse_base_url="")
    sync(settings, catalog, pipeline)
    assert len(catalog.read_pack("mini")["digest"]) == 64
    embedder.calls.clear()

    sync(settings, catalog, pipeline)

    assert embedder.calls == []


def test_an_invalid_pack_fails_alone(pack_settings: Settings, catalog: Catalog, pipeline: Pipeline, packs_dir: Path):
    (packs_dir / "broken").mkdir()
    (packs_dir / "broken" / "pack.yaml").write_text("schema_version: '3'\nid: broken\n")

    statuses = sync(pack_settings, catalog, pipeline)

    assert statuses["mini"]["status"] == "ready"
    assert statuses["broken"]["status"] == "failed"
    assert "pack.yaml" in statuses["broken"]["error"]
    assert catalog.read_pack("broken") is None
    assert packs.sync_cli(pack_settings, pipeline=pipeline) == 1


def test_sync_cli_succeeds_on_valid_packs(pack_settings: Settings, pipeline: Pipeline, catalog: Catalog):
    assert packs.sync_cli(pack_settings, pipeline=pipeline) == 0
    assert catalog.read_pack("mini")["status"] == "ready"


def test_a_multi_sheet_workbook_in_a_pack_loads(
    pack_settings: Settings, catalog: Catalog, pipeline: Pipeline, packs_dir: Path
):
    pack_yaml = packs_dir / "mini" / "pack.yaml"
    pack = yaml.safe_load(pack_yaml.read_text())
    pack["sources"][1]["files"] = ["files/sales/*"]
    pack_yaml.write_text(yaml.safe_dump(pack, sort_keys=False))
    write_awkward_xlsx(packs_dir / "mini" / "files" / "sales" / "credit_risk_report.xlsx")

    statuses = sync(pack_settings, catalog, pipeline)

    assert statuses["mini"]["status"] == "ready", statuses["mini"]["error"]
    sales = catalog.read_source("mini.sales")
    workbook = next(f for f in sales["files"] if f["file_name"] == "credit_risk_report.xlsx")
    assert workbook["tables"] == ["credit_risk_report_q1_sales", "credit_risk_report_q2_sales"]
    stored = sorted(p.name for p in (catalog.source_dir("mini.sales") / "files").iterdir())
    assert stored == sorted(f"{f['file_id']}{Path(f['file_name']).suffix}" for f in sales["files"])
