<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Ingestion

The `ingest` service (`ingest/`, `demo-ingest serve` on 127.0.0.1:8330) turns files into the knowledge catalog
that every tool reads. It is the only writer of the `knowledge` volume. Uploads ("Your data") and industry packs
(`data/packs`) go through the same pipeline, so a question about an uploaded PDF and one about a pack's PDF are
answered the same way. Everything follows the libraries' documented practice: docling for conversion and chunking,
NVIDIA Nemotron Parse 2.0 through docling's official preset, `langchain-nvidia-ai-endpoints` for embeddings,
`pymilvus` for the index, DuckDB's own readers for tables.

[`ingest/README.md`](../ingest/README.md) is the service's reference (layout, routes, environment); this page
explains the pipeline and why it is built this way.

## Stages per file kind

Each file moves through stages that the job store records with progress, so the UI's file cards draw them.

| Kind | Extensions | Stages |
|---|---|---|
| Parsed documents | `.pdf .png .jpg .jpeg .tif .tiff .webp .bmp` | `received` → `detected` → `parsing` (page i of n) → `chunking` → `embedding` (k of n) → `indexing` → `ready` |
| Born-digital documents | `.docx .pptx .html .htm .md .txt` | `received` → `detected` → `converting` → `chunking` → `embedding` → `indexing` → `ready` |
| Tables | `.csv .tsv .xlsx .parquet .json .jsonl` | `received` → `detected` → `loading` (sheet i of n) → `profiling` → `ready` |

Any stage can end in `failed` with an error code and a message; a failed file never blocks the others in its
batch. Each file records the `parser` that read it (`nemotron-parse-2.0`, `pdf-text-layer`, `docling-docx`,
`duckdb-csv`, `duckdb-xlsx`, …) and any `warnings`.

## Detection

- **Extension allowlist**, then **content sniffing** with `filetype` (docling's own dependency) and a few
  signatures, so a renamed file (an executable called `report.pdf`) is refused rather than parsed.
- **Limits**: `INGEST_MAX_FILE_MB` (100) per file and `INGEST_MAX_FILES` (20) per request; the API caps the whole
  multipart body at `INGEST_MAX_REQUEST_MB` (512) before it reaches ingest.
- **Deduplication**: a file's id is `f-` plus the first 16 hex digits of its sha256, so identical bytes in one
  source are one file. Re-uploading them returns the existing file, or ingests it again if it had failed.
- Uploads stream to disk; the original bytes stay in `sources/<source_id>/files/`, so any file can be ingested
  again without asking the user for it.

## Documents: docling and Nemotron Parse

One docling `DocumentConverter` reads every document kind:

- **PDFs and images** use docling's `VlmPipeline` with its official **`nemotron_parse_v2`** preset
  (`VlmConvertOptions.from_preset("nemotron_parse_v2", ...)`), run through docling's **API engine**
  (`ApiVlmEngineOptions`) against an OpenAI-compatible server that serves `nvidia/NVIDIA-Nemotron-Parse-2.0`. The
  preset's response format (`NEMOTRON_PARSE_V2`) turns Parse's output into a `DoclingDocument` with layout classes,
  reading order and tables; docling's `format-latex` extra converts Parse's LaTeX tables. PDFs are rendered with
  `PyPdfiumDocumentBackend`, one request per page, `PARSE_CONCURRENCY` (4) pages in flight per document. The
  request parameters are the model card's: `skip_special_tokens: false`, `top_k: 1`, `repetition_penalty: 1.1`,
  `temperature: 0`, and `max_tokens` from `PARSE_MAX_TOKENS`: 8192 by default (the card says 9,000, but the local
  vLLM's context is 9,000 tokens including the prompt and the image), 4096 for build.nvidia.com.
- **Page images** are rendered at the preset's `scale` (2.0: 144 dpi for a PDF page; an image without DPI metadata
  counts as 72 dpi, so it would be doubled) with the preset's `max_size` set to 1,664: docling scales a page down,
  keeping its aspect ratio, until its longer side is at most 1,664 px, so every page fits the model card's maximum
  resolution of 1,664 x 2,048 (W x H) in either orientation. docling sends each page as a base64 PNG in the request
  body; the cap keeps a scanned Letter page at about 3 MB, under build.nvidia.com's body limit (a scan sent doubled
  made a 10.9 MB request, refused with HTTP 413).
- **DOCX, PPTX, HTML, Markdown and text** use docling's own format backends: born-digital files carry exact text,
  so no model reads them.
- docling is installed as `docling-slim` 2.132 with the extras `format-pdf-pypdfium2`, `format-office`,
  `format-html`, `format-markdown`, `format-latex`, `feat-chunking` and `convert-core`: no PyTorch, and it builds
  on arm64 and x86_64 alike.

Each converted document is also exported as Markdown (`sources/<id>/documents/<document_id>.md`) for previews.

### The Parse service (`parse` profile)

`compose.yaml`'s `parse` service runs `vllm/vllm-openai:v0.27.1` (multi-arch, pinned by digest) on an NVIDIA GPU,
on 127.0.0.1:8340. Its entrypoint, [`infra/parse/entrypoint.sh`](../infra/parse/entrypoint.sh), serves the model
as its card prescribes:

1. It downloads `vllm_tied_patch/sitecustomize.py` from the model's pinned revision
   (`PARSE_REVISION=b6742064f4a8cf22a10383ece5e7fbead355ac04`) and puts it on `PYTHONPATH`. The card's patch ties
   the output head to the decoder embeddings; without it this vLLM leaves the head untied and the model emits
   `<s>` until `max_tokens`.
2. It runs `vllm serve nvidia/NVIDIA-Nemotron-Parse-2.0 --revision <pin> --dtype bfloat16 --max-num-seqs 8
   --limit-mm-per-prompt '{"image": 1}' --trust-remote-code --gpu-memory-utilization 0.15`.

The weights download into the `parse-cache` volume on the first start (the health check allows 15 minutes).
`PARSE_GPU_MEMORY_UTILIZATION` (default 0.15) is the server's share of GPU memory; on a DGX Spark the memory is
unified with the CPU's, so keep it small. Under the `parse` profile `demo.sh` points ingest at
`http://parse:8000/v1`; ingest waits for Parse to be healthy before it syncs the packs, so their PDFs are parsed
rather than read from the text layer.

A spike on a DGX Spark (GB10, 2026-10-02) parsed a two-page PDF with a table in 2.3 s and a scanned page in 1.8 s
this way, tables included. The Nemotron Parse NIM has no GB10 profile, which is why vLLM is used.

**Without a GPU**, set `PARSE_BASE_URL=https://integrate.api.nvidia.com/v1`, `PARSE_MODEL=nvidia/nemotron-parse-2.0`,
`PARSE_MAX_TOKENS=4096` and an `nvapi-` `PARSE_API_KEY`: the hosted model takes the same chat contract, but serves
a 4,096-token context, so it refuses the default cap of 8,192 with HTTP 400 (every PDF would then fall back to its
text layer and every image would fail).

### Fallbacks

| Situation | What happens |
|---|---|
| No Parse endpoint configured (no `parse` profile, empty `PARSE_BASE_URL`) | A PDF is read from its text layer with `pypdfium2`: each page becomes a `## Page N` Markdown section that docling's Markdown backend converts. The file records `parser: pdf-text-layer` and a warning, which the UI shows. An image fails with `parser_unavailable` |
| Parse unreachable, failing, or slower than three quarters of the stage timeout | The same text-layer fallback for a PDF, with the reason in the warning; an image fails with `parser_unavailable` |
| A pack synced while Parse was unreachable | The pack's digest is not recorded, so the next sync tries Parse again |

`doctor` warns when neither the `parse` profile nor `PARSE_BASE_URL` is set.

## Chunking

docling's `HybridChunker` (the `feat-chunking` extra), the documented docling RAG practice: it splits by document
structure, then by tokens, and merges small neighbours. Its tokenizer is the embed model's own Hugging Face
tokenizer (`nvidia/Nemotron-3-Embed-1B-BF16` at a pinned revision, files only, baked into the image at
`INGEST_TOKENIZER_DIR`), with chunks of at most 512 tokens, so a chunk never exceeds what the embedder reads.

Each chunk is embedded as `chunker.contextualize(chunk)`: its heading path plus its text. Its metadata:
`page_start`, `page_end`, `headings`, the docling item labels, the `parser`, and a `citation` string
(`"<file name>, p. 3"`) that the agent's answer shows. Chunks are kept in `sources/<id>/chunks/<document_id>.jsonl`.

## Embedding

`NVIDIAEmbeddings` from `langchain-nvidia-ai-endpoints` 1.4.3, the same client and pin as
[`tools/retrieval`](../tools/retrieval/README.md): `input_type: passage`, an explicit `base_url` and key (never
`NVIDIA_BASE_URL` or `NVIDIA_API_KEY`), the model registered with `register_model`, batches of 50 texts, and up to
8 attempts with jittered backoff (tenacity) on transient errors. The defaults are build.nvidia.com and
`nvidia/nemotron-3-embed-1b` (`RETRIEVER_*` in `.env`). Retrieval must embed queries with the same model.

## Index: Milvus

One collection, `knowledge__v1`, behind the alias `knowledge`, in the CPU standalone Milvus 2.6 of the `core`
profile. Schema: `chunk_id` (primary key), `source_id` (the partition key), `document_id`, `title`, `url`,
`published_at`, `text`, the `embedding` vector, and dynamic fields for each chunk's metadata; HNSW with cosine
similarity, `M = 16`, `efConstruction = 200` (retrieval searches with `ef = 128`). Ingest creates the collection and
alias on first use. Per document it deletes the document's chunks by `document_id`, then inserts the new ones, so
every stage is idempotent. The metadata key `image` is reserved: `NVIDIARerank` would send it to the reranker as an
image. Each documents source's manifest records its document and chunk counts, the collection and the embed model.

## Tables: DuckDB

One `tables.duckdb` per structured source (`sources/<id>/tables.duckdb`), DuckDB 1.5.5:

| Format | Reader |
|---|---|
| CSV, TSV | `read_csv` with auto-detection over the whole file |
| XLSX | the `excel` extension's `read_xlsx`, one table per sheet (installed at image build time, so no network at runtime). A sheet whose first data row or empty header cell would mistype or drop a column is read with openpyxl and typed by `read_csv` |
| Parquet | `read_parquet` |
| JSON, JSON Lines | `read_json_auto` |

Table names are the snake-cased file stem, plus `_<sheet>` for a workbook of several sheets, unique in the database;
column names are snake-cased, non-empty and unique. Legacy `.xls` is not accepted.

**Profiling** fills each table's `TableInfo` in the source manifest: the row count, column types and nullability,
a primary key (a single unique, non-null column, preferring `*_id` or `id`), a time column (DATE or TIMESTAMP), and
foreign keys (a column whose non-null values all appear in another table's primary key, with agreeing names). A
pack may declare keys, time columns, descriptions and prediction templates in `pack.yaml`; declarations win over
inference. The DuckDB alias is the snake-cased source id (`retail.sales` → `retail_sales`); the agent addresses
tables as `<alias>.<table>`, and the run instructions list them.

A pack sync builds each structured source into a new file that replaces the old one in one step, so a reader sees
the old tables or the new ones, never half of each.

## Pack sync

At startup `demo-ingest serve` syncs every `PACKS_DIR/<id>/pack.yaml` (`./data/packs`, mounted read-only at
`/packs`) in the background. `./scripts/demo.sh data sync` (`POST /v1/packs/sync`) starts another sync on a running
stack, `./scripts/demo.sh data sync --force` (`POST /v1/packs/sync?force=true`) ingests every pack again whatever its
digest, and `./scripts/demo.sh data status` (`GET /v1/packs/status`) shows each pack's progress.

- Each pack is validated against `data/schemas` (as `scripts/validate_packs.py` does before a commit), then each of
  its sources becomes the catalog source `<pack>.<source>`, with the pack's declared keys, column descriptions and
  prediction templates. The pack's files are copied into the source, then ingested like uploads.
- A pack is skipped when its digest is unchanged and it is `ready`: sha256 over `pack.yaml`, `questions.yaml` and
  `files/**`, plus the Parse model (or `pdf-text-layer`) and the embed model. Enabling Parse therefore re-ingests
  every pack on the next sync.
- The digest is recorded only when the sync is complete: every file ready, or failed for a reason another sync would
  repeat (the file itself: an unsupported, mismatched, empty, oversized or unreadable file; or Parse turned off).
  After anything else (Parse not answering, so a PDF fell back to its text layer or a scan went unread; an
  embedding, Milvus or internal error; a timeout) the pack keeps no new digest, so the next `data sync` retries it.
  `--force` (`demo-ingest sync-packs --force`) ingests every pack again regardless, for instance after a fix outside
  the digest's inputs. Changing the embed model needs a rebuild of the index instead (`./scripts/demo.sh down --volumes`, then
  `up`): workspace uploads are not re-embedded, a model of another dimension cannot reuse the collection, and the
  ingest image bakes in the model's tokenizer.
- Documents that left a pack are removed from the index; a documents source is re-indexed file by file.
- `demo-ingest sync-packs` does the same once and exits non-zero if any pack or file failed.

## Robustness

- Jobs, files and stage events live in SQLite (WAL) at `ingest/ingest.sqlite3` in the volume.
- `INGEST_WORKERS` (2) workers take files in upload order.
- A restart re-queues every file that was neither ready nor failed; every stage deletes what it wrote before
  writing again, so a repeat is safe.
- Each stage has a timeout (`INGEST_STAGE_TIMEOUT_SECONDS`, 1,800 s); Parse may use three quarters of it, so the
  text-layer fallback still fits.
- Every manifest write is validated against its contract and atomic (a temporary file, then `os.replace`).

## HTTP API

AI-Q's documents contract, so the UI's upload feature is AI-Q's restored nearly verbatim. The API forwards these
routes unchanged (`/v1/collections/**`, `/v1/documents/{job_id}/status`); the browser never reaches ingest directly.

| Route | Purpose |
|---|---|
| `GET /health` | `200 {"status": "ready"}` once the catalog is writable and Milvus answers, else `503` |
| `GET`, `POST /v1/collections`; `GET`, `DELETE /v1/collections/{name}` | collections; `workspace` is the upload collection (deleting it removes its files) |
| `POST /v1/collections/workspace/documents` | multipart `files` → `{job_id, file_ids, message}` |
| `GET /v1/collections/{name}/documents` | `{files: [FileInfo]}` |
| `DELETE /v1/collections/{name}/documents` | body `{file_ids}`; removes the chunks, tables and originals |
| `GET /v1/documents/{job_id}/status` | `IngestionJobStatus` with one `FileProgress` per file |
| `POST /v1/packs/sync`, `GET /v1/packs/status` | start a pack sync (`202`); per pack `{id, status, files_total, files_done, error}` |

`FileProgress` and `FileInfo` add optional fields that upstream's zod schemas ignore: `kind` (`document` or
`table`), `stage`, `stage_detail` (for example `page 3 of 12`), `parser`, `tables` (the table names), `warnings`
and `error_code`. Errors are `{"error": {"code", "message"}}`.

Uploaded documents become the source `workspace.documents` and tables `workspace.tables` (DuckDB alias
`workspace_tables`), both in the pack `workspace`, which the UI shows as "Your data".

## Limits

| Limit | Default | Setting |
|---|---|---|
| File size | 100 MB | `INGEST_MAX_FILE_MB` (also the UI's upload zone) |
| Files per request | 20 | `INGEST_MAX_FILES` |
| Request body | 512 MB | `INGEST_MAX_REQUEST_MB` (the API refuses a larger one with 413) |
| Files ingested at once | 2 | `INGEST_WORKERS` |
| Pages in flight to Parse per document | 4 | `PARSE_CONCURRENCY` |
| Stage timeout | 1,800 s | `INGEST_STAGE_TIMEOUT_SECONDS` |
| Parse output per page | 8,192 tokens (4,096 for build.nvidia.com) | `PARSE_MAX_TOKENS` |
| Page image sent to Parse | longer side 1,664 px | fixed: inside the model card's 1,664 x 2,048 |
| Chunk size | 512 tokens | fixed |

Not accepted: legacy `.xls` and `.doc`, audio and video. Uploads are single-user: there is one workspace, shared
by every browser that reaches the UI.

## Tests

```bash
uv run --directory ingest pytest -q
```

The suite is offline: a fake OpenAI-compatible server replays recorded Nemotron Parse 2.0 responses
(`ingest/tests/fixtures/parse/`), a fake embedder, Milvus Lite, fixture CSVs and a mini pack
(`ingest/tests/fixtures/packs/mini/`). [Development](development.md) lists the other suites.
