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
  sources/<source_id>/files/<file_id>             original bytes
  sources/<source_id>/documents/<document_id>.md  docling Markdown export
  sources/<source_id>/chunks/<document_id>.jsonl  chunks with metadata, for re-embedding without re-parsing
  sources/<source_id>/tables.duckdb               structured sources
  ingest/ingest.sqlite3                           jobs and files (SQLite, WAL)
```

Every manifest write is validated against its contract and atomic (`tempfile` + `os.replace`).

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
