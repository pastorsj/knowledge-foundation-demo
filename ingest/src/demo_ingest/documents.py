# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Documents: docling conversion, chunking, embedding and indexing.

PDFs and images go through docling's VlmPipeline with its ``nemotron_parse_v2`` preset on the API engine, against
the vLLM server that serves NVIDIA Nemotron Parse 2.0 (one request per page). Born-digital formats (DOCX, PPTX,
HTML, Markdown, text) use docling's own backends and no model.

When Parse is disabled, unreachable, failing, or slower than the parse budget (most of the stage timeout), a PDF
falls back to its text layer: pypdfium2's text of each page becomes one Markdown section (``## Page N``) that
docling's Markdown backend converts; the file records parser ``pdf-text-layer`` and a warning. An image has no text
layer, so it fails with ``parser_unavailable``. A fallback is ``transient`` when another try may read the file with
Parse: Parse did not answer (connection error, HTTP 5xx, 408 or 429, or slower than the budget). A 4xx or empty
output would end the same way again.

Chunks come from docling's HybridChunker, sized with the embed model's own tokenizer; each is embedded as its
contextualized text (heading path + text).
"""

from __future__ import annotations

import atexit
import io
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

import requests
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import ConversionStatus
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import VlmConvertOptions
from docling.datamodel.pipeline_options import VlmPipelineOptions
from docling.datamodel.vlm_engine_options import ApiVlmEngineOptions
from docling.datamodel.vlm_engine_options import VlmEngineType
from docling.document_converter import DocumentConverter
from docling.document_converter import ImageFormatOption
from docling.document_converter import PdfFormatOption
from docling.pipeline.vlm_pipeline import VlmPipeline
from docling_core.types.doc import DoclingDocument
from docling_core.types.io import DocumentStream

from .detect import IMAGE_FORMATS
from .models import IngestError
from .models import Stage
from .models import redact
from .settings import Settings

if TYPE_CHECKING:
    from docling_core.transforms.chunker.tokenizer.base import BaseTokenizer

    from .embed import Embedder
    from .index import KnowledgeIndex

logger = logging.getLogger(__name__)

PARSE_PARSER = "nemotron-parse-2.0"
TEXT_LAYER_PARSER = "pdf-text-layer"
TEXT_LAYER_WARNING = "Parsed from the PDF's text layer: Nemotron Parse was unavailable ({reason})."
# The served context is 9000 tokens (prompt and image included), so the model card's 9000 cannot be the output cap.
PARSE_MAX_TOKENS = 8192
PARSE_REQUEST_TIMEOUT = 300
PARSE_PROBE_TIMEOUT = 5.0
# The share of the stage timeout Parse may use, so the text-layer fallback still finishes inside the stage.
PARSE_BUDGET = 0.75
EMBED_TOKENIZER = "nvidia/Nemotron-3-Embed-1B-BF16"
EMBED_TOKENIZER_REVISION = "c0c9fea93ea424587517f2c59e20db9f1d6bf615"
CHUNK_MAX_TOKENS = 512
# Docling input format and the extension it recognises, per born-digital format.
BORN_DIGITAL = {
    "docx": (InputFormat.DOCX, "docx"),
    "pptx": (InputFormat.PPTX, "pptx"),
    "html": (InputFormat.HTML, "html"),
    "md": (InputFormat.MD, "md"),
    "txt": (InputFormat.MD, "md"),  # docling has no plain-text backend; text is valid Markdown
}
_PAGE_HEADING = re.compile(r"^Page (\d+)$")
# How docling reports a Parse that did not answer: requests' connection errors and timeouts, or the HTTP status.
_TRANSIENT = re.compile(r"HTTP (?:5\d\d|408|429)\b|connection|timeout|timed out|max retries", re.IGNORECASE)

Progress = Callable[[int, int], None]
StageCallback = Callable[[str, str | None, float], None]
StageRunner = Callable[[str, Callable[[], Any]], Any]
ParseProbe = Callable[[Settings], str | None]

_page_progress: ContextVar[Progress | None] = ContextVar("page_progress", default=None)
# A fast tokenizer must not be used from two threads at once; chunking is quick next to parsing and embedding.
_chunk_lock = threading.Lock()


@dataclass
class Converted:
    document: DoclingDocument
    parser: str
    pages: int
    warnings: list[str] = field(default_factory=list)
    transient: bool = False  # Parse did not answer for some or all pages: another try may read them


@dataclass
class Chunk:
    text: str
    page_start: int | None
    page_end: int | None
    headings: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)


@dataclass
class DocumentResult:
    parser: str
    pages: int
    chunks: int
    warnings: list[str] = field(default_factory=list)
    transient: bool = False


class _ParseUnavailable(Exception):
    """Parse cannot convert this file; the message is the reason the warning shows. ``transient``: Parse did not
    answer (down, restarting, overloaded or slow), so another try may succeed."""

    def __init__(self, reason: str, *, transient: bool) -> None:
        super().__init__(redact(reason))
        self.transient = transient


def is_transient(text: str) -> bool:
    return _TRANSIENT.search(text) is not None


# Conversion


class ProgressVlmPipeline(VlmPipeline):
    """docling's VlmPipeline, reporting pages as each batch of Parse requests returns."""

    def _process_page_batch(self, *, conv_res, page_batch, page_documents, processed_page_nos) -> None:
        super()._process_page_batch(
            conv_res=conv_res,
            page_batch=page_batch,
            page_documents=page_documents,
            processed_page_nos=processed_page_nos,
        )
        if (report := _page_progress.get()) is not None:
            report(len(processed_page_nos), conv_res.input.page_count)


def probe_parse(settings: Settings) -> str | None:
    """None when Parse answers GET /models, else why it cannot be used."""
    if not settings.parse_enabled:
        return "PARSE_BASE_URL is empty"
    try:
        response = requests.get(
            f"{settings.parse_base_url}/models", headers=_parse_headers(settings), timeout=PARSE_PROBE_TIMEOUT
        )
        response.raise_for_status()
    except requests.RequestException as error:
        return f"{settings.parse_base_url} did not answer: {type(error).__name__}"
    return None


def _parse_headers(settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.parse_api_key}"} if settings.parse_api_key else {}


@cache
def _parse_converter(settings: Settings, budget: float) -> DocumentConverter:
    engine = ApiVlmEngineOptions(
        engine_type=VlmEngineType.API,
        url=f"{settings.parse_base_url}/chat/completions",
        params={
            "model": settings.parse_model,
            "skip_special_tokens": False,
            "top_k": 1,
            "repetition_penalty": 1.1,
            "temperature": 0,
            "max_tokens": PARSE_MAX_TOKENS,
        },
        headers=_parse_headers(settings),
        timeout=PARSE_REQUEST_TIMEOUT,
        concurrency=settings.parse_concurrency,
    )
    options = VlmPipelineOptions(
        vlm_options=VlmConvertOptions.from_preset("nemotron_parse_v2", engine_options=engine),
        enable_remote_services=True,
        document_timeout=budget,  # an abandoned conversion stops at its next page batch
    )
    return DocumentConverter(
        allowed_formats=[InputFormat.PDF, InputFormat.IMAGE],
        format_options={
            # The default PDF backend needs docling-parse, which this image does not install.
            InputFormat.PDF: PdfFormatOption(
                pipeline_cls=ProgressVlmPipeline, pipeline_options=options, backend=PyPdfiumDocumentBackend
            ),
            InputFormat.IMAGE: ImageFormatOption(pipeline_cls=ProgressVlmPipeline, pipeline_options=options),
        },
    )


# Release the Parse engines while logging still works: docling's cleanup logs, and fails noisily at interpreter exit.
atexit.register(_parse_converter.cache_clear)


@cache
def _born_digital_converter() -> DocumentConverter:
    return DocumentConverter(allowed_formats=[InputFormat.DOCX, InputFormat.PPTX, InputFormat.HTML, InputFormat.MD])


def _stream(path: Path, name: str) -> DocumentStream:
    return DocumentStream(name=name, stream=io.BytesIO(path.read_bytes()))


def convert(
    path: Path, fmt: str, settings: Settings, on_progress: Progress | None, *, probe: ParseProbe = probe_parse
) -> Converted:
    """Convert one file into a DoclingDocument; ``fmt`` (from detection) says what it is, whatever its name."""
    report = on_progress or (lambda done, total: None)
    if fmt == "pdf" or fmt in IMAGE_FORMATS:
        started = time.monotonic()
        budget = max(1.0, settings.stage_timeout_s * PARSE_BUDGET)
        try:
            reason = probe(settings)
            if reason is not None:  # off (configuration) or not answering (transient)
                raise _ParseUnavailable(reason, transient=settings.parse_enabled)
            return _parse(path, fmt, settings, report, budget=budget, remaining=budget - (time.monotonic() - started))
        except _ParseUnavailable as unavailable:
            if fmt != "pdf":
                error = IngestError(
                    "parser_unavailable",
                    f"Nemotron Parse is unavailable ({unavailable}), and an image has no text layer to read instead.",
                )
                error.transient = unavailable.transient
                raise error from None
            logger.warning("%s: Nemotron Parse unavailable (%s); using the text layer", path.name, unavailable)
            return _text_layer(path, report, str(unavailable), transient=unavailable.transient)
    if fmt not in BORN_DIGITAL:
        raise IngestError("unsupported_type", f"{fmt} is not a document format.")
    _, extension = BORN_DIGITAL[fmt]
    report(0, 1)
    try:
        result = _born_digital_converter().convert(_stream(path, f"document.{extension}"), raises_on_error=False)
    except Exception as error:
        raise IngestError("conversion_failed", f"docling could not convert the file: {error}") from error
    if result.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
        raise IngestError("conversion_failed", _errors(result) or "docling could not convert the file.")
    report(1, 1)
    return Converted(document=result.document, parser=f"docling-{fmt}", pages=len(result.document.pages))


def _parse(path: Path, fmt: str, settings: Settings, report: Progress, *, budget: float, remaining: float) -> Converted:
    """Convert through Parse in a thread of its own, so a Parse that stops answering costs at most ``budget``."""
    converter = _parse_converter(settings, budget)
    outcome: dict[str, Any] = {}

    def work() -> None:
        _page_progress.set(report)
        try:
            outcome["result"] = converter.convert(_stream(path, f"document.{'pdf' if fmt == 'pdf' else fmt}"))
        except Exception as error:  # docling raises ConversionError when no page converts
            outcome["error"] = error

    worker = threading.Thread(target=work, name=f"parse-{path.name}", daemon=True)
    worker.start()
    worker.join(max(0.1, remaining))
    if worker.is_alive():
        raise _ParseUnavailable(f"it did not answer within {budget:.0f} s", transient=True)
    if "error" in outcome:
        message = str(outcome["error"])
        raise _ParseUnavailable(_first_line(outcome["error"]), transient=is_transient(message))
    result = outcome["result"]
    if result.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
        errors = _errors(result)
        reason = errors or f"the conversion ended {result.status.value}"
        raise _ParseUnavailable(reason, transient=is_transient(errors))
    document = result.document
    if not document.texts and not document.tables:
        raise _ParseUnavailable("it returned no content", transient=False)
    page_errors = [error for error in result.errors if getattr(error, "page_no", None) is not None]
    warnings = [
        f"Nemotron Parse could not read page {error.page_no}: {redact(error.error_message)}"[:500]
        for error in page_errors
    ][:10]
    return Converted(
        document=document,
        parser=PARSE_PARSER,
        pages=result.input.page_count,
        warnings=warnings,
        transient=any(is_transient(error.error_message) for error in page_errors),
    )


def _text_layer(path: Path, report: Progress, reason: str, *, transient: bool = False) -> Converted:
    import pypdfium2
    from docling.utils.locks import pypdfium2_lock

    texts: list[str] = []
    # PDFium is not thread-safe: two documents read at once crash the process. docling's own PDF backend takes
    # this lock around every PDFium call, so ours does too (reading a text layer takes milliseconds per page).
    with pypdfium2_lock:
        try:
            pdf = pypdfium2.PdfDocument(str(path))
        except pypdfium2.PdfiumError as error:
            raise IngestError("conversion_failed", f"The PDF could not be opened: {error}") from error
        try:
            for index in range(len(pdf)):
                page = pdf[index]
                textpage = page.get_textpage()
                texts.append(textpage.get_text_range().replace("\r\n", "\n").replace("\r", "\n").strip())
                textpage.close()
                page.close()
        finally:
            pdf.close()
    total = len(texts)
    sections: list[str] = []
    for number, text in enumerate(texts, start=1):
        # A line that starts with '#' would become a heading and break the one-section-per-page layout.
        escaped = re.sub(r"(?m)^(\s*)#", r"\1\\#", text)
        sections.append(f"## Page {number}\n\n{escaped}\n")
        report(number, total)
    has_text = any(texts)
    if not has_text:
        error = IngestError(
            "parser_unavailable",
            f"Nemotron Parse is unavailable ({reason}), and the PDF has no text layer (it may be a scan).",
        )
        error.transient = transient
        raise error
    markdown = "\n".join(sections)
    result = _born_digital_converter().convert(
        DocumentStream(name="document.md", stream=io.BytesIO(markdown.encode())), raises_on_error=False
    )
    if result.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
        raise IngestError("conversion_failed", _errors(result) or "The PDF's text could not be converted.")
    return Converted(
        document=result.document,
        parser=TEXT_LAYER_PARSER,
        pages=total,
        warnings=[TEXT_LAYER_WARNING.format(reason=reason)[:500]],
        transient=transient,
    )


def _errors(result: Any) -> str:
    return "; ".join(error.error_message for error in result.errors)[:500]


def _first_line(error: BaseException) -> str:
    return (str(error).strip().splitlines() or [type(error).__name__])[0][:300]


# Chunking


@cache
def load_tokenizer(directory: Path) -> BaseTokenizer:
    """The embed model's tokenizer: from ``directory`` (the image bakes it in), else the Hugging Face cache."""
    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
    from transformers import AutoTokenizer

    if (directory / "tokenizer.json").exists():
        tokenizer = AutoTokenizer.from_pretrained(str(directory))
    else:
        tokenizer = AutoTokenizer.from_pretrained(EMBED_TOKENIZER, revision=EMBED_TOKENIZER_REVISION)
    return HuggingFaceTokenizer(tokenizer=tokenizer, max_tokens=CHUNK_MAX_TOKENS)


def chunk(document: DoclingDocument, tokenizer: BaseTokenizer) -> list[Chunk]:
    from docling.chunking import HybridChunker

    chunker = HybridChunker(tokenizer=tokenizer)
    with _chunk_lock:
        items = [(item, chunker.contextualize(chunk=item)) for item in chunker.chunk(dl_doc=document)]
    chunks: list[Chunk] = []
    for item, text in items:
        if not text.strip():
            continue
        headings = list(item.meta.headings or [])
        pages = sorted({prov.page_no for doc_item in item.meta.doc_items for prov in doc_item.prov})
        if not pages:  # the text-layer fallback carries its pages as "Page N" sections
            pages = [int(m[1]) for heading in headings if (m := _PAGE_HEADING.match(heading))]
        labels = sorted({str(getattr(doc_item.label, "value", doc_item.label)) for doc_item in item.meta.doc_items})
        chunks.append(
            Chunk(
                text=text,
                page_start=pages[0] if pages else None,
                page_end=pages[-1] if pages else None,
                headings=headings,
                labels=labels,
            )
        )
    return chunks


# The whole document


def _direct(stage: str, work: Callable[[], Any]) -> Any:
    return work()


def ingest_document(
    *,
    settings: Settings,
    catalog: Any,
    embedder: Embedder,
    index: KnowledgeIndex,
    tokenizer: BaseTokenizer,
    source_id: str,
    document_id: str,
    file_id: str,
    file_path: Path,
    file_name: str,
    fmt: str,
    on_stage: StageCallback | None = None,
    run_stage: StageRunner = _direct,
    probe: ParseProbe = probe_parse,
) -> DocumentResult:
    """Convert, chunk, embed and index one document; write its Markdown and chunk files for previews and re-embeds.

    ``run_stage`` runs each stage (the pipeline passes one that enforces the stage timeout).
    """
    notify = on_stage or (lambda stage, detail, percent: None)
    born_digital = fmt in BORN_DIGITAL
    convert_stage = Stage.CONVERTING if born_digital else Stage.PARSING

    def pages(done: int, total: int) -> None:
        notify(convert_stage, None if born_digital else f"page {done} of {total}", 5 + 55 * done / max(total, 1))

    notify(convert_stage, None, 5)
    converted: Converted = run_stage(convert_stage, lambda: convert(file_path, fmt, settings, pages, probe=probe))

    notify(Stage.CHUNKING, None, 60)
    chunks: list[Chunk] = run_stage(Stage.CHUNKING, lambda: chunk(converted.document, tokenizer))
    if not chunks:
        raise IngestError("empty_file", f"No text was found in {file_name}, so there is nothing to search.")
    directory = catalog.source_dir(source_id)
    (directory / "documents").mkdir(parents=True, exist_ok=True)
    (directory / "chunks").mkdir(parents=True, exist_ok=True)
    _write_text(directory / "documents" / f"{document_id}.md", converted.document.export_to_markdown())
    metadata = {"parser": converted.parser, "file_id": file_id, "file_name": file_name}
    _write_text(
        directory / "chunks" / f"{document_id}.jsonl",
        "".join(
            json.dumps(_chunk_record(document_id, number, item, file_name, metadata), ensure_ascii=False) + "\n"
            for number, item in enumerate(chunks, start=1)
        ),
    )

    def embedded(done: int, total: int) -> None:
        notify(Stage.EMBEDDING, f"{done} of {total} chunks", 65 + 25 * done / max(total, 1))

    notify(Stage.EMBEDDING, f"0 of {len(chunks)} chunks", 65)
    vectors = run_stage(Stage.EMBEDDING, lambda: embedder.embed_documents([c.text for c in chunks], embedded))

    notify(Stage.INDEXING, None, 90)
    run_stage(
        Stage.INDEXING,
        lambda: index.replace_document(
            source_id=source_id,
            document_id=document_id,
            title=file_name,
            url=None,
            chunks=chunks,
            vectors=vectors,
            metadata=metadata,
        ),
    )
    return DocumentResult(
        parser=converted.parser,
        pages=converted.pages,
        chunks=len(chunks),
        warnings=converted.warnings,
        transient=converted.transient,
    )


def _chunk_record(document_id: str, number: int, item: Chunk, file_name: str, metadata: dict) -> dict[str, Any]:
    from .index import HEADING_SEPARATOR
    from .index import citation

    return {
        "chunk_id": f"{document_id}:{number:04d}",
        "document_id": document_id,
        "text": item.text,
        "metadata": {
            **metadata,
            "citation": citation(file_name, item.page_start),
            "page_start": item.page_start,
            "page_end": item.page_end,
            "headings": HEADING_SEPARATOR.join(item.headings),
            "labels": item.labels,
        },
        "chunk": asdict(item),
    }


def _write_text(path: Path, text: str) -> None:
    """Atomic, as every catalog write."""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(0o644)
    temporary.replace(path)
