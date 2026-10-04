<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Ingest

The only writer of the knowledge catalog (`/knowledge`). It ingests the files a user uploads and every industry pack
under `/packs` through one pipeline:

| Kind | Stages |
|---|---|
| PDF and images | `received` → `detected` → `parsing` (page i of n, NVIDIA Nemotron Parse 2.0 through docling) → `chunking` → `embedding` → `indexing` → `ready` |
| DOCX, PPTX, HTML, Markdown, text | `received` → `detected` → `converting` (docling's own backends) → `chunking` → `embedding` → `indexing` → `ready` |
| CSV, TSV, XLSX, Parquet, JSON | `received` → `detected` → `loading` (sheet i of n, DuckDB) → `profiling` → `ready` |

Any stage can end in `failed` with an error code; a failed file never blocks the others.

## Layout it writes

```text
/knowledge/
  catalog/packs/<pack_id>.json                    PackManifest (contracts/catalog/pack-manifest.schema.json)
  catalog/sources/<source_id>.json                SourceManifest (contracts/catalog/source-manifest.schema.json)
  sources/<source_id>/files/<file_id><ext>        original bytes, with the original's extension
  sources/<source_id>/documents/<document_id>.md  docling Markdown export
  sources/<source_id>/chunks/<document_id>.jsonl  chunks with metadata, for re-embedding without re-parsing
  sources/<source_id>/tables.duckdb               structured sources
  ingest/ingest.sqlite3                           jobs and files (SQLite, WAL)
```

Every manifest write is validated against its contract and atomic (`tempfile` + `os.replace`).

## HTTP API (`demo-ingest serve`, port 8330)

AI-Q's documents contract, so its upload UI works unchanged; responses add `kind`, `stage`, `stage_detail`, `parser`,
`tables`, `warnings` and `error_code` per file. Errors are `{"error": {"code", "message"}}`.

| Route | Purpose |
|---|---|
| `GET /health` | `200 {"status": "ready"}` once the catalog is writable and Milvus answers, else `503` |
| `GET`, `POST /v1/collections`; `GET`, `DELETE /v1/collections/{name}` | collections; `workspace` is the upload collection (deleting it removes its files) |
| `POST /v1/collections/workspace/documents` | multipart `files` → `{job_id, file_ids, message}` |
| `GET /v1/collections/{name}/documents` | `{files: [FileInfo]}` |
| `DELETE /v1/collections/{name}/documents` | body `{file_ids}`; removes chunks, tables and originals |
| `GET /v1/documents/{job_id}/status` | `IngestionJobStatus` |
| `POST /v1/packs/sync[?force=true]`, `GET /v1/packs/status` | start a pack sync (`202`; `force` ignores digests), per-pack `{id, status, files_total, files_done, error}` |

Uploads stream to disk; `file_id` is `f-` plus the first 16 hex digits of the file's sha256, so identical bytes are
one file (a re-upload returns it, or ingests it again if it had failed). A file that cannot be ingested (unsupported,
renamed, empty, too large) fails on its own with an error code and message; the rest of the batch continues.
Uploaded documents become the source `workspace.documents`, tables `workspace.tables` (DuckDB alias
`workspace_tables`), both in the pack `workspace` ("Your data"). Table files load one at a time into a private copy of
the DuckDB file that is swapped in whole; uploads and deletes never wait for a load (a deleted file's tables leave the
manifest at once and the DuckDB file once the running load is done). Error messages carry no URLs or paths.

**Nemotron Parse fallback.** When Parse is disabled, unreachable, failing, or slower than three quarters of the stage
timeout, a PDF is read from its text layer instead (parser `pdf-text-layer`, with a warning); an image fails with
`parser_unavailable`.

## Industry packs (`demo-ingest sync-packs [--force]`)

Every `PACKS_DIR/<id>/pack.yaml` (validated with `data/schemas`) is ingested through the same pipeline into
`<id>.<source>` sources, with the pack's declared keys, column descriptions and prediction templates. The server
syncs in the background at startup; the command syncs and exits non-zero if any pack or file failed. A pack is skipped
when its digest is unchanged: sha256 over `pack.yaml`, `questions.yaml` and `files/**`, plus the Parse model (or
`pdf-text-layer` when Parse is disabled) and the embed model. The digest is recorded only when every file is ready
or failed for a reason another sync would repeat (its content: `unsupported_type`, `type_mismatch`, `empty_file`,
`too_large`, `load_failed`, `conversion_failed`, a Parse 4xx or empty output; or Parse turned off). After anything
else (Parse unreachable, answering 5xx or slower than its budget, mid-file too, so a PDF fell back to its text layer
or a scan was not read; an embedding, Milvus, internal or timeout error) it is left out and the next sync tries again.
`force` (`POST /v1/packs/sync?force=true`, `demo-ingest sync-packs --force`) ingests every pack again.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `KNOWLEDGE_DIR` | `/knowledge` | the catalog volume |
| `PACKS_DIR` | `/packs` | industry packs (`data/packs`) |
| `MILVUS_URI` | `http://milvus:19530` | Milvus (a path ending `.db` uses Milvus Lite) |
| `PARSE_BASE_URL` | `http://parse:8000/v1` | Nemotron Parse 2.0 (vLLM, OpenAI-compatible); empty disables it |
| `PARSE_MODEL` | `nvidia/NVIDIA-Nemotron-Parse-2.0` | served model id |
| `PARSE_API_KEY` / `/run/secrets/parse_api_key` | none | optional bearer token |
| `RETRIEVER_BASE_URL` | `https://integrate.api.nvidia.com/v1` | embeddings endpoint |
| `RETRIEVER_API_KEY` / `/run/secrets/retriever_api_key` | none | embeddings key (documents fail without it) |
| `RETRIEVER_EMBED_MODEL` | `nvidia/nemotron-3-embed-1b` | embed model |
| `INGEST_WORKERS` | `2` | files ingested at once |
| `INGEST_MAX_FILE_MB`, `INGEST_MAX_FILES` | `100`, `20` | upload limits (per file, per request) |
| `PARSE_CONCURRENCY` | `4` | pages in flight to Parse per document |
| `INGEST_STAGE_TIMEOUT_SECONDS` | `1800` | per-stage timeout |
| `INGEST_TOKENIZER_DIR` | `/opt/demo-ingest/tokenizer` | the embed model's tokenizer, for chunk sizes |

`NVIDIA_API_KEY` and `NVIDIA_BASE_URL` are never read.

## Development

```bash
uv run --directory ingest pytest -q
ruff check ingest && ruff format --check ingest
```

The tests are offline: a fake embedder, Milvus Lite, and a fake OpenAI-compatible server that replays recorded
Nemotron Parse 2.0 responses (`tests/fixtures/parse/`).
