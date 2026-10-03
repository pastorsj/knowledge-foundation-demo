# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from conftest import CATALOG_FIXTURES

from demo_ingest.detect import Detected
from demo_ingest.detect import detect
from demo_ingest.models import IngestError


def code(path: Path, name: str, **kwargs) -> str:
    with pytest.raises(IngestError) as error:
        detect(path, name, **kwargs)
    return error.value.code


@pytest.fixture
def zip_file(tmp_path: Path) -> Path:
    path = tmp_path / "archive.bin"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("a.txt", "hello")
    return path


def test_a_zip_renamed_pdf_is_a_type_mismatch(zip_file: Path):
    assert code(zip_file, "report.pdf") == "type_mismatch"


def test_a_zip_renamed_csv_is_a_type_mismatch(zip_file: Path):
    # Text formats are not sniffed for what they are, only for what they must not be.
    assert code(zip_file, "orders.csv") == "type_mismatch"


def test_a_pdf_renamed_xlsx_is_a_type_mismatch(policy_pdf: Path):
    assert code(policy_pdf, "sales.xlsx") == "type_mismatch"


def test_an_empty_file_is_refused(tmp_path: Path):
    (tmp_path / "empty").write_bytes(b"")
    assert code(tmp_path / "empty", "notes.pdf") == "empty_file"
    assert code(tmp_path / "empty", "orders.csv") == "empty_file"


@pytest.mark.parametrize("name", ["setup.exe", "archive.zip", "README", "legacy.xls", "old.doc"])
def test_an_unknown_extension_is_unsupported(tmp_path: Path, name: str):
    (tmp_path / "file").write_bytes(b"MZ\x90\x00")
    assert code(tmp_path / "file", name) == "unsupported_type"


def test_a_file_over_the_limit_is_too_large(policy_pdf: Path):
    assert code(policy_pdf, "policy.pdf", max_bytes=100) == "too_large"


def test_documents_and_tables_are_told_apart(policy_pdf: Path, scan_png: Path, docx_file: Path, awkward_xlsx: Path):
    assert detect(policy_pdf, "Policy.PDF") == Detected(kind="document", format="pdf")
    assert detect(scan_png, "scan.png") == Detected(kind="document", format="png")
    assert detect(scan_png, "scan.jpg") == Detected(kind="document", format="png")  # any image is an image
    assert detect(docx_file, "operations.docx") == Detected(kind="document", format="docx")
    assert detect(awkward_xlsx, "awkward.xlsx") == Detected(kind="table", format="xlsx")
    assert detect(CATALOG_FIXTURES / "tables" / "orders.csv", "orders.csv") == Detected(kind="table", format="csv")


def test_text_formats_are_accepted_without_a_signature(tmp_path: Path):
    for name, text in [("a.md", "# Title\n"), ("a.txt", "plain"), ("a.html", "<p>x</p>"), ("a.jsonl", '{"a": 1}\n')]:
        (tmp_path / name).write_text(text)
        assert detect(tmp_path / name, name).kind == ("table" if name.endswith("jsonl") else "document")


def test_a_parquet_file_needs_its_magic_bytes(tmp_path: Path):
    (tmp_path / "x").write_bytes(b"PAR1" + b"\x00" * 16 + b"PAR1")
    (tmp_path / "y").write_bytes(b"not parquet at all")

    assert detect(tmp_path / "x", "x.parquet") == Detected(kind="table", format="parquet")
    assert code(tmp_path / "y", "y.parquet") == "type_mismatch"
