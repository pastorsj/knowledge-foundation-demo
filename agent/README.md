# Agent

The research assistant of NVIDIA Knowledge Foundation is
[Hermes Agent](https://github.com/NousResearch/hermes-agent) 0.21.5 (image
`nousresearch/hermes-agent:v2026.9.24`), packaged as the image that OpenShell
runs in its sandbox (`knowledge-foundation/hermes-sandbox:local`). It answers
questions about enterprise knowledge (documents, tables and predictions) with
cited evidence. The job API drives it through the Hermes Runs API; see
`infra/openshell/` for how the sandbox is created and reached.

## Where things live

| Concern | File | Notes |
|---|---|---|
| Model route, tools, API server | `profile/config.yaml` | One config with every optional tool; `render_config.py` narrows it per image. |
| Always-on answer policy | `profile/SOUL.md` | In every system prompt: workflow, skill choice by capability, evidence, citations, answer format. |
| How to use each tool | `profile/skills/*/SKILL.md` | Loaded on demand; baked read-only at `/opt/agent/skills`. |
| Receipts and source scope | `profile/plugins/` | The `execution-receipts` Hermes plugin, baked into `/opt/data/plugins`. |
| Tracing | `profile/relay-plugins.toml` | NeMo Relay sends OpenInference traces to Phoenix (project `knowledge-foundation`). `enable_full_payloads` is one line to flip. |
| Network and filesystem limits | `sandbox-policy.yaml` | Baked at `/etc/openshell/policy.yaml`. Switchyard and the receipt API are allowed by the provider profiles in `infra/openshell/providers/`. |
| Changes to Hermes itself | `patches/` | Four Runs API patches and two MCP fixes; see `patches/README.md`. |
| Profile manifest | `profile/distribution.yaml` | Hermes profile distribution metadata (`knowledge-foundation-agent`). |

Hermes reaches everything as `host.openshell.internal:<port>`: Switchyard
`:4300` (`/v1`, routes `knowledge*`), the job API `:8300`, the retrieval MCP
server `:8320`, the tables MCP server `:8321`, the prediction MCP server
`:8322`, the Auto Ontology MCP server `:3303` and Phoenix `:6306`.

## Tools and skills

| Feature | MCP server (Hermes toolset) | Tool | Family | Skill |
|---|---|---|---|---|
| `retrieval` | `retrieval` | `retrieve_evidence` | `unstructured_retrieval` | `searching-documents` |
| `tables` | `tables` | `query_tables` | `structured_retrieval` | `querying-tables` |
| `kumo` | `prediction` | `predict` | `structured_prediction` | `predicting-with-kumo` |
| `ontology` | `auto_ontology` | `ask_question` | `structured_retrieval` | `querying-auto-ontology` |

`SOUL.md` picks a skill by the capability (`family`) a work item needs. The
model never chooses the data scope: the plugin sets each tool's `source_ids`
to the selected sources that allow its family.

## Features

`AGENT_FEATURES` (build argument, default `retrieval,tables`) picks the
optional tools baked into an image: `retrieval`, `tables`, `kumo` and
`ontology`. A feature that is off has no toolset, MCP server, tool or visible
skill. Change it by rebuilding the image and recreating the sandbox.

`scripts/lib/env.sh` (`agent_features`) derives it from `COMPOSE_PROFILES`:
`retrieval,tables` always (the `core` profile), `kumo` with the `kumo` profile
or with the `prediction` profile and a remote `KUMO_RELATIONAL_URL`, and
`ontology` with the `ontology` profile. A name outside the four fails the build
as an unknown feature.

## Run contract

The job API starts each job with `POST /v1/runs` and the model name
`enterprise-research`. `SOUL.md` holds every static rule, so a run carries only
what changes per job:

- `instructions`: the selected source IDs, plus the selected-source catalog as
  JSON. Each source's `capabilities` are `family` values from
  `contracts/tool-registry.json` (`unstructured_retrieval`,
  `structured_retrieval`, `structured_prediction`); `SOUL.md` picks skills by
  those names. A structured source also carries its DuckDB `alias`, tables,
  columns, keys and prediction templates, so the agent writes SQL and PQL
  without a schema tool. Do not name an `answering-with-evidence` skill:
  `SOUL.md` replaced it, and the image has no such skill.
- `enabled_toolsets` (patch 0002): always `skills`, plus the registry `server`
  of every tool whose family is selected and whose feature the image has. For
  example, `unstructured_retrieval,structured_prediction` gives
  `["skills", "retrieval", "prediction"]`, and `structured_retrieval` gives
  `tables` (and `auto_ontology` with the `ontology` feature). There is no
  `skills_readonly` toolset. Hermes rejects any toolset the image's config
  does not list.

## Build

The build context is `agent/`; the tool registry comes from the named context
`contracts`:

```bash
docker build --build-context contracts=contracts \
  --build-arg AGENT_FEATURES=retrieval,tables,kumo \
  -t knowledge-foundation/hermes-sandbox:local agent
```

The build applies the patches and renders the config. OpenShell ignores the
image's entrypoint, so Hermes' s6 init never runs; the build does its work
instead: it seeds `HERMES_HOME=/opt/data` as the `hermes` user (uid 10000) with
only the essential bundled skill, and runs Hermes' config migration. The build
fails if the policy names any interpreter other than Hermes' own
(`/usr/bin/python3.13`), if Hermes would block `SOUL.md`, or if `skill_manage`
writes would apply instead of being staged.

Profile files are root-owned and read-only to the `hermes` user. `/sandbox` is
the workspace, because `/opt/data` is an image volume.

## Runtime environment

| Variable | Source |
|---|---|
| `API_SERVER_KEY` | `openshell sandbox create --env "API_SERVER_KEY=$HERMES_API_SERVER_KEY"`, from `.env`. At least 16 characters, or Hermes' API server refuses to start. The job API sends it as a bearer token. |
| `HERMES_RECEIPT_API_KEY` | The `receipts` provider: an OpenShell placeholder that the supervisor swaps for the real key on the three `/internal/hermes` routes only. The plugin sends it as `X-Receipt-Key`. |
| `HERMES_RECEIPT_API_URL` | Image: `http://host.openshell.internal:8300` |
| `SWITCHYARD_CLIENT_API_KEY` | Image: `not-a-secret`. Switchyard holds the model key. |
| `HERMES_STREAM_READ_TIMEOUT`, `HERMES_STREAM_STALE_TIMEOUT` | Image: 900 and 600 s, because the escalation router buffers replies. |
| `HERMES_NEMO_RELAY_PLUGINS_TOML` | Image: `/opt/data/relay-plugins.toml` |

No model key or `NVIDIA_*`/`OPENAI_*` variable ever reaches the sandbox.

## Test

```bash
uv run --directory agent pytest
uv run --directory agent agentskills validate profile/skills/querying-tables   # one skill, by hand
```

The tests run offline. They check the skills against the Agent Skills spec and
Hermes' 60-character index limit (and that their examples cite every table
row, qualify SQL tables with the source alias and follow the PQL grammar they
teach), the config and its rendering, the OpenShell pins, the plugin's
receipts against `contracts/schemas/receipt.schema.json`, and the wiring: every
URL the agent calls is allowed by the policy or a provider profile, the MCP
tool allowlists match the config and `contracts/tool-registry.json`, Switchyard
serves every configured model, `compose.yaml` publishes every port on
`127.0.0.1`, every MCP server's image is in `TOOL_IMAGES`, and the job API asks
for the Runs API's model name.

## Adding a tool

Add it to the MCP server, then to `contracts/tool-registry.json`, the server's
`tools.include` in `profile/config.yaml`, the server's `tools/call` allowlist in
`sandbox-policy.yaml`, and the skill that teaches it. A new server also needs a
feature in `render_config.py`, a loopback port in `compose.yaml`, and its image
in `TOOL_IMAGES` (`scripts/lib/openshell.sh`). The tests name whatever else you
missed; [customize.md](../docs/customize.md#add-an-mcp-server) lists every file.
