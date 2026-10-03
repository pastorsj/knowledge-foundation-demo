# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The demo-data commands end to end, on packs small enough to build offline in milliseconds."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FIXTURES
from conftest import PACKS

from demo_data import corpus
from demo_data import structured
from demo_data.cli import main


@pytest.fixture
def run(tmp_path, capsys):
    """Run demo-data against tmp_path/data; returns (exit code, stdout, stderr)."""

    def run(*args: str, packs: Path = FIXTURES / "packs") -> tuple[int, str, str]:
        code = main(["--packs-dir", str(packs), "--data-dir", str(tmp_path / "data"), *args])
        out, err = capsys.readouterr()
        return code, out, err

    return run


@pytest.fixture
def no_tables(monkeypatch):
    """Skip the structured build (generator, DuckDB, ontology) in tests about what pack.json lists."""
    monkeypatch.setattr(structured, "build", lambda pack, profile, contracts, out, **_: {"rows": {"assets": 0}})


@pytest.fixture
def no_documents(monkeypatch):
    """Skip the corpus downloads in tests about which part a build holds."""
    monkeypatch.setattr(corpus, "build", lambda pack, corpora, downloads, out, **_: {c["source"]: 0 for c in corpora})


def active(tmp_path: Path) -> Path:
    return (tmp_path / "data" / "active").resolve()


def active_pack(tmp_path: Path) -> dict:
    return json.loads((active(tmp_path) / "pack.json").read_text())


def test_prepare_builds_publishes_and_then_reuses(run, tmp_path):
    code, out, _ = run("--pack", "notes", "prepare")
    assert code == 0
    assert "corpus: built" in out and "active -> builds/notes@0.1.0+default+" in out

    build = active(tmp_path)
    pack = json.loads((build / "pack.json").read_text())
    assert pack["build"] == build.name
    assert [question["id"] for question in pack["questions"]] == ["what-happened"]
    assert pack["parts"]["corpus"]["documents"] == {"notes": 2}
    assert set(pack["parts"]["corpus"]["files"]) == {"corpus/documents.jsonl"}
    assert len((build / "corpus" / "documents.jsonl").read_text().splitlines()) == 2

    code, out, _ = run("--pack", "notes", "prepare")
    assert (code, out.splitlines()[0]) == (0, f"corpus: up to date in {build.name}")
    assert run("verify")[:2] == (0, f"{build.name}: verified\n")


def test_a_changed_pack_gets_a_new_build_and_clean_keeps_only_the_active_one(run, tmp_path, pack_copy):
    packs = pack_copy("notes", FIXTURES / "packs").parent
    run("--pack", "notes", "prepare", packs=packs)
    first = active(tmp_path)
    (packs / "notes" / "documents" / "second.md").write_text(
        "---\ndocument_id: note-second\n---\n\n# Second\n\nEdited.\n"
    )

    run("--pack", "notes", "prepare", packs=packs)

    assert active(tmp_path) != first
    code, out, _ = run("clean", packs=packs)
    assert (code, out) == (0, f"removed {first}\n")
    assert [path.name for path in (tmp_path / "data" / "builds").iterdir()] == [active(tmp_path).name]


def test_verify_notices_a_changed_file(run, tmp_path):
    run("--pack", "notes", "prepare")
    (active(tmp_path) / "corpus" / "documents.jsonl").write_text("{}\n")

    code, _, err = run("verify")

    assert code == 1
    assert "corpus/documents.jsonl changed after it was built" in err


def test_a_structured_build_lists_no_documents_until_its_corpus_is_built(run, tmp_path, no_tables):
    code, _, _ = run("prepare", "--structured", packs=PACKS)  # synthetic-market, with SEC filings and regulations

    assert code == 0
    pack = active_pack(tmp_path)
    assert [source["id"] for source in pack["sources"]] == ["market_data"]
    needed = {source for question in pack["questions"] for source in question["sources"]}
    assert needed == {"market_data"}
    assert "documents" not in pack


def test_a_corpus_alone_fails_until_the_same_build_gets_its_structured_part(run, tmp_path, no_tables, no_documents):
    code, out, err = run("prepare", "--corpus", "--corpora", "market_regulations", packs=PACKS)

    assert code == 1
    assert "corpus: built" in out and "has no structured part" in err
    assert not (tmp_path / "data" / "active").exists()

    assert run("prepare", "--structured", "--corpora", "market_regulations", packs=PACKS)[0] == 0
    pack = active_pack(tmp_path)
    assert [source["id"] for source in pack["sources"]] == ["market_data", "market_regulations"]
    assert pack["documents"]["sources"] == ["market_regulations"]
    assert set(pack["parts"]) == {"corpus", "structured"}


def test_prepare_fails_fast_without_a_required_variable(run, tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    code, _, err = run("prepare", "--corpus", packs=PACKS)

    assert code == 1
    hint = "sec_filings needs SEC_USER_AGENT (or skip it: set DATA_CORPORA=market_regulations for both"
    assert hint in err
    assert not (tmp_path / "data" / "builds").exists()


def test_validate_and_list(run):
    code, out, _ = run("--pack", "notes", "validate")
    assert (code, out) == (0, "notes@0.1.0: valid\n  0 tables, 1 sources, 1 questions\n")

    code, out, _ = run("list")
    assert code == 0
    assert "  notes@0.1.0  Test notes  (profiles: default)" in out


def test_validate_reports_errors_and_exits_nonzero(run, pack_copy):
    packs = pack_copy("notes", FIXTURES / "packs").parent
    (packs / "notes" / "documents" / "manifest.json").write_text('{"source_id": "other", "files": []}')

    code, _, err = run("--pack", "notes", "validate", packs=packs)

    assert code == 1
    assert "corpus notes: manifest documents/manifest.json does not declare source_id 'notes'" in err
