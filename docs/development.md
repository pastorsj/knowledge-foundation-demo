<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Development

Working on the code: where things live, the tests, CI and the rules a change follows. [`AGENTS.md`](../AGENTS.md)
is the same guide for coding agents.

## Project structure

```text
agent/                   Hermes profile, skills, receipts plugin, patches, sandbox policy and image
api/                     Job API (FastAPI): jobs, execution.v2 events, receipts, reports, recordings
ui/                      Next.js UI based on the upstream AI-Q UI; src/features/execution is the execution view
tools/retrieval/         retrieve_evidence: LangChain NVIDIA embed and rerank over Milvus
tools/market-analytics/  seven market tools and Kumo prediction, on CPU or RAPIDS
tools/auto-ontology/     patches and seed for Auto Ontology (the ontology profile)
infra/openshell/         OpenShell pins, gateway config, provider profiles, CLI image
infra/switchyard/        Switchyard image, route templates, judge prompt
infra/phoenix/           Phoenix launcher
data/                    data packs and the demo-data builder; packs/<id>/recordings is the replay bundle
eval/                    on-demand checks of a running deployment: the answer-quality eval and the GPU guard
contracts/               tool registry, JSON Schemas and golden fixtures
scripts/                 demo.sh lifecycle and gen-contracts.sh codegen
docs/                    the guides
vendor/                  the Auto Ontology submodule (not checked out by default; private until it is published)
```

## Tests

Each service is its own project: one `uv` project and lock per Python service (Python 3.12), and `npm` for the
UI.

```bash
./scripts/demo.sh test              # unit (every Python project, ruff, skills lint), ui, contracts, compose
./scripts/demo.sh test e2e          # UI build and Playwright: live smoke and replay of every recorded session
./scripts/demo.sh test switchyard   # build Switchyard and dry-run every route template
uv run --directory api pytest       # one project: also agent, data, data/generate, eval and tools/*
npm --prefix ui run lint            # also type-check, test:ci, build and e2e
npm --prefix ui run e2e:visual      # the UI's visual baselines, in Docker (ui/e2e/visual/README.md)
scripts/gen-contracts.sh --check    # the generated schemas and TypeScript are up to date
pre-commit run --all-files          # ruff, shellcheck, JSON/YAML/TOML checks, gitleaks
```

Tests run offline with no keys. Tests marked `live` call real endpoints and those marked `gpu` need a GPU, so
neither runs by default. Three checks of a running deployment are on demand only, run by hand and never by CI
([operations](operations.md#on-demand-checks)):

| Command | Checks |
|---|---|
| `./scripts/demo.sh test live --url URL` | Each featured question asked live through the deployment's UI: success, a resolved citation, tool pills against the tools called, the replay, the closing events, a latency budget; also its health and pack |
| `./scripts/demo.sh eval [--pack P] [--runs N] [--questions ID,...]` | Answer quality against the pack's oracles; an LLM grader (a frontier model) when `GRADER_BASE_URL`, `GRADER_API_KEY` and `GRADER_MODEL` are set ([eval](../eval/README.md)) |
| `./scripts/demo.sh test gpu [--perf]` | On an NVIDIA GPU host, the CPU/GPU parity tests; `--perf` also the running stack's GPU speedups against floors set from the A100 recordings |

## Checks

There is no hosted CI. Before you push, run:

| Command | Checks |
|---|---|
| `pre-commit run --all-files` | ruff, shellcheck, gitleaks, JSON/TOML/YAML syntax, and the guards: no market data files, no Makefile, no Git LFS |
| `./scripts/demo.sh test all` | Every Python project's tests and ruff, the UI's lint, type-check, unit tests, build and Playwright suite, the contracts check, the Compose config of every profile set, and the Switchyard dry-runs |
| `cd ui && npm run e2e:visual` | The visual baselines, in the Playwright Docker image ([e2e/visual](../ui/e2e/visual/README.md)) |
| `cd data && uv run --locked pytest -m slow` | The end-to-end `synthetic-market` build |

## When you change the code

- Change the Pydantic models in `api/` or `contracts/tool-registry*.json`, then run
  `scripts/gen-contracts.sh`. Never edit `contracts/schemas/` or `ui/src/generated/`.
- Keep the wiring in sync: `agent/tests/test_wiring.py` fails when the registry, the agent config, the sandbox
  policy, the provider profiles and `compose.yaml` disagree about a tool or an endpoint.
- Services share JSON contracts only; never import another service's code.
- Publish ports on `127.0.0.1` only (the UI's alone follows `UI_BIND_HOST`), and keep every Docker resource in
  the `market-demo` project.
- New files carry the SPDX header (`Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES`, `Apache-2.0`).
- Run the tests of every project you touch.

What a new tool, server, skill or model touches is in [customize](customize.md).
