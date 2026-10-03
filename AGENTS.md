<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Guide for coding agents

NVIDIA Knowledge Foundation: a Hermes agent in an OpenShell sandbox answers questions over enterprise knowledge
(industry packs and uploaded files) with cited evidence, through MCP tools, a FastAPI job API, an ingestion
service and a Next.js UI, run together with Docker Compose. See `README.md` for the overview and
`docs/architecture.md` for the design.

## Repository map

- `agent/`: Hermes profile (`profile/`: config, `SOUL.md`, skills, the receipts plugin), patches, sandbox image
  and policy (`sandbox-policy.yaml`), `render_config.py` (features).
- `api/`: job API (`demo_api`): catalog, jobs, events, receipts, reports, documents forwarding, data viewer,
  recordings. Contracts live in `api/src/demo_api/events/` and `receipts/`.
- `ingest/`: ingestion service (`demo_ingest`), the only writer of `/knowledge`: docling with Nemotron Parse,
  chunking, embedding, Milvus, DuckDB loading and profiling, pack sync.
- `tools/`: MCP servers, each its own project: `retrieval` (`retrieve_evidence`), `tables` (`query_tables`,
  DuckDB), `prediction` (`predict`, Kumo), `auto-ontology` (patches and `prepare.sh` for the optional
  `ask_question`).
- `ui/`: Next.js UI. `ui/src/generated/` is generated.
- `data/`: industry packs (`data/packs/<id>/`: `pack.yaml`, `questions.yaml`, `files/`, `generator/`,
  `recordings/`) and their schemas (`data/schemas/`).
- `contracts/`: tool registry, catalog manifests (`contracts/catalog/`), generated JSON Schemas and golden fixtures.
- `infra/`: `openshell/` (release pinned in `infra/openshell/versions.env`), `switchyard/`, `parse/` (the vLLM
  entrypoint for Nemotron Parse), `phoenix/`.
- `scripts/`: `demo.sh` and `lib/*.sh` (lifecycle), `gen-contracts.sh` (codegen), `validate_packs.py` (packs).
- `eval/`: on-demand answer-quality eval of a running deployment (`demo.sh eval`); run by hand.
- `docs/`: guides. `architecture.md` for the design, `ingestion.md` for the pipeline, `customize.md` for the files
  a new tool or skill touches, `development.md` for the tests and checks.

## Commands

- Stack: `./scripts/demo.sh <command>`; `./scripts/demo.sh --help` lists them.
- Python tests, per project: `uv run --directory <dir> pytest` (dirs: `agent`, `api`, `ingest`, `eval`,
  `tools/retrieval`, `tools/tables`, `tools/prediction`, `tools/auto-ontology`).
- All unit tests and ruff: `./scripts/demo.sh test unit`; everything offline: `./scripts/demo.sh test all`.
- Lint: `ruff check . && ruff format --check .` (one `ruff.toml` for the repo).
- UI: `npm --prefix ui run lint`, `type-check`, `test:ci`; Playwright smoke: `./scripts/demo.sh test e2e`.
- Contracts: `scripts/gen-contracts.sh` to regenerate, `scripts/gen-contracts.sh --check` to verify.
- Packs: `./scripts/demo.sh data validate` (runs `scripts/validate_packs.py`); `./scripts/demo.sh data generate PACK`.
- Compose: `./scripts/demo.sh test compose` renders every profile set and checks every port is on loopback.
- Everything a commit should pass: `pre-commit run --all-files`.

## Invariants

- Never edit generated files: `contracts/schemas/`, `ui/src/generated/`. Change the Pydantic models in `api/` or
  `contracts/tool-registry*.json`, then run `scripts/gen-contracts.sh`. The catalog schemas in `contracts/catalog/`
  are hand-written.
- Only `ingest` writes the knowledge volume; the API and the tools mount it read-only and read the manifests on
  every call. Tool schemas never depend on data.
- Keep the wiring in sync. A tool is named in `contracts/tool-registry.json`, the agent config, the sandbox policy,
  `compose.yaml`, `TOOL_IMAGES` in `scripts/lib/openshell.sh` and `COMPONENT_BY_SERVER` in the API;
  `agent/tests/test_wiring.py` checks them. `docs/customize.md` lists every file.
- Services share JSON contracts only; no service imports another service's Python code.
- One `uv` project and `uv.lock` per service directory; Python 3.12.
- Secrets live only in `.env` (never committed). Never use `NVIDIA_API_KEY` or `NVIDIA_BASE_URL`.
- Host ports bind to 127.0.0.1, except the UI's via `UI_BIND_HOST`, and stay in the demo's block (3300, 4300,
  6306, 83xx, 1838x, 3303). All Docker resources belong to the Compose project `knowledge-foundation`.
- Pack files are written by the pack's seeded generator, never by hand; everything in a pack is synthetic.
- A cached rebuild must give the same image ID, or `demo.sh up` recreates the container (and, for the agent and
  tool images, the sandbox). So no `EXPOSE`: Docker Engine 28's BuildKit writes a pointer into its history line.
  `demo.sh` also builds without provenance attestations.
- Run the tests for every project you touch before you finish.
