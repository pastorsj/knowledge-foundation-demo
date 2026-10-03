<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Development

How the repository is laid out, how to test each part, and the rules a change keeps.

## Project structure

| Path | What | Tests |
|---|---|---|
| `agent/` | Hermes profile (`profile/`: config, `SOUL.md`, skills, the `execution-receipts` plugin), patches, sandbox image and policy, `render_config.py` | `uv run --directory agent pytest` |
| `api/` | The job API (`demo_api`): catalog, jobs, Hermes runs, events, receipts, reports, documents forwarding, data viewer, recordings | `uv run --directory api pytest` |
| `ingest/` | The ingestion service (`demo_ingest`): detection, docling and Parse, chunking, embedding, Milvus, DuckDB, pack sync | `uv run --directory ingest pytest` |
| `tools/retrieval/` | `retrieve_evidence` MCP server, plus the Milvus configuration | `uv run --directory tools/retrieval pytest` |
| `tools/tables/` | `query_tables` MCP server (sqlglot guard, locked DuckDB worker) | `uv run --directory tools/tables pytest` |
| `tools/prediction/` | `predict` MCP server (`kumo-relational-client`) | `uv run --directory tools/prediction pytest` |
| `tools/auto-ontology/` | Auto Ontology patches and `prepare.sh` (optional profile) | `uv run --directory tools/auto-ontology pytest` |
| `eval/` | The on-demand answer-quality eval (`demo-eval`) | `uv run --directory eval pytest` |
| `ui/` | The Next.js UI | `npm --prefix ui run lint`, `type-check`, `test:ci`, `e2e` |
| `contracts/` | Tool registry, catalog manifests, generated schemas, golden fixtures | `scripts/gen-contracts.sh --check` |
| `data/` | Industry packs (`data/packs/<id>/`) and their schemas (`data/schemas/`) | `./scripts/demo.sh data validate` |
| `infra/` | `openshell/`, `switchyard/`, `parse/`, `phoenix/` | agent tests, `./scripts/demo.sh test switchyard` |
| `scripts/` | `demo.sh` and `lib/*.sh` (lifecycle), `gen-contracts.sh` (codegen), `validate_packs.py` | `./scripts/demo.sh test compose`, shellcheck |
| `docs/` | These guides | – |

Each Python project is its own `uv` project with its own `uv.lock` (Python 3.12); there is no root workspace. One
`ruff.toml` covers the repository.

## Tests

```bash
./scripts/demo.sh test              # unit (every Python project and ruff), ui, contracts, compose
./scripts/demo.sh test e2e          # UI build and the Playwright smoke suite against a fake API
./scripts/demo.sh test switchyard   # build Switchyard and dry-run every route template
./scripts/demo.sh test all          # all of the above
./scripts/demo.sh data validate     # every pack against data/schemas
pre-commit run --all-files          # ruff, shellcheck, JSON/YAML/TOML checks, gitleaks and the guards
```

`test unit` runs `uv run --locked pytest -q -m "not gpu and not slow and not live"` in each of `agent`, `api`,
`eval`, `ingest`, `tools/retrieval`, `tools/tables`, `tools/prediction` and `tools/auto-ontology`, then
`ruff check` and `ruff format --check` over them. Tests run offline with no keys:

| Project | What the offline suite uses |
|---|---|
| `ingest` | A fake OpenAI-compatible server replaying recorded Nemotron Parse 2.0 output (`tests/fixtures/parse/`), a fake embedder, Milvus Lite, fixture tables and a mini pack |
| `tools/retrieval`, `tools/tables`, `tools/prediction` | The shared catalog fixture (`contracts/fixtures/catalog/`), with `tables.duckdb` built from its CSVs; a fake embedder and Milvus Lite; a stubbed Kumo client (one real request to a closed port checks the client's call shape) |
| `api` | The catalog fixture, a fake Hermes, a fake ingest for the forwarded documents routes, golden contract fixtures |
| `agent` | The skills against the Agent Skills spec, the config rendering, the OpenShell pins, the plugin's receipts against the schema, and the wiring |

Tests marked `live` call real endpoints (for example `KUMO_RELATIONAL_URL=... uv run --directory tools/prediction
pytest -m live`), and those marked `gpu` or `slow` need a GPU or minutes; none runs by default.

### UI and Playwright

```bash
npm --prefix ui ci
npm --prefix ui run lint && npm --prefix ui run type-check && npm --prefix ui run test:ci   # vitest
npm --prefix ui run build && npm --prefix ui run e2e      # Playwright smoke (ui/e2e/), fake API (ui/e2e/fake-api.mjs)
npm --prefix ui run e2e:visual                            # visual baselines, in Docker (ui/e2e/visual/README.md)
```

The smoke suite (`ui/e2e/*.spec.ts`) drives the built UI against a fake API: the industry selector, uploads
through the pipeline stages, the execution view, and the replay of every recorded session of every pack. On
Linux, `./scripts/demo.sh test e2e` installs Chromium with its system libraries (through sudo).

### Contracts

```bash
scripts/gen-contracts.sh           # after changing an API model, the registry or a fixture
scripts/gen-contracts.sh --check   # change nothing; fail if anything is out of date
```

It exports the API's Pydantic models to `contracts/schemas/`, validates and canonicalizes `contracts/fixtures/`, and
generates TypeScript into `ui/src/generated/`. The catalog manifests (`contracts/catalog/*.schema.json`) are
hand-written; ingest validates every manifest it writes against them, and the tools' and API's tests read the
fixtures under `contracts/fixtures/catalog/`. [`contracts/README.md`](../contracts/README.md) has the details.

### On demand

Two checks of a running deployment are run by hand only ([operations](operations.md#on-demand-checks)):

| Command | Checks |
|---|---|
| `./scripts/demo.sh test live --url URL` | Each industry's featured questions asked live through the UI, and the upload-then-ask flow on "Your data" (`ui/e2e-live/`, `ui/playwright.live.config.ts`) |
| `./scripts/demo.sh eval [--pack P]` | Answer quality ([eval](../eval/README.md)) |

## Checks

There is no hosted CI. Before you push, run `pre-commit run --all-files` and `./scripts/demo.sh test all`, plus the
tests of every project you touched. The pre-commit guards refuse data files outside the allowed fixtures, a
Makefile and Git LFS.

## When you change the code

- Change the Pydantic models in `api/` or `contracts/tool-registry*.json`, then run `scripts/gen-contracts.sh`.
  Never edit `contracts/schemas/` or `ui/src/generated/`.
- Keep the wiring in sync: `agent/tests/test_wiring.py` fails when the registry, the agent config, the sandbox
  policy, the provider profiles, `compose.yaml` and `TOOL_IMAGES` disagree about a tool or an endpoint.
- Services share JSON contracts only; never import another service's code. Only ingest writes `/knowledge`.
- Publish ports on `127.0.0.1` only (the UI's alone follows `UI_BIND_HOST`), from the demo's block, and keep every
  Docker resource in the `knowledge-foundation` project.
- Change a pack through its generator, then `./scripts/demo.sh data validate`.
- New files carry the SPDX header (`Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES`, `Apache-2.0`).

What a new tool, server, skill or model touches is in [customize](customize.md).
