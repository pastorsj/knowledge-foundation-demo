<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# retrieval

The `retrieve_evidence` MCP tool. It searches the document sources of the knowledge catalog with NVIDIA Nemotron
embed and (optionally) rerank models, through LangChain's `langchain-nvidia-ai-endpoints`, over Milvus.

`demo-retrieval serve` (the image's only command) serves streamable HTTP at `:8320/mcp` and `GET /health`.

## How it fits

The `ingest` service owns the data. It parses and chunks every documents source, embeds the chunks and writes them
to one Milvus collection, `knowledge__v<N>`, behind the alias `knowledge`, with the schema of
`store.create_collection`. It also writes the catalog, `/knowledge/catalog/sources/<source_id>.json`
(`contracts/catalog/source-manifest.schema.json`). This service never writes either.

Hermes (in the OpenShell sandbox) calls `mcp__retrieval__retrieve_evidence` at
`http://host.openshell.internal:8320/mcp`. The agent plugin's `pre_tool_call` hook sets `source_ids` to the run's
selected document sources; the model never chooses them. The plugin turns each result into a `retrieval_evidence`
receipt for the UI.

Each call runs the same steps:

1. Read the manifest of each requested source (`KNOWLEDGE_DIR`, read on every call, never cached). Every id must
   exist, be `kind: documents` and have status `ready` or `ingesting` (what an ingesting source already holds stays
   searchable); otherwise the call fails with a `ToolError` that names each refused source.
2. Embed the query once (`input_type=query`).
3. Resolve the alias `knowledge` once, so every source is searched in the same collection, even while ingest
   re-points the alias.
4. Search every source for the same share of candidates: `min(4 × top_k, 200 ÷ sources)`.
5. Rerank all candidates together in one request (passages carry text only). Without a rerank model, the
   candidates keep their vector-search order.

The server sends OpenInference spans (`embed`, `search`, and `rerank` when reranking) to Phoenix. They nest under
the MCP SDK's `tools/call retrieve_evidence` span.

## Environment

| Variable | Default | Notes |
|---|---|---|
| `RETRIEVER_BASE_URL` | `https://integrate.api.nvidia.com/v1` | build.nvidia.com, a self-hosted NIM, or another OpenAI-compatible endpoint |
| `RETRIEVER_API_KEY` | required | Or the Compose secret file `/run/secrets/retriever_api_key` |
| `RETRIEVER_EMBED_MODEL` | `nvidia/nemotron-3-embed-1b` | Must be the model ingest embedded the chunks with |
| `RETRIEVER_RERANK_MODEL` | `nvidia/llama-nemotron-rerank-vl-1b-v2` when unset | Set it empty to turn reranking off (a gateway without NVIDIA's ranking route) |
| `RETRIEVER_RERANK_URL` | unset | Full rerank URL; see below |
| `MILVUS_URI` | `http://milvus:19530` | A local `*.db` path uses Milvus Lite (dev only) |
| `KNOWLEDGE_DIR` | `/knowledge` | The knowledge volume, mounted read-only |
| `RETRIEVAL_PORT` | `8320` | |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | unset | e.g. `http://phoenix:6006/v1/traces`; no spans are exported when unset |

`NVIDIA_BASE_URL` and `NVIDIA_API_KEY` are never read.

Unlike the other variables, `RETRIEVER_RERANK_MODEL` tells unset from empty: unset means the default reranker,
empty means none. Compose's `${RETRIEVER_RERANK_MODEL:-}` renders an unset `.env` value as empty, so under Compose
`.env` must name the model to keep reranking on (`.env.example` does).

Rerank requests go to the first of these that applies:

1. `RETRIEVER_RERANK_URL`, if set.
2. The model's build.nvidia.com endpoint, when the base URL is build.nvidia.com.
3. `{RETRIEVER_BASE_URL}/ranking`, the self-hosted NIM path.

## Data contract

- **Catalog:** `<KNOWLEDGE_DIR>/catalog/sources/<source_id>.json`. This service reads `id`, `kind` and `status`.
  A source id must match the contract's pattern (`<pack>.<name>`), so an id can never name another path.
- **Milvus:** the alias `knowledge` and the collection it points at. Columns: `chunk_id` (primary key),
  `source_id` (partition key), `document_id`, `title`, `url`, `published_at`, `text` and the `embedding`
  `FLOAT_VECTOR`, HNSW/COSINE (`M=16`, `efConstruction=200`, searched with `ef=128`). Dynamic fields hold each
  source's own metadata (file name, page, ...), returned in each hit's `metadata`. The metadata key `image` must
  never be written: `NVIDIARerank` would send it to the reranker as an image.

## Tool result

`retrieve_evidence(query, source_ids, top_k=8)` returns at most 8 passages (a larger `top_k` returns 8):

- `hits[]`: `rank`, `score` (rerank logit, or the cosine similarity without a reranker), `vector_score`
  (cosine), `source_id`, `document_id`, `chunk_id`, `title`, `url`, `published_at`, `snippet` and `metadata`.
- `candidate_counts`, per source.
- `collection`, the alias (`knowledge`), and `collection_version`, the collection it pointed at during the call.
- `models`: `embed` and `rerank` (`null` when reranking is off).
- `index`: HNSW/COSINE, `M=16`, `efConstruction=200`, `ef=128`.
- `timings`: `embed_ms`, `search_ms`, `rerank_ms` (`0` when reranking is off), `total_ms`.
- Also `query` and `source_ids` (sorted, without repeats).

### Size

A passage is at most 2,400 characters; a longer chunk is cut at a word and ends in an ellipsis. Each hit adds about
1 KB of ids, URL, metadata and JSON indentation.

The MCP SDK sends every result twice: once as `structuredContent` and once as the same JSON in a text block.
Hermes drops `structuredContent` when a text block repeats it, so the model reads only one copy, as a JSON
string, with the receipts plugin's `evidence_id`.

Hermes saves a result longer than 50,000 characters to a file the agent cannot read
([tool result size](../../docs/architecture.md#tool-result-size)). So `budget.py` caps a call at 8 passages and
drops the lowest-ranked ones while the result is longer than 30,000 characters as the agent reads it; a lone
passage that is still too long has its title, metadata and text cut. `tests/test_budget.py` measures the worst
case: passages of text that JSON escapes twice, with long titles, URLs and metadata.

## LangChain configuration choices

Everything uses public API; there are no patches.

1. **`register_model` for `nemotron-3-embed-1b`.** Release 1.4.3 has no table entry for the model. Without
   one, construction runs a blocking `GET /v1/models` lookup.
2. **Explicit `base_url` and `api_key` on every client.** The package's defaults come from `NVIDIA_BASE_URL` and
   `NVIDIA_API_KEY`, which belong to other services in this stack.
3. **Rerank `max_batch_size=200`.** Search never collects more than 200 candidates, so reranking is always one
   request.
4. **Retries around every embed and rerank call** (3 attempts, because an agent is waiting). Dropped connections,
   timeouts, 408, 429 and 5xx are retried, including the async client's `[###] Unknown Error`, which is how it
   reports a non-JSON error body, such as a gateway's 502/503/504 page.
5. **Explicit spans.** LangChain's instrumentation emits nothing for direct embed, search or rerank calls.
6. **Pin `==1.4.3` and set `NVIDIA_USAGE_TELEMETRY_ENABLED=false`.** The next release turns on usage telemetry
   by default, and 1.4.3 is the floor for GHSA-g28h-2cmm-rj9x.

`milvus/embedEtcd.yaml` and `milvus/user.yaml` configure the single-container Milvus: embedded etcd, local
storage, no MinIO. They come from the official `standalone_embed.sh` recipe.

## Run

The image runs as uid 10001 and mounts the knowledge volume read-only at `/knowledge`. Locally, against a
collection ingest wrote into Milvus Lite:

```bash
cd tools/retrieval && uv sync
MILVUS_URI=./milvus.db KNOWLEDGE_DIR=/path/to/knowledge RETRIEVER_API_KEY=nvapi-... uv run demo-retrieval serve
```

## Test

```bash
uv run pytest                                  # offline: Milvus Lite, a fixture catalog, a fake NVIDIA transport
RETRIEVER_API_KEY=nvapi-... uv run pytest -m live   # 16 passages against build.nvidia.com; never in CI
```
