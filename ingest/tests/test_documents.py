# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import io
import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import FakeEmbedder
from conftest import FakeParse
from conftest import closed_port_url

from demo_ingest import documents
from demo_ingest.catalog import Catalog
from demo_ingest.index import KnowledgeIndex
from demo_ingest.models import IngestError
from demo_ingest.settings import Settings

TEXT_LAYER_WARNING = "Parsed from the PDF's text layer: Nemotron Parse was unavailable ("


@pytest.fixture
def progress() -> list[tuple[int, int]]:
    return []


def test_parse_converts_a_pdf_into_pages_and_a_table(
    parse_settings: Settings, fake_parse: FakeParse, policy_pdf: Path, progress: list
):
    fake_parse.label = "policy-pdf"

    converted = documents.convert(policy_pdf, "pdf", parse_settings, lambda i, n: progress.append((i, n)))

    assert converted.parser == "nemotron-parse-2.0"
    assert converted.pages == 2 and len(converted.document.pages) >= 2
    assert len(converted.document.tables) == 1
    assert converted.warnings == []
    assert progress[-1] == (2, 2)
    markdown = converted.document.export_to_markdown()
    assert "Restocking fees" in markdown and "Clearance" in markdown
    # The model card's request parameters, with max_tokens inside the local vLLM's served context (9000)
    assert len(fake_parse.requests) == 2
    request = fake_parse.requests[0]
    assert request["model"] == "nvidia/NVIDIA-Nemotron-Parse-2.0"
    assert (request["skip_special_tokens"], request["top_k"], request["repetition_penalty"]) == (False, 1, 1.1)
    assert (request["temperature"], request["max_tokens"]) == (0, 8192)


def test_the_output_cap_follows_the_setting(parse_settings: Settings, fake_parse: FakeParse, policy_pdf: Path):
    fake_parse.label = "policy-pdf"

    # build.nvidia.com serves Parse with a 4096-token context and refuses max_tokens=8192 with HTTP 400
    converted = documents.convert(policy_pdf, "pdf", replace(parse_settings, parse_max_tokens=4096), None)

    assert converted.parser == "nemotron-parse-2.0"
    assert {request["max_tokens"] for request in fake_parse.requests} == {4096}


def _sent_image_sizes(fake_parse: FakeParse) -> list[tuple[int, int]]:
    from PIL import Image

    sizes = []
    for request in fake_parse.requests:
        url = request["messages"][0]["content"][0]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        sizes.append(Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).size)
    return sizes


@pytest.mark.parametrize("size", [(1404, 1794), (1794, 1404), (918, 1188)], ids=["portrait", "landscape", "small"])
def test_page_images_stay_inside_the_model_cards_resolution(
    parse_settings: Settings, fake_parse: FakeParse, tmp_path: Path, size: tuple[int, int]
):
    """An image without DPI metadata would be sent at twice its size (the preset's scale); a 1404x1794 scan then
    made a 10.9 MB request that build.nvidia.com refused with HTTP 413."""
    from PIL import Image
    from PIL import ImageDraw

    fake_parse.label = "scan-png"
    image = Image.new("L", size, 255)
    ImageDraw.Draw(image).text((40, 40), "Clearance items are final sale.", fill=0)
    image.save(tmp_path / "scan.png", format="PNG")

    documents.convert(tmp_path / "scan.png", "png", parse_settings, None)

    [(width, height)] = _sent_image_sizes(fake_parse)
    assert width <= 1664 and height <= 2048 and max(width, height) == 1664  # scaled up to the cap, never past it
    assert abs(width / height - size[0] / size[1]) < 0.01


def test_pdf_pages_keep_the_presets_resolution(parse_settings: Settings, fake_parse: FakeParse, policy_pdf: Path):
    fake_parse.label = "policy-pdf"

    documents.convert(policy_pdf, "pdf", parse_settings, None)

    # A Letter page at the preset's scale (2.0, 144 dpi) already fits, so the recorded responses still match
    assert _sent_image_sizes(fake_parse) == [(1224, 1584), (1224, 1584)]


def test_parse_reads_an_image(parse_settings: Settings, fake_parse: FakeParse, scan_png: Path):
    fake_parse.label = "scan-png"

    converted = documents.convert(scan_png, "png", parse_settings, None)

    assert converted.parser == "nemotron-parse-2.0"
    assert "Clearance" in converted.document.export_to_markdown()


@pytest.mark.parametrize("parse_url", ["closed", ""], ids=["unreachable", "disabled"])
def test_without_parse_a_pdf_falls_back_to_its_text_layer(settings: Settings, policy_pdf: Path, parse_url: str):
    settings = replace(settings, parse_base_url=closed_port_url() if parse_url == "closed" else "")

    converted = documents.convert(policy_pdf, "pdf", settings, None)

    assert converted.parser == "pdf-text-layer"
    assert converted.pages == 2
    [warning] = converted.warnings
    assert warning.startswith(TEXT_LAYER_WARNING) and warning.endswith(").")
    markdown = converted.document.export_to_markdown()
    assert "## Page 2" in markdown and "Clearance items are final sale" in markdown


def test_parse_errors_fall_back_to_the_text_layer(parse_settings: Settings, fake_parse: FakeParse, policy_pdf: Path):
    fake_parse.fail_status = 400

    converted = documents.convert(policy_pdf, "pdf", parse_settings, None)

    assert converted.parser == "pdf-text-layer"
    assert converted.warnings[0].startswith(TEXT_LAYER_WARNING)


def test_a_slow_parse_falls_back_within_the_stage_timeout(
    parse_settings: Settings, fake_parse: FakeParse, policy_pdf: Path
):
    fake_parse.delay = 5
    started = time.monotonic()

    converted = documents.convert(policy_pdf, "pdf", replace(parse_settings, stage_timeout_s=2), None)

    assert time.monotonic() - started < 2
    assert converted.parser == "pdf-text-layer"
    assert "did not answer within" in converted.warnings[0]


@pytest.mark.parametrize("parse_url", ["closed", ""], ids=["unreachable", "disabled"])
def test_without_parse_an_image_fails(settings: Settings, scan_png: Path, parse_url: str):
    settings = replace(settings, parse_base_url=closed_port_url() if parse_url == "closed" else "")

    with pytest.raises(IngestError) as error:
        documents.convert(scan_png, "png", settings, None)

    assert error.value.code == "parser_unavailable"


def test_a_docx_converts_with_docling_alone(settings: Settings, docx_file: Path):
    converted = documents.convert(docx_file, "docx", settings, None)

    assert (converted.parser, converted.warnings) == ("docling-docx", [])
    markdown = converted.document.export_to_markdown()
    assert "Store Operations" in markdown and "Cash handling" in markdown
    assert len(converted.document.tables) == 1


def test_markdown_and_text_convert(settings: Settings, tmp_path: Path):
    (tmp_path / "a").write_text("# Title\n\nSome text about returns.\n")

    assert documents.convert(tmp_path / "a", "md", settings, None).parser == "docling-md"
    assert documents.convert(tmp_path / "a", "txt", settings, None).parser == "docling-txt"


def test_chunks_keep_page_numbers_and_headings(parse_settings: Settings, fake_parse: FakeParse, policy_pdf, tokenizer):
    fake_parse.label = "policy-pdf"
    converted = documents.convert(policy_pdf, "pdf", parse_settings, None)

    chunks = documents.chunk(converted.document, tokenizer)

    assert chunks and all(c.page_start is not None and c.page_start <= c.page_end for c in chunks)
    assert {c.page_start for c in chunks} == {1, 2}
    clearance = next(c for c in chunks if "Clearance" in c.text)
    assert clearance.page_start == 2
    assert any("table" in c.labels for c in chunks)
    assert any(c.headings for c in chunks)


def test_text_layer_chunks_keep_page_numbers(settings: Settings, policy_pdf: Path, tokenizer):
    converted = documents.convert(policy_pdf, "pdf", settings, None)

    chunks = documents.chunk(converted.document, tokenizer)

    assert {c.page_start for c in chunks} == {1, 2}
    assert next(c for c in chunks if "Clearance" in c.text).page_end == 2


@pytest.fixture
def index(tmp_path: Path):
    index = KnowledgeIndex(str(tmp_path / "milvus.db"), "knowledge")
    yield index
    index.close()


def test_ingest_document_writes_markdown_chunks_and_vectors(
    *, settings: Settings, catalog: Catalog, policy_pdf: Path, tokenizer, embedder: FakeEmbedder, index: KnowledgeIndex
):
    stages: list[tuple[str, str | None]] = []

    result = documents.ingest_document(
        settings=settings,
        catalog=catalog,
        embedder=embedder,
        index=index,
        tokenizer=tokenizer,
        source_id="workspace.documents",
        document_id="workspace.documents:f-0123456789abcdef",
        file_id="f-0123456789abcdef",
        file_path=policy_pdf,
        file_name="policy.pdf",
        fmt="pdf",
        on_stage=lambda stage, detail, percent: stages.append((stage, detail)),
    )

    assert result.parser == "pdf-text-layer" and result.pages == 2 and result.chunks > 0
    assert list(dict.fromkeys(s for s, _ in stages if s != "parsing")) == ["chunking", "embedding", "indexing"]
    assert ("parsing", "page 2 of 2") in stages
    source = catalog.source_dir("workspace.documents")
    markdown = (source / "documents" / "workspace.documents:f-0123456789abcdef.md").read_text()
    assert "Clearance items are final sale" in markdown
    lines = (source / "chunks" / "workspace.documents:f-0123456789abcdef.jsonl").read_text().splitlines()
    assert len(lines) == result.chunks
    first = json.loads(lines[0])
    assert first["chunk_id"] == "workspace.documents:f-0123456789abcdef:0001"
    assert first["metadata"]["citation"] == "policy.pdf, p. 1"
    assert index.count(source_id="workspace.documents") == result.chunks
    assert sum(len(call) for call in embedder.calls) == result.chunks


def test_the_embed_models_tokenizer_sizes_the_chunks(policy_pdf: Path, settings: Settings):
    try:
        tokenizer = documents.load_tokenizer(settings.tokenizer_dir)
    except Exception as error:  # no baked-in tokenizer and no network to fetch it
        pytest.skip(f"the embed model's tokenizer is unavailable: {type(error).__name__}")

    chunks = documents.chunk(documents.convert(policy_pdf, "pdf", settings, None).document, tokenizer)

    assert tokenizer.get_max_tokens() == 512
    assert chunks and all(tokenizer.count_tokens(c.text) <= 512 for c in chunks)


@pytest.mark.live
def test_live_parse(settings: Settings, policy_pdf: Path):
    """Against the parse service on this host: PARSE_BASE_URL=http://127.0.0.1:8340/v1 pytest -m live."""
    url = os.environ.get("PARSE_BASE_URL", "http://127.0.0.1:8340/v1")
    converted = documents.convert(policy_pdf, "pdf", replace(settings, parse_base_url=url), None)

    assert converted.parser == "nemotron-parse-2.0"
    assert len(converted.document.tables) == 1
