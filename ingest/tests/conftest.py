# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures: repo paths, settings over a temporary knowledge root, and the catalog."""

from __future__ import annotations

from pathlib import Path

import pytest
from demo_ingest.catalog import Catalog
from demo_ingest.settings import Settings

REPO = Path(__file__).resolve().parents[2]
CATALOG_FIXTURES = REPO / "contracts" / "fixtures" / "catalog"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def knowledge_dir(tmp_path: Path) -> Path:
    path = tmp_path / "knowledge"
    path.mkdir()
    return path


@pytest.fixture
def settings(tmp_path: Path, knowledge_dir: Path) -> Settings:
    return Settings.from_env(
        {
            "KNOWLEDGE_DIR": str(knowledge_dir),
            "PACKS_DIR": str(tmp_path / "packs"),
            "MILVUS_URI": str(tmp_path / "milvus.db"),
            "PARSE_BASE_URL": "",
            "RETRIEVER_API_KEY": "nvapi-test-dummy",
        },
        secrets_dir=tmp_path / "no-secrets",
    )


@pytest.fixture
def catalog(knowledge_dir: Path) -> Catalog:
    return Catalog(knowledge_dir)


AWKWARD_HEADER = ["Store #", "Net Sales ($)", "Net Sales ($)", "Région"]


def write_awkward_xlsx(path: Path) -> Path:
    """Two sheets with spaces, symbols, a duplicate and a non-ASCII header; the second starts with blank rows."""
    import openpyxl

    workbook = openpyxl.Workbook()
    q1 = workbook.active
    q1.title = "Q1 Sales"
    q1.append(AWKWARD_HEADER)
    q1.append([1, 100.5, 90.0, "Nord"])
    q1.append([2, 200.0, 180.0, "Sud"])
    q2 = workbook.create_sheet("Q2 Sales")
    q2.append([None] * 4)
    q2.append([None] * 4)
    q2.append(AWKWARD_HEADER)
    q2.append([1, 110.5, 95.0, "Nord"])
    workbook.save(path)
    return path


@pytest.fixture
def awkward_xlsx(tmp_path: Path) -> Path:
    return write_awkward_xlsx(tmp_path / "awkward.xlsx")
