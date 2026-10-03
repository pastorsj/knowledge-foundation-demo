# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from conftest import CATALOG_FIXTURES
from demo_ingest.catalog import Catalog
from demo_ingest.catalog import CatalogError
from demo_ingest.settings import Settings


def fixtures(kind: str) -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted((CATALOG_FIXTURES / kind).glob("*.json"))]


@pytest.mark.parametrize("manifest", fixtures("sources"), ids=lambda m: m["id"])
def test_every_source_fixture_round_trips(catalog: Catalog, manifest: dict):
    catalog.write_source(manifest)

    assert catalog.read_source(manifest["id"]) == manifest
    assert manifest["id"] in [source["id"] for source in catalog.sources()]
    path = catalog.root / "catalog" / "sources" / f"{manifest['id']}.json"
    # Other services read the catalog under their own uid.
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


@pytest.mark.parametrize("manifest", fixtures("packs"), ids=lambda m: m["id"])
def test_every_pack_fixture_round_trips(catalog: Catalog, manifest: dict):
    catalog.write_pack(manifest)

    assert catalog.read_pack(manifest["id"]) == manifest
    assert [pack["id"] for pack in catalog.packs()] == [manifest["id"]]


def test_an_invalid_manifest_is_refused_and_leaves_no_file(catalog: Catalog):
    manifest = fixtures("sources")[0]
    del manifest["kind"]

    with pytest.raises(CatalogError, match="kind"):
        catalog.write_source(manifest)

    assert list((catalog.root / "catalog" / "sources").iterdir()) == []
    assert catalog.read_source(manifest["id"]) is None


def test_an_invalid_rewrite_keeps_the_previous_manifest(catalog: Catalog):
    manifest = fixtures("sources")[0]
    catalog.write_source(manifest)

    with pytest.raises(CatalogError):
        catalog.write_source({**manifest, "status": "unknown"})

    assert catalog.read_source(manifest["id"]) == manifest
    assert [p.name for p in (catalog.root / "catalog" / "sources").iterdir()] == [f"{manifest['id']}.json"]


def test_delete_source_removes_the_manifest_and_its_files(catalog: Catalog):
    manifest = fixtures("sources")[0]
    catalog.write_source(manifest)
    (catalog.source_dir(manifest["id"]) / "files").mkdir(parents=True)

    catalog.delete_source(manifest["id"])

    assert catalog.read_source(manifest["id"]) is None
    assert not catalog.source_dir(manifest["id"]).exists()
    catalog.delete_source(manifest["id"])  # idempotent


@pytest.mark.parametrize("bad", ["../etc", "retail/x", "", "Retail.Sales"])
def test_ids_that_could_escape_the_root_are_refused(catalog: Catalog, bad: str):
    with pytest.raises(CatalogError):
        catalog.source_dir(bad)
    assert catalog.read_source(bad) is None


def test_settings_defaults(tmp_path: Path):
    settings = Settings.from_env({}, secrets_dir=tmp_path)

    assert settings.knowledge_dir == Path("/knowledge")
    assert settings.packs_dir == Path("/packs")
    assert settings.collection_alias == "knowledge"
    assert settings.parse_base_url == "http://parse:8000/v1"
    assert settings.parse_model == "nvidia/NVIDIA-Nemotron-Parse-2.0"
    assert settings.parse_api_key is None
    assert settings.embed_model == "nvidia/nemotron-3-embed-1b"
    assert (settings.workers, settings.max_file_mb, settings.max_files) == (2, 100, 20)
    assert (settings.parse_concurrency, settings.stage_timeout_s) == (4, 1800)


def test_settings_read_secret_files_and_an_empty_parse_url_disables_parse(tmp_path: Path):
    (tmp_path / "parse_api_key").write_text("parse-key\n")
    (tmp_path / "retriever_api_key").write_text("nvapi-secret\n")

    settings = Settings.from_env({"PARSE_BASE_URL": "", "INGEST_WORKERS": "3"}, secrets_dir=tmp_path)

    assert settings.parse_base_url == ""
    assert settings.parse_api_key == "parse-key"
    assert settings.retriever_api_key == "nvapi-secret"
    assert settings.workers == 3
    assert "nvapi-secret" not in repr(settings)
