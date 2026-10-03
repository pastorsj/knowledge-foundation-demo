# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Corpus builders on small offline fixtures (downloads are served from a pre-filled cache or a fake server)."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest
from conftest import FIXTURES
from conftest import MINUTE_BARS

from demo_data import corpus
from demo_data import external
from demo_data.corpus import ecfr
from demo_data.corpus import edgar
from demo_data.corpus import federal_register
from demo_data.corpus import gdelt
from demo_data.corpus import markdown
from demo_data.corpus.common import CorpusError
from demo_data.corpus.common import Downloads
from demo_data.pack import load_pack

NOTES = FIXTURES / "packs" / "notes"


def cached(downloads: Downloads, fixture: Path) -> str:
    """Put a fixture in the download cache, as if fetched earlier; returns its sha256."""
    digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
    downloads.path(digest).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture, downloads.path(digest))
    return digest


def write_manifest(path: Path, manifest: dict) -> Path:
    path.write_text(json.dumps(manifest))
    return path


def test_ecfr_one_document_per_section_with_its_headings(tmp_path):
    downloads = Downloads(tmp_path / "downloads")
    url = "https://www.ecfr.gov/api/versioner/v1/full/2026-08-17/title-17.xml"
    digest = cached(downloads, FIXTURES / "ecfr" / "title-17-mini.xml")
    manifest = write_manifest(
        tmp_path / "ecfr.json",
        {
            "source_id": "market_regulations",
            "as_of": "2026-08-17",
            "document": {
                "url": url,
                "sha256": digest,
                "title_number": "17",
                "title": "Title 17—Commodity and Securities Exchanges",
            },
        },
    )

    section, reserved = ecfr.documents("market_regulations", manifest, downloads)

    assert section.document_id.startswith("ecfr-title17:229.106:")
    assert section.title == "§ 229.106 (Item 106) Cybersecurity."
    assert section.url == "https://www.ecfr.gov/on/2026-08-17/title-17/section-229.106"
    assert section.text.split("\n\n")[:4] == [
        "Title 17—Commodity and Securities Exchanges",
        "CHAPTER II—SECURITIES AND EXCHANGE COMMISSION",
        "PART 229—STANDARD INSTRUCTIONS FOR FILING FORMS",
        "Subpart 229.100—Business",
    ]
    assert "cybersecurity incident means an unauthorized occurrence" in section.text
    assert section.metadata | {"snapshot_url": None} == {
        "citation": "17 CFR § 229.106",
        "title_number": "17",
        "chapter": "II",
        "chapter_heading": "CHAPTER II—SECURITIES AND EXCHANGE COMMISSION",
        "part": "229",
        "part_heading": "PART 229—STANDARD INSTRUCTIONS FOR FILING FORMS",
        "subpart_heading": "Subpart 229.100—Business",
        "section": "229.106",
        "as_of": "2026-08-17",
        "snapshot_url": None,
    }
    # A reserved range has no page of its own, so it links to its part.
    assert reserved.url == "https://www.ecfr.gov/on/2026-08-17/title-17/part-229"


def test_ecfr_rejects_another_title(tmp_path):
    with pytest.raises(CorpusError, match="not title 16"):
        ecfr.parse_title(
            FIXTURES / "ecfr" / "title-17-mini.xml",
            source_id="s",
            title_number="16",
            title_heading="Title 16",
            as_of="2026-08-17",
            snapshot_url="https://example.com",
        )


RULE = {
    "url": "https://www.federalregister.gov/documents/full_text/xml/2023/08/04/2023-16194.xml",
    "html_url": "https://www.federalregister.gov/documents/2023/08/04/2023-16194/cybersecurity-risk-management",
    "document_number": "2023-16194",
    "citation": "88 FR 51896",
    "published_on": "2023-08-04",
    "title": "Cybersecurity Risk Management, Strategy, Governance, and Incident Disclosure",
    "release": "Release Nos. 33-11216; 34-97989",
}


def test_federal_register_keeps_the_summary_and_each_appendix_of_form_text(tmp_path):
    downloads = Downloads(tmp_path / "downloads")
    digest = cached(downloads, FIXTURES / "federal-register" / "rule-mini.xml")
    pin = RULE | {"sha256": digest}
    manifest = write_manifest(tmp_path / "fr.json", {"source_id": "market_regulations", "document": pin})

    summary, form_8k, form_10k = federal_register.documents("market_regulations", manifest, downloads)

    assert [summary.document_id, form_8k.document_id, form_10k.document_id] == [
        "fr-2023-16194:summary",
        "fr-2023-16194:appendix-c-form-8-k",
        "fr-2023-16194:appendix-d-form-10-k",
    ]
    assert form_8k.title == f"{RULE['title']}: Appendix C—Form 8-K"
    assert form_8k.text.split("\n\n") == [
        f"{RULE['title']}. Final rule, {RULE['release']}, 88 FR 51896 (August 4, 2023). Appendix C—Form 8-K.",
        "FORM 8-K",
        "* * *",
        "B. Events To Be Reported and Time for Filing of Reports",
        "1. A report pursuant to Item 1.05 is to be filed within four business days after the registrant determines "
        "that it has experienced a material cybersecurity incident.",
        "Item 1.05 Material Cybersecurity Incidents",
        "(a) If the registrant experiences a cybersecurity incident that is determined by the registrant to be "
        "material, describe the material aspects of the nature, scope, and timing of the incident.",
    ]
    assert (form_8k.url, form_8k.published_at) == (RULE["html_url"], "2023-08-04T00:00:00Z")
    assert form_8k.metadata == {
        "citation": "88 FR 51896 (August 4, 2023), Appendix C—Form 8-K",
        "document_number": "2023-16194",
        "release": "Release Nos. 33-11216; 34-97989",
        "part": "Appendix C—Form 8-K",
        "published_on": "2023-08-04",
        "snapshot_url": RULE["url"],
    }
    # The summary and dates, without footnote markers; the preamble's discussion is left out.
    assert summary.text.split("\n\n")[1:] == [
        "SUMMARY:",
        "The Commission is adopting amendments to require current disclosure about material cybersecurity incidents.",
        "DATES:",
        "Effective date: The amendments are effective September 5, 2023.",
    ]
    assert not any("long discussion" in document.text for document in (summary, form_8k, form_10k))


def test_federal_register_rejects_another_rule(tmp_path):
    with pytest.raises(CorpusError, match="not the rule the manifest names"):
        federal_register.parse_rule(
            FIXTURES / "federal-register" / "rule-mini.xml", source_id="s", pin=RULE | {"title": "Another rule"}
        )


def test_the_pinned_rule_manifests_name_the_form_8k_deadline_source():
    """Both packs pin the same rule; its sha256 is the federalregister.gov full-text XML of 2023-16194."""
    for name in ("synthetic-market", "us-equities"):
        pack = load_pack(Path(__file__).parents[1] / "packs" / name)
        (rule,) = [c for c in pack.manifest["documents"]["corpora"] if c["format"] == "federal-register-xml"]
        pin = json.loads(pack.path(rule["manifest"]).read_text())["document"]
        assert (rule["source"], pin["document_number"], pin["citation"]) == (
            "market_regulations",
            "2023-16194",
            RULE["citation"],
        )
        assert pin["sha256"] == "c1f5314824b4f97e08bc791a0098f33a37b7d65198c392a7b3652c43ac42119e"


@pytest.fixture
def edgar_manifest(tmp_path) -> tuple[Path, Downloads]:
    downloads = Downloads(tmp_path / "downloads")
    filing = {
        "filing_id": "0000000001:0000000001-26-000001",
        "company_name": "EXAMPLE CORP",
        "form": "8-K",
        "filed_on": "2026-04-01",
        "source_url": "https://www.sec.gov/Archives/edgar/data/1/0000000001-26-000001.txt",
        "sha256": cached(downloads, FIXTURES / "edgar" / "submission.txt"),
        "ticker": "EXMP",
        "items": "1.05,9.01",
    }
    return write_manifest(tmp_path / "edgar.json", {"source_id": "sec_filings", "filings": [filing]}), downloads


def test_edgar_keeps_the_primary_document_and_ex99_exhibits(edgar_manifest, monkeypatch):
    manifest, downloads = edgar_manifest
    monkeypatch.setenv("SEC_USER_AGENT", "Example Co admin@example.com")

    primary, exhibit = edgar.documents("sec_filings", manifest, downloads)

    assert primary.document_id == "edgar:0000000001:0000000001-26-000001:example-8k.htm"
    assert primary.title == "EXAMPLE CORP 8-K: CURRENT REPORT"
    assert primary.text.startswith("Item 1.05 Material Cybersecurity Incidents. On March 30, 2026")
    assert "track()" not in primary.text and "color" not in primary.text
    assert primary.published_at == "2026-04-01T00:00:00Z"
    assert primary.metadata["citation"] == "SEC 8-K, EXAMPLE CORP, filed 2026-04-01, accession 0000000001-26-000001"
    assert (primary.metadata["ticker"], primary.metadata["items"]) == ("EXMP", "1.05,9.01")  # citation metadata only
    assert exhibit.metadata["document_type"] == "EX-99.1"
    assert exhibit.text.startswith("Example Corp announces that its operations were restored on April 1, 2026")


def test_edgar_text_keeps_no_control_characters():
    # PDF-extracted exhibits carry stray control characters, which the receipt schema refuses downstream.
    assert edgar.plain_text("Item\x031.05  Material\x7f\r\nIncidents\x00") == "Item 1.05 Material Incidents"


def test_edgar_needs_a_user_agent(edgar_manifest, monkeypatch):
    manifest, downloads = edgar_manifest
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    with pytest.raises(CorpusError, match="SEC_USER_AGENT"):
        edgar.documents("sec_filings", manifest, downloads)


def test_markdown_files_become_documents_with_front_matter_as_metadata():
    first, second = markdown.documents("notes", NOTES / "documents" / "manifest.json", None)

    assert (first.document_id, second.document_id) == ("note-first", "note-second")
    assert first.title == "First note"
    assert first.published_at == "2026-09-01T12:00:00+00:00"
    assert first.text.startswith("# First note")
    assert first.metadata == {"citation": "First note", "asset_id": "asset-one"}


def test_gdelt_headlines_are_read_in_place_from_the_verified_dataset(equities, tmp_path):
    pack = load_pack(equities / "us-equities")  # pinned to the fixture dataset
    world_news = pack.select_corpora(["world_news"])[0]
    dataset = external.datasets(pack.manifest, tmp_path / "sources")["minute-bars"]
    shutil.copytree(MINUTE_BARS, dataset.root)

    with pytest.raises(external.ExternalError, match="not verified yet"):
        corpus.dataset_files(pack, world_news, tmp_path / "sources")
    external.verify(dataset)
    files = corpus.dataset_files(pack, world_news, tmp_path / "sources")
    rates, strike = gdelt.documents("world_news", files)  # the blank headline is skipped

    assert files == [dataset.root / "gdelt" / "headlines.parquet"]
    assert rates.document_id == "gdelt:20260102140000-1"
    assert (rates.title, rates.text) == ("Central bank holds rates steady", "Central bank holds rates steady")
    assert (rates.url, strike.url) == ("https://example.com/rates", None)  # only https links are kept
    assert rates.published_at == "2026-01-02T14:00:00Z"
    assert rates.metadata == {
        "citation": "example.com, 2026-01-02 14:00 UTC (GDELT)",
        "source_domain": "example.com",
        "tone": -1.5,
        "topic": "1_bank_rates",
    }


def test_build_writes_sorted_valid_rows(tmp_path):
    notes = load_pack(NOTES)
    counts = corpus.build(notes, notes.select_corpora(None), Downloads(tmp_path), tmp_path, sources_dir=tmp_path)

    rows = [json.loads(line) for line in (tmp_path / "corpus" / "documents.jsonl").read_text().splitlines()]
    assert counts == {"notes": 2}
    assert [row["document_id"] for row in rows] == sorted(row["document_id"] for row in rows)
    assert list(rows[0]) == ["document_id", "source_id", "title", "text", "url", "published_at", "metadata"]


def test_document_rows_follow_the_schema():
    row = asdict(markdown.documents("notes", NOTES / "documents" / "manifest.json", None)[0])
    bad = [
        row | {"url": "http://example.com"},
        row | {"metadata": row["metadata"] | {"image": "chart.png"}},
        row | {"metadata": {"asset_id": "ACME"}},
    ]

    errors = corpus.document_errors([row, row, *bad])

    assert [error.split(": ")[1] for error in errors] == ["url", "metadata", "metadata", "duplicate document_id"]


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, encoding: str | None = None) -> None:
        super().__init__(body)
        self.headers = {"Content-Encoding": encoding} if encoding else {}


def test_downloads_are_checked_and_cached(tmp_path, monkeypatch):
    body = b"pinned content"
    digest = hashlib.sha256(body).hexdigest()
    requests = []

    def urlopen(request, timeout):
        requests.append(request.full_url)
        return FakeResponse(gzip.compress(body), "gzip")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    downloads = Downloads(tmp_path)

    path = downloads.fetch("https://example.com/file", digest, user_agent="test", max_bytes=1000)
    assert path.read_bytes() == body
    assert downloads.fetch("https://example.com/file", digest, user_agent="test", max_bytes=1000) == path
    assert requests == ["https://example.com/file"]

    with pytest.raises(CorpusError, match="pinned"):
        downloads.fetch("https://example.com/other", "0" * 64, user_agent="test", max_bytes=1000)
    assert sorted(p.name for p in (tmp_path / "sha256").iterdir()) == [digest]
