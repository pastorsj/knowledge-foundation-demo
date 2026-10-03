<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Auto Ontology

NVIDIA Auto Ontology answers questions over one structured source with ontology-grounded SQL. Its MCP server
exposes `ask_question`, which Hermes sees as `mcp__auto_ontology__ask_question`. The call returns the answer, the
SQL, the rows and the resolution lineage (which phrase mapped to which ontology object, table and column).

**It is optional.** The `tables` tool (`query_tables`, DuckDB) answers the demo's structured questions; Auto
Ontology adds a second, ontology-grounded path when the `ontology` profile is on. **It is blocked until repository
access:** the upstream repository, `NVIDIA/auto-ontology`, is private, so the submodule needs a GitHub token that is
SSO-authorized for the NVIDIA organization, and there are no public images.

This directory holds only what we add to upstream:

| Path | What |
| --- | --- |
| `prepare.sh` | Exports the pinned commit to `.build/auto-ontology` and applies the patches |
| `patches/backend/` | Two patches to the Auto Ontology backend |
| `patches/mcp/` | One patch to the Auto Ontology MCP server |
| `tests/` | A patch check, skipped without the submodule |

## How it fits

```text
knowledge volume (read-only) ──> /knowledge/sources/<AUTO_ONTOLOGY_SOURCE>/tables.duckdb
auto-ontology-db (pgvector) ──> auto-ontology-migrate ──> auto-ontology (backend, :3001)
                                                            ├──> auto-ontology-ingestion (:3002)
                                                            └──> auto-ontology-mcp (127.0.0.1:3303 → :3003/mcp) <── Hermes
auto-ontology-db ──> auto-ontology-frontend-migrate ──> auto-ontology-frontend (web UI; the API signs in here)
```

- **One structured source.** `AUTO_ONTOLOGY_SOURCE` (default `retail.sales`) names a structured source of the
  knowledge catalog. Its DuckDB file, written by ingest, is the backend's and ingestion's only connection
  (`CONNECTION_STRINGS=duckdb:///knowledge/sources/<source>/tables.duckdb`, the `knowledge` volume mounted
  read-only). The backend waits for ingest to be healthy.
- **No seed.** The ontology is built by upstream's own ingestion from that database; nothing is imported from the
  packs. The market demo's seed (`seed.py`, a hand-written `model.yaml` per pack) is gone.
- The images are upstream's own Dockerfiles (non-root uid 1000, tini), built from `.build/auto-ontology`. Only the
  MCP server is published, on 127.0.0.1:3303. The sandbox reaches it as `host.openshell.internal:3303`.
- The database connection comes from `CONNECTION_STRINGS`, not from the catalog, so upstream's `check_readiness`
  tool reports `can_execute_sql: false` even though SQL runs. Hermes enables only `ask_question`.
- The agent image includes the `auto_ontology` MCP server and the `querying-auto-ontology` skill only when it is
  built with the `ontology` feature, which `demo.sh` sets under the `ontology` profile
  ([agent](../../agent/README.md#features)).
- The API signs in to the web app (`AUTO_ONTOLOGY_URL`, set by `demo.sh`) for the data viewer's ontology view
  (`GET /v1/data_sources/{id}/ontology`).

To serve another source, set `AUTO_ONTOLOGY_SOURCE` in `.env` (any `<pack>.<source>` of kind `structured`, such as
`manufacturing.operations`) and run `./scripts/demo.sh up`. Auto Ontology keeps what it ingested in the
`auto-ontology-db` volume; `./scripts/demo.sh down --volumes` resets it, with the rest of the demo's data.

## Pin

`vendor/auto-ontology` is a submodule of `https://github.com/NVIDIA/auto-ontology.git` with `update = none`. A plain
`git clone --recurse-submodules` prints "Skipping submodule" and needs no credentials. The pin is the commit the
superproject records: `d0ebb96f` ("Sync release/v1.0 with main (#304)", the `release/v1.0` head on 2026-09-29).
There, the backend and the MCP server are both version 1.0.0.

## Patches

Each patch applies with `git apply` in order: first `backend/`, then `mcp/`.

| Patch | Why | Remove when |
| --- | --- | --- |
| `backend/0001` Hide configured schemas from semantic search | `SEMANTIC_SEARCH_EXCLUDED_SCHEMAS=<database>.<schema>` keeps a schema's relations from answering ordinary questions. The market demo excluded its Kumo views; the knowledge sources have none, so nothing is excluded today. Records with no schema identity are kept (related: #254) | Upstream can exclude schemas from search |
| `backend/0002` Report ontology resolution lineage | Adds `resolution_lineage` to chat results, for the UI's ontology explorer (#251, #256) | Upstream reports typed lineage |
| `mcp/0001` Trusted service mode and lineage | Upstream MCP accepts only per-caller OAuth, so an unattended agent cannot call it. `AUTO_ONTOLOGY_MCP_TRUSTED_SERVICE_MODE=true` calls the backend with no caller identity. `ask_question` also returns `resolution_lineage` | Upstream documents a service-to-service mode and returns lineage |

Trusted service mode is safe here only because nothing untrusted can reach the server. It listens on loopback and
the Compose network, and the backend is never published. Do not enable it anywhere else.

## Env contract

Auto Ontology reads four model settings. Every field is set explicitly in `compose.yaml`. If any field is unset,
upstream falls back to built-in defaults chosen by the key prefix, which may be models the endpoint does not serve,
and to `NVIDIA_API_KEY`, which this repository never uses.

| Upstream setting | Set from |
| --- | --- |
| `REASONING_ENDPOINT` / `_API_KEY` / `_MODEL` | `INFERENCE_BASE_URL`, `INFERENCE_API_KEY`, `AUTO_ONTOLOGY_REASONING_MODEL` |
| `NON_REASONING_ENDPOINT` / `_API_KEY` / `_MODEL` | `INFERENCE_BASE_URL`, `INFERENCE_API_KEY`, `AUTO_ONTOLOGY_NON_REASONING_MODEL` |
| `EMBED_ENDPOINT` / `_API_KEY` / `_MODEL` | `RETRIEVER_BASE_URL`, `RETRIEVER_API_KEY` (empty: `INFERENCE_API_KEY`), `RETRIEVER_EMBED_MODEL` |
| `RERANK_ENDPOINT` / `_API_KEY` / `_MODEL` | `RETRIEVER_RERANK_URL` (default: the hosted NIM URL for the default model), `RETRIEVER_API_KEY` (empty: `INFERENCE_API_KEY`), `RETRIEVER_RERANK_MODEL` |

The `.env.example` defaults, for build.nvidia.com: `AUTO_ONTOLOGY_REASONING_MODEL=nvidia/nemotron-3-super-120b-a12b`
and `AUTO_ONTOLOGY_NON_REASONING_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b`. On another OpenAI-compatible endpoint,
use the ids it lists for the same models; `./scripts/demo.sh doctor --keys` checks them. Reasoning runs on the
inference endpoint directly, not through Switchyard. Lightning on build.nvidia.com can be slow (about 80 s in one
sample); upstream's `LLM_INVOKE_TIMEOUT_S` defaults to 120.

Other settings:

| Variable | Service | Value |
| --- | --- | --- |
| `CONNECTION_STRINGS` | backend, ingestion | `duckdb:///knowledge/sources/${AUTO_ONTOLOGY_SOURCE:-retail.sales}/tables.duckdb` |
| `POSTGRES_*`, `INGESTION_SERVICE_URL` | backend, ingestion, migrate, frontend | the private `auto-ontology-db` and ingestion service |
| `AUTO_ONTOLOGY_ADMIN_EMAIL`, `AUTO_ONTOLOGY_ADMIN_PASSWORD`, `AUTH_SECRET`, `APP_URL` | frontend | web UI sign-in; `./scripts/demo.sh init` generates the secrets |
| `AUTO_ONTOLOGY_API_URL`, `AUTO_ONTOLOGY_MCP_TRUSTED_SERVICE_MODE=true`, `AUTO_ONTOLOGY_MCP_CHAT_TIMEOUT_S=900` | MCP | the backend at `http://auto-ontology:3001` |

## Run

With access to `NVIDIA/auto-ontology` (a `gh` token SSO-authorized for the NVIDIA organization):

```bash
git submodule update --init --checkout vendor/auto-ontology   # update = none: must be explicit
tools/auto-ontology/prepare.sh                                # writes .build/auto-ontology (up also runs it)
```

Then add `ontology` to `COMPOSE_PROFILES` in `.env` and run `./scripts/demo.sh up`. `demo.sh` builds the agent image
with the `ontology` feature, so Hermes gets the `auto_ontology` server. Without access, `doctor` refuses the
profile and names the submodule command. The images are built locally from the submodule; do not publish them.

## Move the pin

```bash
git -C vendor/auto-ontology fetch origin release/v1.0
git -C vendor/auto-ontology checkout --detach <commit>
git add vendor/auto-ontology
tools/auto-ontology/prepare.sh --check
```

If a patch no longer applies, reapply it by hand on an export of the new commit, regenerate it with `git diff`, and
keep its header. Delete a patch once upstream ships the same behavior.

## Test

```bash
uv run --directory tools/auto-ontology pytest
```

The tests need no network, keys or Docker. `test_patches.py` runs `prepare.sh --check` and is skipped when the
submodule is not checked out. To run the upstream test suites against the patched tree, use
`uv run pytest auto_ontology/retrieval` in `.build/auto-ontology`, and `uv run pytest` in
`.build/auto-ontology/mcp`.
