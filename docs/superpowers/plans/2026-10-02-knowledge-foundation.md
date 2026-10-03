<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# NVIDIA Knowledge Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the imported market-analysis-claw-demo into NVIDIA Knowledge Foundation: industry packs selectable in
the UI, user uploads ingested by a docling + Nemotron Parse + DuckDB pipeline, and a Hermes agent answering with
cited documents, SQL and Kumo predictions, runnable on a DGX Spark and on Brev.

**Architecture:** A shared, co-resident knowledge catalog (`/knowledge`) written only by a new `ingest` service and
read per call by the API and three data-independent MCP servers (`retrieval`, `tables`, `prediction`). Industry
packs are committed files ingested through the same pipeline as uploads. The rest of the base (Hermes in
OpenShell, Switchyard, Phoenix, job API, AI-Q-derived UI) keeps its design.

**Tech Stack:** Python 3.12 (uv per service), FastAPI, docling-slim 2.132 (Nemotron Parse 2.0 preset via the API
engine), vLLM 0.27.1, langchain-nvidia-ai-endpoints 1.4.3, pymilvus 2.6 / Milvus 2.6.25, DuckDB 1.5.5, sqlglot,
kumo-relational-client 1.0.2, MCP Python SDK 2.2, Next.js 16 / React 18 / KUI / Zustand, Playwright 1.63.

**Spec:** `docs/superpowers/specs/2026-10-02-knowledge-foundation-design.md` (read it first; this plan argues from it).

## Global Constraints

- Python 3.12 only; one `uv` project and `uv.lock` per service directory; `ruff.toml` at the root (line length 120,
  `force-single-line` isort). Run `uv run --directory <dir> pytest` and `ruff check <dir> && ruff format --check <dir>`.
- Every new source file carries the SPDX header used across the repo
  (`SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.` and
  `SPDX-License-Identifier: Apache-2.0`).
- Services share JSON contracts only (`contracts/`); no service imports another service's Python code.
- Never read or set `NVIDIA_API_KEY` or `NVIDIA_BASE_URL`; every NVIDIA client gets an explicit base URL and key.
- Dockerfiles: base images pinned by digest (reuse the digests already in `api/Dockerfile` and
  `tools/retrieval/Dockerfile`), no `EXPOSE`, non-root user (uid 10001, as `api/Dockerfile`).
- Host ports, all on `127.0.0.1`: ui 3300, api 8300, switchyard 4300, phoenix 6306, openshell 18380/18381,
  retrieval 8320, tables 8321, prediction 8322, ingest 8330, parse 8340, auto-ontology-mcp 3303. In-sandbox URLs use
  `host.openshell.internal:<port>`.
- Names: product "NVIDIA Knowledge Foundation"; Compose project `knowledge-foundation`; images
  `knowledge-foundation/<name>:local`; sandbox namespace label `knowledge-foundation`; Switchyard routes `knowledge`,
  `knowledge-aux`, `knowledge-fallback`, `knowledge-efficient`, `knowledge-capable`; Phoenix project
  `knowledge-foundation`.
- MCP servers: streamable HTTP at `/mcp`, `GET /health`, every tool annotated `read_only_hint=True`, every result at
  most 30,000 characters as JSON text, `source_ids` injected by the agent plugin (the model never chooses scope).
- Catalog contracts: `contracts/catalog/source-manifest.schema.json`, `contracts/catalog/pack-manifest.schema.json`;
  fixtures in `contracts/fixtures/catalog/` (packs, sources, and CSVs that build the fixture DuckDB).
- Pack format: `data/schemas/pack.schema.json` and `data/schemas/questions.schema.json` (schema version `"3"`).
- Tool registry: `contracts/tool-registry.json` (tools `retrieve_evidence`, `query_tables`, `predict`,
  `ask_question`; pills `retrieval`, `duckdb`, `kumo`, `ontology`).
- All industry data is synthetic and says so (pack `disclaimer`, README data card).

## Review Focus

1. A renamed or empty file in an upload batch (a `.pdf` that is really a zip, a 0-byte `.csv`): that file fails
   with a clear `error_message`; the rest of the batch ingests. Test in Task A3.
2. A spreadsheet with awkward headers (spaces, duplicates, unicode, leading blank rows) and several sheets: every
   sheet becomes a table with sanitized, unique column names, or that sheet fails alone. Test in Task A2.
3. SQL from the agent that tries to escape (`ATTACH`, `COPY`, `read_csv('/etc/passwd')`, `PRAGMA`, `SET`,
   two statements, `INSTALL`): refused before execution. Test in Task B2.
4. A restored chat session whose sources belong to another industry than the one selected: the UI switches the
   industry to the session's pack instead of silently dropping the sources. Test in Task F2.
5. The Parse server down or slow while a PDF ingests: the file falls back to the text layer with a visible warning
   (PDF) or fails with `parser_unavailable` (image), within the stage timeout. Test in Task A3.

---

## Workstreams and file ownership

Workstreams run in parallel in separate worktrees. Each owns its files; nobody edits another's files. The
integrator (Workstream G) merges, regenerates contracts and resolves the few shared files.

| WS | Owner of | Depends on |
|---|---|---|
| A ingest | `ingest/**` (new) | Phase 0 contracts |
| B tools | `tools/retrieval/**`, `tools/tables/**` (new), `tools/prediction/**` (new), deletes `tools/market-analytics/**` | Phase 0 |
| C agent | `agent/**` | Phase 0, B's tool signatures (below) |
| D packs | `data/packs/<industry>/**` (new), one agent per industry; deletes nothing | Phase 0 pack schema |
| E api | `api/**`, `contracts/fixtures/receipts.json`, `contracts/fixtures/execution-events.json` | Phase 0 |
| F ui | `ui/**` | E's routes (below); runs `scripts/gen-contracts.sh` after E merges |
| G ops (integrator) | `compose.yaml`, `scripts/**`, `infra/**`, `.env.example`, `eval/**`, `data/` project (not packs), `docs/**`, `README.md`, `AGENTS.md`, `CLAUDE.md`, `.pre-commit-config.yaml`, `.gitignore` | everything |

---

## Phase 0: Shared contracts (done by the integrator before dispatch)

- [x] Spec and this plan committed.
- [x] `contracts/tool-registry.json` and its schema: servers `retrieval`, `tables`, `prediction`, `auto_ontology`;
  families `unstructured_retrieval`, `structured_retrieval`, `structured_prediction`; explorers `retrieval`, `sql`,
  `pql`, `ontology`; receipt kinds `retrieval_evidence`, `structured_query`, `structured_prediction`; profiles
  `retrieval`, `tables`, `kumo`, `ontology`; pills `retrieval`, `duckdb`, `kumo`, `ontology`.
- [x] `contracts/catalog/*.schema.json` and `contracts/fixtures/catalog/**`.
- [x] `data/schemas/pack.schema.json` and `questions.schema.json` at schema version 3.

---

## Workstream A: ingest service

### Task A1: Project, settings, catalog writer and job store

**Files:**
- Create: `ingest/pyproject.toml`, `ingest/README.md`, `ingest/src/demo_ingest/{__init__,__main__,settings,catalog,store,models}.py`
- Test: `ingest/tests/{conftest,test_catalog,test_store}.py`

**Interfaces:**
- Produces:
  - `Settings.from_env()` with `knowledge_dir: Path` (`KNOWLEDGE_DIR`, default `/knowledge`), `packs_dir: Path`
    (`PACKS_DIR`, default `/packs`), `milvus_uri` (`MILVUS_URI`), `collection_alias` (`knowledge`),
    `parse_base_url` (`PARSE_BASE_URL`, default `http://parse:8000/v1`; empty disables Parse), `parse_model`
    (`PARSE_MODEL`, default `nvidia/NVIDIA-Nemotron-Parse-2.0`), `parse_api_key` (secret file
    `/run/secrets/parse_api_key` or `PARSE_API_KEY`, optional), `retriever_base_url`, `retriever_api_key` (secret
    file `/run/secrets/retriever_api_key`), `embed_model` (`RETRIEVER_EMBED_MODEL`, default
    `nvidia/nemotron-3-embed-1b`), `workers` (`INGEST_WORKERS`, 2), `max_file_mb` (100), `max_files` (20),
    `parse_concurrency` (`PARSE_CONCURRENCY`, 4), `stage_timeout_s` (`INGEST_STAGE_TIMEOUT_SECONDS`, 1800).
  - `Catalog(root: Path)`: `write_source(manifest: dict)`, `read_source(id) -> dict | None`, `sources() -> list[dict]`,
    `write_pack(manifest: dict)`, `read_pack(id)`, `packs()`, `delete_source(id)`, `source_dir(id) -> Path`.
    Every write validates against `contracts/catalog/*.schema.json` (copied into the image at
    `/opt/demo-ingest/contracts/catalog/`; tests read the repo copy) and is atomic (`tempfile` in the same
    directory + `os.replace`).
  - `JobStore(path)`: SQLite WAL with tables `jobs(job_id, collection, status, submitted_at, started_at,
    completed_at, error_message)`, `files(file_id, job_id, collection, source_id, file_name, sha256, size_bytes,
    kind, status, stage, stage_detail, progress_percent, parser, chunks, tables_json, warnings_json, error_message,
    uploaded_at, ingested_at)`. Methods: `create_job(collection, files) -> job_id`, `update_file(file_id, **fields)`,
    `job_status(job_id) -> dict` (the AI-Q `IngestionJobStatus` shape plus `stage`, `stage_detail`, `kind`,
    `parser`, `tables`, `warnings` per file), `files(collection) -> list[dict]` (AI-Q `FileInfo` plus the same extras),
    `unfinished() -> list[file rows]` (for restart recovery), `delete_files(collection, file_ids)`.
  - Status mapping to AI-Q's enums: file `uploading` until stored, `ingesting` while any stage runs, `success` when
    `ready`, `failed`; job `pending`, `processing`, `completed` (all files terminal, at least one success),
    `failed` (all files failed).

- [ ] **Step 1: Write failing tests** — catalog round-trip of every fixture in `contracts/fixtures/catalog`;
  an invalid manifest (missing `kind`) raises `CatalogError` and leaves no file behind; job store: create a job of
  two files, update one to `ready`, assert `job_status()["status"] == "processing"` then `"completed"` after the
  second; `unfinished()` returns files whose status is not terminal.
- [ ] **Step 2:** `uv run --directory ingest pytest -q` fails (module missing).
- [ ] **Step 3:** Implement. Dependencies: `fastapi`, `uvicorn[standard]`, `python-multipart`, `pydantic>=2.13`,
  `jsonschema`, `duckdb==1.5.5`, `openpyxl`, `filetype`, `pymilvus>=2.6.17,<2.7`,
  `langchain-nvidia-ai-endpoints==1.4.3`, `tenacity`, `docling-slim[format-pdf-pypdfium2,format-office,format-html,format-markdown,format-latex,feat-chunking,convert-core]==2.132.0`,
  `pyyaml`; dev: `pytest`, `httpx`, `milvus-lite>=3.2.1`, `reportlab`, `python-docx`.
- [ ] **Step 4:** Tests pass; ruff clean.
- [ ] **Step 5:** Commit `feat(ingest): catalog writer and ingestion job store`.

### Task A2: Tables pipeline (DuckDB load and profiling)

**Files:**
- Create: `ingest/src/demo_ingest/tables.py`
- Test: `ingest/tests/test_tables.py`, fixtures `ingest/tests/fixtures/{awkward.xlsx (generated in conftest), orders.csv, empty.csv}`

**Interfaces:**
- Produces: `load_table_file(db_path: Path, file_path: Path, file_name: str, existing: set[str]) -> LoadResult`
  where `LoadResult = {tables: list[str], parser: str, warnings: list[str]}`; `profile_database(db_path: Path,
  declarations: dict[str, dict] | None) -> list[TableInfo dict]` (the `TableInfo` of the source-manifest schema).
- Rules: CSV/TSV via `read_csv(path, sample_size=-1)`; XLSX: sheet names from
  `openpyxl.load_workbook(read_only=True).sheetnames`, each via `read_xlsx(path, sheet=..., header=true)` after
  `INSTALL excel; LOAD excel;` (the image pre-installs it; tests may install); Parquet `read_parquet`; JSON/JSONL
  `read_json_auto`. Table name: snake-case of the file stem (`_<sheet>` appended when the workbook has more than one
  sheet), unique against `existing` with `_2`, `_3`. Column names: snake-cased, non-empty, unique (`col`, `col_2`).
  Write with `CREATE OR REPLACE TABLE`. A file that yields no rows fails with `empty_file`. Profiling: row count;
  DuckDB types; nullable from null counts; primary key = declared, else the first column whose values are unique and
  non-null, preferring names `id` or ending in `_id`; time column = declared, else the first DATE/TIMESTAMP column
  (prefer names ending `_at`, `_date`, `date`); foreign keys = declared (`inferred: false`) plus inferred ones
  (`inferred: true`): a column named like another table's primary key, or ending `_id` with that key's name, whose
  distinct non-null values are all contained in that key. Descriptions from declarations (table and per-column),
  else empty.

- [ ] **Step 1: Failing tests** — `orders.csv` + `customers.csv` from `contracts/fixtures/catalog/tables` load into
  `customers`, `orders`; profiling finds `customer_id`/`order_id` keys, `ordered_at` time column and the inferred
  foreign key `orders.customer_id → customers.customer_id`; an XLSX built in the test with sheets `Q1 Sales` and
  `Q2 Sales`, headers `Store #`, `Net Sales ($)`, `Net Sales ($)`, `Région` loads two tables
  `awkward_q1_sales`, `awkward_q2_sales` with columns `store`, `net_sales`, `net_sales_2`, `region`; a 0-byte CSV
  raises `IngestError("empty_file")`; declared keys override inferred ones.
- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit `feat(ingest): load spreadsheets into DuckDB and profile keys`.

### Task A3: Documents pipeline (docling + Nemotron Parse + chunk + embed + Milvus)

**Files:**
- Create: `ingest/src/demo_ingest/{detect,documents,embed,index}.py`
- Test: `ingest/tests/{test_detect,test_documents,test_index}.py`, fixtures: `ingest/tests/fixtures/parse/` holding
  recorded Nemotron Parse 2.0 responses (record them once from the running `parse` service at
  `http://127.0.0.1:8340`, or from the spike: the service on this host answers now) and a fake OpenAI-compatible
  server in `conftest.py` that replays them.

**Interfaces:**
- Produces:
  - `detect(path, file_name) -> Detected{kind: "document"|"table", format: str}`; raises `IngestError` with codes
    `unsupported_type`, `type_mismatch` (extension disagrees with `filetype.guess`; text formats skip sniffing),
    `empty_file`, `too_large`.
  - `convert(path, fmt, settings, on_progress) -> Converted{document: DoclingDocument, parser: str, pages: int,
    warnings: list[str]}`. PDF and images: `DocumentConverter` with `PdfFormatOption(pipeline_cls=VlmPipeline,
    pipeline_options=VlmPipelineOptions(vlm_options=VlmConvertOptions.from_preset("nemotron_parse_v2",
    engine_options=ApiVlmEngineOptions(engine_type=VlmEngineType.API, url=f"{parse_base_url}/chat/completions",
    params={"model": parse_model, "skip_special_tokens": False, "top_k": 1, "repetition_penalty": 1.1,
    "temperature": 0, "max_tokens": 8192}, headers={"Authorization": f"Bearer {key}"} if key, timeout=300,
    concurrency=parse_concurrency)), enable_remote_services=True), backend=PyPdfiumDocumentBackend)` and the same
    for `InputFormat.IMAGE` with `ImageFormatOption`. Parser name `nemotron-parse-2.0`. Office/HTML/MD/TXT: default
    `DocumentConverter` format options; parser `docling-<fmt>`. Fallback: when Parse is disabled, unreachable
    (`GET {parse_base_url}/models` fails) or the conversion fails, a PDF converts with docling's standard
    `PdfPipelineOptions(do_ocr=False, do_table_structure=False)` is NOT used (it needs layout models); instead use
    `pypdfium2` page text → one Markdown section per page → `DocumentConverter` for Markdown; parser
    `pdf-text-layer`; warning `"Parsed from the PDF's text layer: Nemotron Parse was unavailable (<reason>)."`
    An image then fails with `parser_unavailable`. Page progress is reported per page through `on_progress`.
  - `chunk(document, tokenizer) -> list[Chunk{text (contextualized), page_start, page_end, headings, labels}]` with
    docling `HybridChunker(tokenizer=HuggingFaceTokenizer(AutoTokenizer.from_pretrained(<embed tokenizer dir>),
    max_tokens=512))`. The tokenizer: the embed model's Hugging Face tokenizer (`nvidia/Nemotron-3-Embed-1B-BF16`,
    files only) baked into the image at `/opt/demo-ingest/tokenizer`; `INGEST_TOKENIZER_DIR` overrides; tests use a
    tiny tokenizer (`HuggingFaceTokenizer` over `bert-base-uncased` is fine if cached, else docling-core's default).
  - `Embedder(settings)`: `NVIDIAEmbeddings(model=embed_model, base_url=retriever_base_url, api_key=key,
    truncate="END")` with `register_model` exactly as `tools/retrieval/src/demo_retrieval/nvidia.py`, batches of 50,
    tenacity retries on transient errors (8 attempts); `embed_documents(texts) -> list[list[float]]`.
  - `KnowledgeIndex(uri, alias)`: creates the collection with the schema of
    `tools/retrieval/src/demo_retrieval/store.py:create_collection` (copy the field list; do not import it) if the
    alias is missing, named `knowledge__v1`, and points the alias at it; `replace_document(source_id, document_id,
    title, url, chunks, vectors, metadata)` deletes `document_id == "<id>"` then inserts; `delete_document`,
    `delete_source`. Chunk ids `<document_id>:<NNNN>`. Metadata per chunk: `citation` (`"<file name>, p. N"` or
    `"<file name>"`), `page_start`, `page_end`, `headings` (joined with ` › `), `parser`, `file_id`; never the key
    `image` (the VL reranker would read it).
  - `ingest_document(...)` writes `sources/<id>/documents/<document_id>.md` (docling Markdown export) and
    `sources/<id>/chunks/<document_id>.jsonl`, then embeds and indexes.

- [ ] **Step 1: Failing tests** — detection: a zip renamed `.pdf` → `type_mismatch`; 0 bytes → `empty_file`;
  `.exe` → `unsupported_type`. Conversion against the fake Parse server: the two-page fixture PDF (generate it with
  reportlab in `conftest.py`: title, a heading, a paragraph, a 4×3 table, a second page) yields a document with one
  table and ≥2 pages, parser `nemotron-parse-2.0`; with `PARSE_BASE_URL` pointing at a closed port the same PDF
  converts with parser `pdf-text-layer` and the warning; a PNG with Parse down fails `parser_unavailable`. DOCX
  (python-docx in the test) converts with parser `docling-docx`. Chunking keeps page numbers. Index (Milvus Lite
  `ingest/tests/.milvus.db`): replacing a document twice leaves only the second version's chunks.
- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit `feat(ingest): parse documents with docling and Nemotron Parse, index them in Milvus`.

### Task A4: Pipeline worker, HTTP API, pack sync

**Files:**
- Create: `ingest/src/demo_ingest/{pipeline,app,packs}.py`
- Test: `ingest/tests/{test_pipeline,test_app,test_packs}.py`

**Interfaces:**
- Produces: the HTTP API of spec §4.2 (FastAPI app factory `create_app(settings, *, embedder=None, index=None,
  parse_probe=None)` for tests). `workspace` collection: created on startup with two sources materialized lazily
  (`workspace.documents` on the first document, `workspace.tables` on the first table) and the pack manifest
  `catalog/packs/workspace.json` (`kind: workspace`, title "Your data", icon `Upload`, generic questions empty).
  Uploads: stream each part to `sources/<source>/files/<file_id>` (`file_id = "f-" + sha256[:16]`), dedupe by sha256
  inside the collection (return the existing `file_id`, no new work). Worker pool of `INGEST_WORKERS` asyncio tasks
  running blocking stages in a thread pool; per-stage timeout; restart recovery re-queues `unfinished()` files.
  After each file the owning SourceManifest is rewritten (files list, counts, status, `database.tables` from
  `profile_database`, `documents` counts). Deleting files removes their chunks / drops their tables and rewrites the
  manifest.
  `packs.sync_all(settings, catalog, pipeline)`: for each `data/packs/<id>/pack.yaml` (mounted at `/packs`), validate
  with `data/schemas/pack.schema.json` + `questions.schema.json` (copied into the image), compute the digest
  (sha256 over sorted relative paths and bytes of `pack.yaml`, `questions.yaml`, `files/**`), skip when the pack
  manifest's digest matches and status is `ready`; otherwise ingest every source's files through the same
  `pipeline` functions (declared table metadata and prediction templates applied), then write the PackManifest
  (questions' sources prefixed `<pack>.`, `examples`, `conversations`, `icon`, `disclaimer`, `as_of`, status).
  Pack sync runs in the background at startup; `GET /v1/packs/status` reports `{packs: [{id, status, files_total,
  files_done, error}]}`.
- `__main__`: `demo-ingest serve` (uvicorn, one worker, port 8330) and `demo-ingest sync-packs` (synchronous, exits
  non-zero on any failure; used by tests and by `demo.sh data prepare`).

- [ ] **Step 1: Failing tests** with `httpx.ASGITransport`: upload a CSV and the fixture PDF to `workspace` →
  `{job_id, file_ids}`; poll `/v1/documents/{job_id}/status` until `completed`; per-file `stage` is `ready`, the
  table file lists `tables`, the PDF lists `parser`; `catalog/sources/workspace.tables.json` validates and lists the
  table; re-uploading the same CSV returns the same `file_id`; `DELETE` removes it from the manifest. Pack sync of a
  minimal fixture pack under `ingest/tests/fixtures/packs/mini/` (one documents source with a Markdown file, one
  structured source with two CSVs and a declared foreign key and a prediction template) writes both manifests and the
  pack manifest; a second sync is a no-op (digest unchanged).
- [ ] **Steps 2–4:** fail, implement, pass.
- [ ] **Step 5:** Commit `feat(ingest): upload API, worker pool and industry pack sync`.

### Task A5: Image

**Files:** Create `ingest/Dockerfile`, `ingest/.dockerignore`.
- Base `python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f` with uv
  `0.12.22` as `api/Dockerfile`; build contexts `contracts` and `packschemas` (`data/schemas`) copied to
  `/opt/demo-ingest/contracts/catalog/` and `/opt/demo-ingest/schemas/`; at build time: `INSTALL excel` into a
  DuckDB extension directory readable by uid 10001 (`SET extension_directory`, and set the same at runtime via
  `DUCKDB_EXTENSION_DIRECTORY` read by `tables.py`), and download the embed tokenizer files with
  `huggingface_hub.snapshot_download("nvidia/Nemotron-3-Embed-1B-BF16", allow_patterns=["tokenizer*", "special_tokens_map.json", "*.model"], local_dir="/opt/demo-ingest/tokenizer")`
  pinned to a revision. `CMD ["demo-ingest", "serve"]`, `HEALTHCHECK` on `/health`.
- [ ] Build with `docker build --build-context contracts=contracts/catalog --build-context packschemas=data/schemas -t knowledge-foundation/ingest:local ingest`; run `docker run --rm knowledge-foundation/ingest:local demo-ingest --help`.
- [ ] Commit `build(ingest): container image`.

---

## Workstream B: tools

### Task B1: retrieval over the shared catalog

**Files:** Modify `tools/retrieval/src/demo_retrieval/{settings,datapack,search,server,nvidia,__main__}.py`,
`tools/retrieval/README.md`, tests under `tools/retrieval/tests/`. Delete the corpus/index one-shot code paths that
only serve pack builds (`ingest.py` and its CLI, `benchmark.py` and the GPU Milvus files) and their tests.

**Interfaces:**
- `retrieve_evidence(query: str, source_ids: list[str], top_k: int = 8)`: unchanged result shape, except
  `models.rerank` may be `null` and `timings.rerank_ms` is `0` when `RETRIEVER_RERANK_MODEL` is empty (then hits are
  ordered by vector score and `score` equals `vector_score`). Sources are validated per call against
  `/knowledge/catalog/sources/*.json` (`KNOWLEDGE_DIR`): every id must exist, be `kind: documents` and have status
  `ready` or `ingesting`; otherwise `ToolError`. The collection is the alias `knowledge`.
  `collection_version` is the alias's current collection name. Tool description: generic enterprise documents.
- [ ] Tests: fixture catalog + Milvus Lite with chunks inserted for `retail.policies`; a search with a stub embedder
  returns hits only from the requested source; an unknown source id raises; rerank disabled path.
- [ ] Commit `refactor(retrieval): search the shared knowledge catalog`.

### Task B2: tables MCP server (new)

**Files:** Create `tools/tables/{pyproject.toml,README.md,Dockerfile,.dockerignore}`,
`tools/tables/src/demo_tables/{__init__,__main__,settings,catalog,guard,query,server}.py`, tests.

**Interfaces:**
- Tool `query_tables(question: str, sql: str, source_ids: list[str]) -> QueryResult` with
  `QueryResult = {question, sql, database_name, databases: [{source_id, alias}], columns: [str], rows: [dict],
  row_count: int, truncated: bool, elapsed_ms: float, warnings: [str]}`. `database_name` is the alias when one source
  is attached, else `"knowledge"`.
- Execution: a fresh in-memory DuckDB per call in a worker subprocess (as `api/src/demo_api/database/worker.py`), each
  structured source `ATTACH '<knowledge>/<path>' AS <alias> (READ_ONLY)`, then `SET enable_external_access=false;
  SET autoload_known_extensions=false; SET lock_configuration=true;`, the query, `fetchmany(201)`; 10 s timeout
  (kill the worker); 200 rows (`truncated`). Guard with sqlglot (dialect duckdb) exactly one statement of type
  SELECT (with CTEs/UNION allowed), no table functions or references outside the attached aliases
  (`read_*`, `glob`, `query_table`, `parquet_scan`, file paths), no `ATTACH/COPY/PRAGMA/SET/INSTALL/LOAD/EXPORT`.
  Values serialized JSON-safe (dates ISO, decimals float). Result capped at 30,000 characters by dropping rows
  (`truncated`, warning).
- [ ] Tests (the Review Focus line 3): each escape attempt refused with `ToolError`; a join across `retail_sales`
  tables built from the fixture CSVs returns the expected rows; a sleep-like long query (`SELECT count(*) FROM
  range(10000000000)`) times out; two sources attach under two aliases.
- [ ] Commit `feat(tables): read-only SQL over the selected structured sources`.

### Task B3: prediction MCP server (new, Kumo)

**Files:** Create `tools/prediction/**` (same layout as B2). Port `tools/market-analytics/src/market_analytics/prediction.py`
and `tests/test_prediction.py` (`Predictor`, `StubClient`, the window parser), then delete `tools/market-analytics/`.

**Interfaces:**
- Tool `predict(question: str, pql: str, source_ids: list[str], anchor_time: str | None = None) ->
  PredictionResult = {available: bool, reason: str | None, source_id: str, template_id: str | None, pql: str,
  task_type: str | None, anchor_time: str | None, horizon: {value, unit} | None, entity_table: str | None,
  rows: [{entity_id: str, probability: float | None, value: float | None, label: str | None}], model: str,
  elapsed_ms: float, warnings: [str]}`. `pql` may be a template id of the source (`template:<id>`), which expands to
  its PQL and anchor. The source is the single selected structured source whose tables include every table the PQL
  names (parsed from `PREDICT ... FOR EACH <table>.<column>` and aggregate arguments); none or several → `ToolError`.
- Graph: `relational.Graph.from_duckdb(connection=<read-only path>, tables=[{"name", "primary_key", "time_column"}])`
  per TableInfo, then `graph.link(src_table, fkey, dst_table)` for each foreign key, `graph.validate()`. Entities:
  up to 100 ids from the entity table (ordered by primary key; with a `WHERE` in the PQL, as the engine filters).
  Client: `RelationalClient(url=KUMO_RELATIONAL_URL, api_key=KUMO_API_KEY or None, timeout=60, max_retries=0)
  .relational(graph).predict(pql, ids, anchor_time=..., run_mode="fast", num_retries=0)`. Rows: binary → `probability`
  (`TRUE_PROB`), regression → `value` (`PREDICTION`), multiclass → `label` (`CLASS`) with its `SCORE` as probability;
  rows sorted by probability or value, descending, top 25. No URL configured → `available: false`,
  `reason: "No Kumo endpoint is configured (KUMO_RELATIONAL_URL)."` (the tool is still registered).
- [ ] Tests with the ported `StubClient`: a binary PQL over the fixture tables returns sorted probabilities; template
  expansion; a PQL naming a table no selected source has raises; no URL → unavailable.
- [ ] Commit `feat(prediction): Kumo predictions over any structured source`.

---

## Workstream C: agent

### Task C1: Profile, skills, policy, plugin

**Files:** Modify `agent/profile/{SOUL.md,config.yaml,distribution.yaml,relay-plugins.toml}`, `agent/render_config.py`,
`agent/sandbox-policy.yaml`, `agent/Dockerfile`, `agent/profile/plugins/execution_receipts/__init__.py`,
`agent/tests/*`, `agent/README.md`. Delete `agent/profile/skills/analyzing-market-data/`. Create
`agent/profile/skills/querying-tables/SKILL.md`; rewrite `searching-documents`, `predicting-with-kumo`; keep
`querying-auto-ontology` (generic pitfalls).

**Interfaces:**
- `config.yaml`: `mcp_servers` `retrieval` (`http://host.openshell.internal:8320/mcp`, include
  `retrieve_evidence`), `tables` (`:8321`, `query_tables`), `prediction` (`:8322`, `predict`), `auto_ontology`
  (`:3303`, `ask_question`); `platform_toolsets.api_server: [skills, retrieval, tables, prediction, auto_ontology]`;
  model routes `knowledge*` at `http://host.openshell.internal:4300/v1`; `_config_version` unchanged unless Hermes
  requires otherwise.
- `render_config.py` `FEATURES`: `retrieval → (retrieval, searching-documents)`, `tables → (tables,
  querying-tables)`, `kumo → (prediction, predicting-with-kumo)`, `ontology → (auto_ontology,
  querying-auto-ontology)`; default features `retrieval,tables`; drop the `KUMO_TOOL` special case.
- Policy: one MCP network policy per server port with the tool allowlist; Switchyard 4300, receipts API 8300, Phoenix
  6306 (`relay-plugins.toml` endpoint `http://host.openshell.internal:6306/v1/traces`, project
  `knowledge-foundation`).
- Plugin: `_query_content` uses `args.get("question") or args.get("sql")` for `query` and
  `result.get("database_name") or database_name` for `database_name`; `_prediction_content` passes the generalized
  `PredictionResult` (receipt `StructuredPrediction` in E2); `_retrieval_content` tolerates `models.rerank: null`.
  Source scope injection is unchanged (by family).
- `SOUL.md`: "You are NVIDIA Knowledge Foundation's research assistant for enterprise knowledge"; capability table:
  `unstructured_retrieval → searching-documents`, `structured_retrieval → querying-tables` (and
  `querying-auto-ontology` when present), `structured_prediction → predicting-with-kumo`; keep the evidence and
  citation rules verbatim; no investment-advice copy.
- Skills (Agent Skills format; description ≤ 60 chars): `querying-tables` teaches reading the run's source catalog
  (alias, tables, columns, keys), writing one DuckDB SELECT qualified as `<alias>.<table>`, aggregating instead of
  pulling raw rows, and passing `question`; `predicting-with-kumo` teaches PQL (`PREDICT <aggregation>(<table>.<col>,
  start, end, days) <op> <value> FOR EACH <entity_table>.<pk> [WHERE ...]`, binary vs regression, templates via
  `template:<id>`), reading `available:false`, and never claiming certainty.
- [ ] `uv run --directory agent pytest` passes (wiring, config, skills, plugin, openshell tests updated to the new
  names and ports); `uv run --directory agent agentskills validate profile/skills/<each>`.
- [ ] Commit `feat(agent): knowledge assistant profile, skills and tool wiring`.

---

## Workstream D: industry packs (one agent per industry)

### Task D1–D4: `retail`, `manufacturing`, `healthcare`, `financial-services`

**Files:** Create `data/packs/<id>/{pack.yaml,questions.yaml,README.md,generator/build.py,generator/content/**,files/**}`.

**Interfaces:** `pack.yaml` and `questions.yaml` validate against `data/schemas/pack.schema.json` and
`questions.schema.json` (`uv run --with jsonschema --with pyyaml python -c ...`). `generator/build.py` is a PEP 723
script (`# /// script` block listing `numpy`, `pandas`, `openpyxl`, `reportlab`, `python-docx`, `python-pptx`,
`pillow`) run with `uv run data/packs/<id>/generator/build.py`; it is seeded and deterministic and writes `files/`.

Content per pack:
- One `structured` source, `capabilities: [structured_retrieval, structured_prediction]`, 4–6 tables as CSV and
  one multi-sheet XLSX (at least 2 sheets), 1,000–20,000 rows in total, declared primary keys, foreign keys, time
  columns and column descriptions, and 2–3 `prediction.templates` that are valid Kumo PQL over those tables (binary
  outcomes over a 30/60/90-day window with an `anchor_time` inside the data).
- One or two `documents` sources with 5–8 files in total: at least 3 PDFs (one with a real table, one multi-page
  report), 1 DOCX, 1 PPTX, and 1 scanned page (PNG, grayscale, slight rotation and noise, of a form or memo) so
  Nemotron Parse's value is visible. Documents mention entities that exist in the tables (store ids, plant names,
  product names) so hybrid questions work.
- `questions.yaml`: 8–10 questions: 3 DOCUMENTS, 2–3 SQL, 2 HYBRID, 1–2 PREDICTION; 6 `featured`; `examples` with
  every featured one first; tools pills accurate (`retrieval`, `duckdb`, `kumo`). Every answer must be derivable
  from the files (write the expected answer in a comment-free `README.md` "Answer key" section).
- `README.md`: data card (synthetic, seed, tables, documents, answer key).
- Industries: **retail** (stores, products, customers, orders, order_items, returns; churn and return-risk),
  **manufacturing** (plants, machines, sensor_readings daily aggregates, maintenance_events, work_orders,
  quality_defects; failure-in-30-days), **healthcare** (synthetic patients, providers, encounters, diagnoses, claims;
  30-day readmission; no real PHI), **financial-services** (retail banking: customers, accounts, transactions monthly
  aggregates, loans, payments, card_disputes; loan default in 90 days).
- [ ] Generate, validate both YAML files, eyeball every PDF page (render one to PNG and look), commit
  `feat(data): <industry> industry pack`.

---

## Workstream E: API

### Task E1: Knowledge catalog, packs routes, ingest forwarding

**Files:** Create `api/src/demo_api/catalog.py`, `api/src/demo_api/routes/{packs,documents}.py`; modify
`api/src/demo_api/{app,services,settings,pack,registry}.py`, `routes/{sources,jobs}.py`, `hermes/request.py`,
`cli.py`; delete `benchmark/runner.py`, `benchmark/models.py` and the market benchmark routes; tests.

**Interfaces:**
- `KnowledgeCatalog(root, registry, features)`: `packs() -> list[PackSummary]`, `pack_view(pack_id) -> PackView`
  (now with `kind`, `icon`, `status`), `sources(pack_id: str | None = None) -> list[Source]` (all packs when None;
  capabilities narrowed to the running features; documents sources need status `ready`/`ingesting`), `source(id)`,
  `database_path(source_id) -> Path | None`. `Source` gains `pack_id`, `status`, `tables` (TableInfo list) and
  `prediction_templates`; `catalog_entry()` adds, for structured sources, `database: {alias, tables: [{name,
  description, row_count, primary_key, time_column, columns: [{name, type, description}] (≤ 40 per table),
  foreign_keys}] (≤ 30 tables)}` and `prediction_templates`.
- Questions offered only when every tool pill they declare is served (`kumo` needs the `kumo` feature, `ontology`
  the `ontology` feature, `duckdb` the `tables` feature, `retrieval` the `retrieval` feature).
- Routes: `GET /v1/packs` → `{packs: [{id, kind, title, description, icon, status}]}` (industries sorted by title,
  then the workspace); `GET /v1/pack?id=` (default: first industry) → `PackView`; `GET /v1/data_sources?pack=` (all
  when omitted); data viewer routes resolve the DuckDB per source. Submit accepts `pack_id` (optional) and any
  catalog source ids; stored request keeps `pack_id`.
- Forwarding: `/v1/collections`, `/v1/collections/{name}`, `/v1/collections/{name}/documents` (GET, POST multipart
  streamed with a size cap `INGEST_MAX_REQUEST_MB` default 512, DELETE with JSON body), `/v1/documents/{job_id}/status`
  → `INGEST_URL` (default `http://ingest:8330`), returning the ingest status code and body.
- Receipts (E2) and recorder: `demo-api record --pack <id>` writes `data/packs/<id>/recordings/` and the bundle's
  `pack.json` is that pack's `PackView`; `database.json` holds the first rows of each table of each structured source
  of the pack (keyed by source id).
- [ ] Tests: fixture catalog → `/v1/packs` lists retail and workspace; `/v1/pack?id=retail` hides `churn-risk` without
  the kumo feature and shows it with it; `/v1/data_sources?pack=retail` returns both sources with `database_name`
  `retail_sales`; forwarding with a mocked ingest (`httpx.MockTransport`); submit with `retail.sales` stores the
  catalog entry with tables.
- [ ] Commit `feat(api): knowledge catalog, packs and document routes`.

### Task E2: Contracts

**Files:** `api/src/demo_api/receipts/models.py`, `events/execution.py`, `contracts.py`, `pills.py`,
`contracts/fixtures/{receipts,execution-events}.json`; delete the benchmark schema exports.

**Interfaces:**
- `ArtifactKind = Literal["retrieval_evidence", "structured_query", "structured_prediction"]`; delete
  `AnalyticsResult`, `MarketOperation` and friends.
- `RetrievalModels.rerank: str | None`.
- `StructuredPrediction`: `available`, `reason`, `source_id: OpenIdentifier`, `template_id: OpenIdentifier | None`,
  `pql`, `task_type: str | None`, `anchor_time: AwareDatetime | None`, `horizon: PredictionHorizon | None`,
  `entity_table: str | None`, `rows: tuple[EntityPrediction, ...]` with `EntityPrediction{entity_id: OpenIdentifier,
  probability: float | None (0..1), value: float | None, label: str | None}`, `model`. Same availability validator.
- `COMPONENT_BY_FAMILY`: `unstructured_retrieval`, `structured_retrieval`, `structured_prediction`.
- `scripts/gen-contracts.sh` writes `execution-event`, `receipt`, `retrieval-benchmark` (if kept), `pack`, plus the new
  `packs` (`PackList`) schema. (G updates the script's mapping list; E lists the exports in `contracts.py`.)
- [ ] `uv run --directory api pytest` green; `scripts/gen-contracts.sh --check` green after G's script change.
- [ ] Commit `feat(api): generic prediction receipts; drop market contracts`.

---

## Workstream F: UI

### Task F1: Rebrand and market removal

**Files:** `ui/src/app/layout.tsx`, `features/landing/**`, `features/execution/**` (explorers, graph, activity,
benchmark), `shared/components/ToolPills/**`, `app/globals.css`, `package.json` name, storage keys
(`kf-chat-store`, `kf-deep-research-`), `public/ecosystem-logos/**`, tests and e2e fixtures.
- Product name "NVIDIA Knowledge Foundation"; delete market explorers, market graph nodes, `gpu-acceleration.ts`, the
  market Benchmark tab; graph resources: Documents (Nemotron Parse → Nemotron Embed → Milvus → Rerank), Tables
  (DuckDB), Prediction (NVIDIA Kumo), Ontology; the `sql` explorer renders `structured_query` receipts (SQL, rows,
  database); the `pql` explorer renders the generalized prediction (entity, probability/value/label).
- Landing: "Ask your enterprise knowledge" with the industry selector and featured questions of the selected pack.
- [ ] `npm --prefix ui run lint && npm --prefix ui run type-check && npm --prefix ui run test:ci` green.
- [ ] Commit `feat(ui): NVIDIA Knowledge Foundation branding; drop market views`.

### Task F2: Industry selector

- `AppBar` right group, first item: KUI `Select` (`data-testid="industry-select"`, `aria-label="Industry"`) listing
  `GET /api/v1/packs` (industries, then "Your data"); the choice is `?pack=` plus cookie `kf-pack`; server pages call
  `fetchPack(packId)`; `layoutStore.switchPack(id)` resets sources, closes execution, starts a new draft;
  `fetchDataSources('api', packId)`; `Conversation.packId` saved; restoring a session whose `packId` differs switches
  the pack first (Review Focus 4). Disabled while streaming. Same control on the landing header.
- Replay: `/api/recordings/<pack>/...` (validated pack id; `PACKS_DIR`), sources and data viewer per pack.
- [ ] Vitest for the selector, store and restore path; commit `feat(ui): industry selector`.

### Task F3: Your data (restore AI-Q documents, with pipeline stages)

- Restore from AI-Q `bf4e67d1` (`frontends/ui/src/...`; copies are in `/tmp/aiq-up/` on the build host, else fetch
  from `https://raw.githubusercontent.com/NVIDIA-AI-Blueprints/aiq/bf4e67d1564ef8d2ec8f65b5f9001e512befc095/frontends/ui/src/<path>`):
  `features/documents/*`, `adapters/api/documents-{client,schemas}.ts`, `shared/config/file-upload.ts`,
  `features/chat/components/FileUploadBanner.tsx`, `features/layout/components/{DataConnectionsTab,FileSourcesTab,FileSourceCard,DeleteFileConfirmationModal}.tsx`,
  and the InputArea paperclip, drag-and-drop and file counter. Keep upstream SPDX headers; record the restore in
  `ui/UPSTREAM.md`.
- Adapt: the collection is always `workspace`; the Files tab shows when the selected pack is the workspace (and the
  panel opens to it); accepted types `.pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp,.docx,.pptx,.html,.md,.txt,.csv,.tsv,.xlsx,.parquet,.json,.jsonl`
  (env `FILE_UPLOAD_ACCEPTED_TYPES`), max 100 MB, 20 files; polling interval 1.5 s while active; zod schemas accept
  the extra fields (`kind`, `stage`, `stage_detail`, `parser`, `tables`, `warnings`); `FileSourceCard` shows a
  stage stepper (document: Parse → Chunk → Embed → Index; table: Load → Profile), the progress bar, the parser
  ("Nemotron Parse 2.0", "Docling DOCX", "PDF text layer", "DuckDB CSV"…), warnings, and for tables their names with a
  link that opens the data viewer. When files become ready the layout store refreshes data sources.
- Proxy (`app/api/v1/[...path]/route.ts`): add the documents routes; `DELETE`; stream multipart bodies (no
  `request.text()`), cap `FILE_UPLOAD_MAX_REQUEST_MB`; pass `content-type` with its boundary.
- [ ] Vitest for client schemas, orchestrator and card; commit `feat(ui): upload your own files and watch them ingest`.

### Task F4: Playwright

- `e2e/fake-api.mjs`: packs list (retail, manufacturing, workspace), per-pack pack view and data sources, a
  documents API that advances stages on each status poll, and jobs answering with cited reports.
- `e2e/smoke.spec.ts`: landing → selector shows industries; switching to Manufacturing changes the picker's examples,
  the sources panel and `?pack=`; Your data: upload `e2e/fixtures/files/{policy.pdf,orders.csv}` via
  `setInputFiles`, stages progress to Available, the sources panel lists "Your documents" and "Your tables", a
  question returns a cited answer.
- `e2e/recordings.spec.ts`: every recorded session of every pack replays (packs with recordings only).
- Visual baselines: drop the market ones; re-record the rest with `e2e/visual/run.sh --update` (Playwright image).
- [ ] `npm --prefix ui run e2e` green; commit `test(ui): Playwright coverage for industries and uploads`.

---

## Workstream G: ops and integration (integrator)

### Task G1: Compose, scripts, infra

- `compose.yaml`: project `knowledge-foundation`; volumes `knowledge`, `api-data`, `phoenix-data`, `milvus-data`,
  `switchyard-data`, `openshell-state`, `openshell-client`, `parse-cache`, `auto-ontology-db`; services per spec
  §4.7: `ingest` (core; `knowledge:/knowledge`, `./data/packs:/packs:ro`, secrets `retriever_api_key`,
  `parse_api_key`), `retrieval`, `tables` (core; `knowledge:/knowledge:ro`), `prediction` (profiles `kumo`,
  `prediction`), `kumo-relational` (kumo), `parse` (profile `parse`: `vllm/vllm-openai:v0.27.1@sha256:0a51ea5b…`,
  `gpus: all`, `ipc: host`, entrypoint that downloads `vllm_tied_patch/sitecustomize.py` at revision
  `b6742064f4a8cf22a10383ece5e7fbead355ac04` into the cache and serves with `PYTHONPATH` set,
  `--revision`, `--dtype bfloat16 --max-num-seqs 8 --limit-mm-per-prompt '{"image": 1}' --trust-remote-code
  --gpu-memory-utilization ${PARSE_GPU_MEMORY_UTILIZATION:-0.15}`, health `/health`, start period 600 s); drop
  `data`, `data-corpus`, `retrieval-index`, `data-fetch`, `market-analytics*`, `milvus-gpu`, `retrieval-benchmark`.
- `scripts/demo.sh` and `scripts/lib/*.sh`: new names, ports, profiles (`core`, `parse`, `kumo`, `prediction`,
  `ontology`, `replay`), `agent_features` (`retrieval,tables` + `kumo` when `kumo` or `prediction` is on),
  `TOOL_IMAGES` (retrieval, tables, prediction), `PYTHON_PROJECTS` (+ ingest, tables, prediction), `data prepare`
  → `ingest sync-packs`, `data validate` → schema validation of every pack, `data generate <pack>` → the pack's
  generator, `record --pack <id>`, `replay` (no `DATA_PACK` needed), `test live` per pack; doctor: aarch64 hosts
  refuse `kumo` and explain `KUMO_RELATIONAL_URL` + `prediction`; `parse` needs the nvidia runtime.
- `infra/switchyard/routes/*.toml.tmpl` route ids `knowledge*`; `infra/openshell/gateway.toml` name/label and ports;
  `infra/openshell/providers/*.yaml` ports 4300/8300.
- `.env.example`: Knowledge Foundation sections (inference, retriever incl. empty rerank for gateways without
  `/ranking`, parse, profiles `core,parse` on Spark and `core,parse,kumo` on x86, Kumo URL, secrets).
- `data/`: remove the market builder (`src/demo_data/*` except what validates packs), `generate/`, market tests;
  `demo-data validate` validates every pack against the schemas.
- `eval/`: drop market checks and `perf.py`; answers per pack from `data/packs/<id>/eval/answers.yaml` if present.
- Docs: README, `docs/architecture.md`, `configuration.md`, `operations.md` (Spark and Brev), `data-packs.md`,
  `retrieval.md` → `ingestion.md`, `decisions.md` (new decisions 21+), `customize.md`, AGENTS.md/CLAUDE.md.
- [ ] `docker compose config -q` for `core,parse`, `core,parse,kumo`, `replay`; `bash -n` + shellcheck on scripts.

### Task G2: Integrate, bring up, verify, record

- Merge A–F; `scripts/gen-contracts.sh`; all unit suites; build images; `./scripts/demo.sh up` on the Spark with
  the internal gateway key (agent `nvidia/nvidia/nemotron-3-ultra`, aux `nvidia/nvidia/nemotron-3-super-v3`, embed
  `nvidia/nvidia/nemotron-3-embed-1b`, rerank off); `demo.sh check`; live questions per industry; upload flow;
  `demo.sh record --pack <id>` for each industry's featured questions; Playwright replay; open Chrome at
  `http://127.0.0.1:3300`.
