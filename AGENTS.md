# Guide for coding agents

A Hermes research agent in an OpenShell sandbox, its MCP tools, a FastAPI job API and a
Next.js UI, run together with Docker Compose. See `README.md` for the overview.

## Repository map

- `agent/`: Hermes profile, skills, receipts plugin (`agent/profile/plugins/`), sandbox image and policy.
- `api/`: job API (`demo_api`). Contracts live in `api/src/demo_api/events/` and `receipts/`.
- `ui/`: Next.js UI. `ui/src/generated/` is generated.
- `tools/`: MCP servers (`retrieval`, `market-analytics`, `auto-ontology`), each its own project.
- `infra/`: `openshell/` (version pinned in `infra/openshell/versions.env`) and `switchyard/`.
- `data/`: data packs (`data/packs/<id>/`) and the `demo-data` builder.
- `contracts/`: tool registry, JSON Schemas and golden fixtures shared across languages.
- `scripts/`: `demo.sh` (lifecycle) and `gen-contracts.sh` (codegen).
- `eval/`: on-demand checks of a running deployment (`demo.sh eval`, `demo.sh test gpu --perf`); run by hand.
- `docs/`: guides. `architecture.md` for the design, `customize.md` for the files a new tool or skill touches,
  `development.md` for the tests and checks.

## Commands

- Stack: `scripts/demo.sh <command>`; run it with no arguments for the list.
- Python tests, per project: `uv run --directory <dir> pytest` (dirs: `api`, `agent`, `data`, `data/generate`, `eval`, `tools/*`).
- Lint: `ruff check . && ruff format --check .` (one `ruff.toml` for the repo).
- UI: `npm --prefix ui run lint`, `type-check`, `test:ci`.
- Contracts: `scripts/gen-contracts.sh` to regenerate, `scripts/gen-contracts.sh --check` to verify.
- Everything a commit should pass: `pre-commit run --all-files`.

## Invariants

- Never edit generated files: `contracts/schemas/`, `ui/src/generated/`. Change the Pydantic models
  in `api/` or `contracts/tool-registry*.json`, then run `scripts/gen-contracts.sh`.
- Keep the wiring in sync. A tool is named in `contracts/tool-registry.json`, the agent config,
  the sandbox policy, the provider profiles and `compose.yaml`; `agent/tests/test_wiring.py` checks them.
- Services share JSON contracts only; no service imports another service's Python code.
- One `uv` project and `uv.lock` per service directory; Python 3.12.
- Secrets live only in `.env` (never committed). Never use `NVIDIA_API_KEY` or `NVIDIA_BASE_URL`.
- Host ports bind to 127.0.0.1, except the UI's via `UI_BIND_HOST`. All Docker resources belong to the
  Compose project `market-demo`.
- A cached rebuild must give the same image ID, or `demo.sh up` recreates the container (and, for the
  agent image, the sandbox). So no `EXPOSE`: Docker Engine 28's BuildKit writes a pointer into its
  history line. `demo.sh` also builds without provenance attestations.
- Run the tests for every project you touch before you finish.
