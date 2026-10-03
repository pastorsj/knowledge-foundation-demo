<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Customize

The common changes, and every file each one touches. `agent/tests/test_wiring.py` checks that the files naming a
tool or an endpoint agree, so after any of these run:

```bash
uv run --directory agent pytest        # names whatever you missed
scripts/gen-contracts.sh --check       # after touching the registry, a Pydantic model or a fixture
./scripts/demo.sh test compose         # after touching compose.yaml
./scripts/demo.sh up                   # rebuilds what changed; a new agent or tool image recreates the sandbox
```

## Change the data

Add an industry, or change one, under `data/packs/`: no service changes ([data packs](data-packs.md#adding-an-industry)).
For one-off files, upload them under "Your data" ([ingestion](ingestion.md)).

## Change the models

Edit section 1 of `.env` and run `./scripts/demo.sh restart switchyard`. The sandbox is kept, because Hermes always
asks for the route `knowledge`. The model ids the template uses must all differ, and
`./scripts/demo.sh doctor --keys` checks that each endpoint lists them. The capable model can use its own endpoint
and key (`CAPABLE_BASE_URL`, `CAPABLE_API_KEY`). Which template to use, and how to measure a new model before
adopting it, is in [models and routing](models-and-routing.md).

A new routing template is a new `infra/switchyard/routes/<name>.toml.tmpl` serving `knowledge`, `knowledge-aux`,
`knowledge-fallback` and `knowledge-efficient` (plus `knowledge-capable` if it has a capable model), with a
`# requires:` line naming every setting it substitutes. Templates are baked into the image, so rebuild it and
dry-run every template:

```bash
./scripts/demo.sh test switchyard
```

The embed and rerank models are section 2 of `.env`; changing the embed model re-ingests every pack on the next
sync ([ingestion](ingestion.md#pack-sync)).

## Change the answer policy

`agent/profile/SOUL.md` is in every system prompt: the workflow, how to pick a skill by capability, the evidence
and citation rules, and the answer format. Keep it free of tool details; those belong in skills. Run
`./scripts/demo.sh up` to rebuild the agent image.

## Add or change a skill

Skills live in `agent/profile/skills/<name>/SKILL.md` (`searching-documents`, `querying-tables`,
`predicting-with-kumo`, `querying-auto-ontology`). Each follows the Agent Skills spec, and its index line must stay
within Hermes' 60-character limit. A skill that teaches a tool is tied to that tool's feature in
`agent/render_config.py` (`FEATURES`), so an image without the tool has no skill for it.

```bash
uv run --directory agent agentskills validate profile/skills/querying-tables
uv run --directory agent pytest tests/test_skills.py
```

The skill tests also check that examples cite every table row, qualify SQL tables with the source alias and follow
the PQL grammar they teach.

## Add a tool to an existing MCP server

1. Implement it in the server (`tools/<server>/src/...`) with `read_only_hint=True`, a `source_ids` argument the
   plugin fills, and a result under 30,000 characters ([architecture](architecture.md#tool-result-size)).
2. Add it to `contracts/tool-registry.json`: `id`, `server`, `hermes_name` (`mcp__<server>__<id>`), `family`,
   `label`, `description`, `explorer`, `receipt_kind`, `profile` (the agent feature) and `pills`. Run
   `scripts/gen-contracts.sh`.
3. Add it to the server's `tools.include` in `agent/profile/config.yaml`.
4. Add it to the server's `tools/call` allowlist in `agent/sandbox-policy.yaml`.
5. Teach it in the server's skill.

## Add an MCP server

Everything above, plus the wiring a new server needs. Using the `tables` server as the model:

| File | What to add |
|---|---|
| `tools/<name>/` | A uv project (Python 3.12, its own `uv.lock`), a `Dockerfile` without `EXPOSE`, `demo-<name> serve` on its port with `/mcp` and `/health`; reads `/knowledge` read-only on every call |
| `contracts/tool-registry.json`, `contracts/tool-registry.schema.json` | The tools, and the new `server` in the schema's enum |
| `api/src/demo_api/events/execution.py` | The server's execution graph component in `COMPONENT_BY_SERVER` |
| `agent/profile/config.yaml` | An `mcp_servers.<name>` entry at `http://host.openshell.internal:<port>/mcp` with `tools.include` |
| `agent/render_config.py` | A feature in `FEATURES`: the server and its skill |
| `agent/sandbox-policy.yaml` | A `<name>_mcp` network rule for `host.openshell.internal:<port>`, with the MCP handshake and the `tools/call` allowlist |
| `compose.yaml` | The service, in a profile, with `ports: ["127.0.0.1:<port>:<port>"]`, `knowledge:/knowledge:ro` and the OTLP variables. Pick the port from the block (`83xx`) |
| `scripts/lib/openshell.sh` | The image in `TOOL_IMAGES`, so a new image recreates the sandbox |
| `scripts/demo.sh` | The service in `backend_services` for its profile; the project in `PYTHON_PROJECTS` (unit tests and ruff); a new profile set in `PROFILE_SETS` |
| `scripts/lib/env.sh` | The feature in `agent_features`, derived from the profile |
| `scripts/lib/doctor.sh` | The port in `check_ports`; a new profile in `KNOWN_PROFILES` and any host requirement in `check_host` |
| `.env.example`, `docs/configuration.md` | The profile and any setting |
| `agent/profile/skills/<skill>/SKILL.md` | The skill that teaches it |
| `ui/` | An explorer for its receipts if none fits, and a node in the execution graph |

## Add a tool family or a receipt kind

A new `family` is a capability a source grants: add it to the registry schema, to the catalog contract's
`capabilities` (`contracts/catalog/source-manifest.schema.json`), to ingest (which sources grant it) and to
`SOUL.md`'s skill choice. A new `receipt_kind` is a new variant of the `ReceiptV2` union in
`api/src/demo_api/receipts/models.py`, its fitting rules in the plugin (`agent/profile/plugins/execution_receipts/`),
a fixture in `contracts/fixtures/receipts.json`, and an explorer in the UI. Run `scripts/gen-contracts.sh` and
commit what it writes.

## Change ingestion

The pipeline is `ingest/src/demo_ingest/`: `detect.py` (allowlist and sniffing), `documents.py` (docling, Parse,
chunking), `embed.py`, `index.py` (Milvus), `tables.py` (DuckDB loading and profiling), `packs.py` (pack sync),
`pipeline.py` (stages and workers). A new file kind needs its extension in `detect.py`, a converter or reader, and
the same extension in the UI's upload zone. Keep the catalog contract (`contracts/catalog/`) in step: the API and
every tool read it.

## Change the UI

The UI is the upstream AI-Q UI plus the execution feature; [`ui/README.md`](../ui/README.md) and
[`ui/UPSTREAM.md`](../ui/UPSTREAM.md) say where things live. A new API route the browser needs must also be added
to the proxy's allowlist in `ui/src/app/api/v1/[...path]/route.ts`.
