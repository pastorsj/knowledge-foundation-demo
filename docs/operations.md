<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Operations

Everything after `init`: running the stack, the two host modes (a DGX Spark, a Brev x86_64 VM), traces, jobs,
recordings, the on-demand checks and troubleshooting. `./scripts/demo.sh --help` lists every command.

## Lifecycle

| Command | What it does |
|---|---|
| `./scripts/demo.sh init` | Creates `.env` from `.env.example` (mode 600) and fills its generated secrets |
| `./scripts/demo.sh doctor [--keys]` | Checks the host, the ports, `.env` and the profiles; `--keys` asks each endpoint for its model list, and the remote Kumo NIM whether it is ready |
| `./scripts/demo.sh up [--no-build]` | Builds, starts Milvus, Parse, ingest, the tools, Switchyard, Phoenix and the API, then the OpenShell sandbox and the Hermes forwarder, then the UI. The industry packs ingest in the background |
| `./scripts/demo.sh status` | Services, the sandbox and its endpoints, Switchyard's routes, profiles and agent features, each pack's ingestion, the URLs |
| `./scripts/demo.sh check` | Proves the sandbox boundary on the running stack ([OpenShell](openshell.md#proving-the-boundary)) |
| `./scripts/demo.sh logs [agent\|routing\|SERVICE...] [-f]` | `agent`: Hermes in the sandbox; `routing`: Switchyard's routing decisions; else Compose logs |
| `./scripts/demo.sh restart SERVICE` | `agent`: recreate the sandbox; `switchyard`: apply `.env` sections 1 and 2 to Switchyard, the API, ingest, retrieval and Auto Ontology; anything else: `docker compose restart` |
| `./scripts/demo.sh data sync` / `data status` | Ingest every pack whose files changed; each pack's progress |
| `./scripts/demo.sh down [--volumes] [--prune]` | Deletes the sandbox, then stops everything. `--volumes` also deletes the knowledge catalog, uploads, jobs and traces; `--prune` also removes this project's untagged images and Docker's unused build cache (host-wide) |

The first `up` builds every image and downloads Nemotron Parse 2.0 into the `parse-cache` volume (`up` waits up to
an hour). A repeat `up` with nothing changed recreates nothing and keeps the sandbox. While the packs ingest,
`status` shows each one's progress:

```text
industry packs:
  financial-services   ready      22/22 files
  healthcare           ingesting  9/20 files
```

A pack's questions are offered once its sources are `ready` or `ingesting`. `down --volumes` empties the catalog;
the next `up` ingests every pack again.

## DGX Spark mode

The default profiles, `core,parse,prediction`, run the whole demo on a DGX Spark (GB10, arm64) except the Kumo
NIM. Nemotron Parse runs on the GPU in vLLM with a small share of the unified memory
(`PARSE_GPU_MEMORY_UTILIZATION=0.15`); the agent, embedding and rerank models are hosted, and predictions go to a
remote Kumo Relational NIM ([below](#kumo-through-a-remote-nim)).

```bash
./scripts/demo.sh init
"${EDITOR:-vi}" .env              # INFERENCE_API_KEY, KUMO_RELATIONAL_URL, KUMO_API_KEY
./scripts/demo.sh doctor --keys
./scripts/demo.sh up
./scripts/demo.sh check
```

The Spark is a shared host: the demo's ports are a block of their own (3300, 4300, 6306, 83xx, 1838x), chosen to
avoid other demos' (port 6006 is held by another one). `doctor` refuses to start if one is taken
([troubleshooting](#port-collisions)).

### Kumo through a remote NIM

The Kumo Relational NIM ships for amd64 only, so `doctor` refuses the `kumo` profile on the Spark. The
`prediction` profile runs only the prediction server on the Spark and sends every prediction to a remote Kumo
Relational NIM on an x86_64 GPU host. Both settings in `.env` section 3 are required:

| Variable | What |
|---|---|
| `KUMO_RELATIONAL_URL` | The NIM's base URL, the one that serves `/v1/health/ready`. Use `https://`: the Kumo client refuses to send the key over plain `http://` to anything but `localhost`, `127.0.0.1` or `::1` |
| `KUMO_API_KEY` | The key the endpoint expects, sent as `X-API-Key` |

The NIM itself serves plain HTTP and checks no key, and the `prediction` container cannot reach a tunnel on the
host: `127.0.0.1` inside it is the container. Two shapes work:

- **An `https://` endpoint that checks `X-API-Key`**, such as a TLS reverse proxy in front of the NIM on the
  x86_64 host. Set `KUMO_RELATIONAL_URL` to it and `KUMO_API_KEY` to its key.
- **A tunnel that ends inside the `prediction` container's network namespace**: a service in a Compose override
  file of your own, kept outside the repository, with `network_mode: service:prediction`, that forwards a local
  port to the NIM. Set `KUMO_RELATIONAL_URL=http://127.0.0.1:<port>` (loopback, so the client sends the key) and
  `KUMO_API_KEY` to any value the far end accepts (`doctor` requires one). The tunnel shares the container's
  namespace, so restart it whenever `up` recreates `prediction`.

Without either, run `COMPOSE_PROFILES=core,parse` (no predictions).

`doctor` reports a problem while either is empty or the URL is plain `http://` to another host, and
`doctor --keys` asks `GET $KUMO_RELATIONAL_URL/v1/health/ready` with the key. With a `KUMO_RELATIONAL_URL`, the
agent image gains the `kumo` feature, so the first `up` with it rebuilds the image and recreates the sandbox once.

If the NIM cannot be reached, `predict` answers `available: false` with the reason, and the answer says so. Each
prediction is one attempt within 60 s, so a cold or distant NIM shows up as unavailable rather than as a stalled
job. To run without predictions, drop `prediction` from `COMPOSE_PROFILES`.

### Auto Ontology on the Spark

The `ontology` profile runs on the Spark: upstream's images build for arm64 unchanged
([Auto Ontology](../tools/auto-ontology/README.md#arm64)). With access to the private submodule:

```bash
git submodule update --init --checkout vendor/auto-ontology
"${EDITOR:-vi}" .env              # COMPOSE_PROFILES=core,parse,prediction,ontology; AUTO_ONTOLOGY_SOURCE
./scripts/demo.sh up
```

`up` builds the three images, rebuilds the agent image with the `ontology` feature and recreates the sandbox once.
Its models use `INFERENCE_*` (reasoning and non-reasoning, by the ids in `AUTO_ONTOLOGY_*_MODEL`) and
`RETRIEVER_*` (embed and rerank). The ingestion service then builds the ontology of `AUTO_ONTOLOGY_SOURCE` in the
background, in about 10 minutes for `retail.sales`; until then `ask_question` answers that the semantic layer has
not been created ([how](../tools/auto-ontology/README.md#how-the-ontology-is-built)). If `up` recreated
`prediction` and Kumo goes through a tunnel in its network namespace ([the second shape](#kumo-through-a-remote-nim)),
start the tunnel again afterwards.

## Brev VM mode

On a Linux x86_64 VM with an NVIDIA GPU, such as a Brev instance, `core,parse,kumo` runs everything locally,
Kumo included. Nothing about the demo changes: the same `demo.sh`, bound to the VM's loopback, reached over SSH,
or with the UI alone shared through a Brev link.

1. **Check the host.** Docker Engine 28+ with Compose 2.30+, a kernel of 6.2 or later (Landlock), and the NVIDIA
   Container Toolkit (`docker info` lists the `nvidia` runtime). Plan for 150 GB of free disk ([disk](#disk)).
2. **Get the code** on the VM. With GitHub access there, `git clone` it. Without it, ship a bundle:

   ```bash
   git bundle create /tmp/demo.bundle --all                 # on your machine, in your clone
   scp /tmp/demo.bundle <instance>:/tmp/
   git clone /tmp/demo.bundle knowledge-foundation-demo     # on the VM
   ```

   To update later, bundle and copy again, then `git pull` on the VM (its `origin` is the bundle's path). For the
   `ontology` profile, also bring the private submodule ([Auto Ontology](../tools/auto-ontology/README.md#run)).
3. **Configure.** Run `./scripts/demo.sh init` on the VM, then edit `.env` there (it stays mode 600; keep keys off
   command lines). Set `INFERENCE_API_KEY` (your `nvapi-` key, which also serves the retriever) and
   `COMPOSE_PROFILES=core,parse,kumo`. `KUMO_RELATIONAL_URL` and `KUMO_API_KEY` are ignored: the `kumo` profile
   runs its own NIM, and `demo.sh` passes the prediction server no key for it (the Kumo client refuses to send one
   over plain `http://`), so a `.env` copied from the Spark works unchanged.
4. **The Kumo NIM image.** `up` pulls `nvcr.io/nim/nvidia/kumo-relational:1.0.1` (about 14 GB to download, 44 GB
   unpacked); the pull has worked without a login. If it is denied, log in with an NGC API key, piped rather than
   typed on the command line:

   ```bash
   printf '%s' "$NGC_API_KEY" | docker login nvcr.io --username '$oauthtoken' --password-stdin
   ```

   The NIM has no GPU gate: its only model profile carries no GPU tag, so it starts on any NVIDIA GPU, including
   ones outside its support matrix such as the A100. It logs `TagsBasedProfileSelector not able to find the
   profile` and then `ManifestProfileSelector compatible profile selected`, which is expected.
5. **Start and prove it.** `./scripts/demo.sh doctor --keys`, `./scripts/demo.sh up`, `./scripts/demo.sh check`,
   then `./scripts/demo.sh status` until the packs are ingested.
6. **Reach it** from your machine through an SSH tunnel, then open <http://127.0.0.1:3300> and
   <http://127.0.0.1:6306> locally:

   ```bash
   ssh -N -L 3300:127.0.0.1:3300 -L 6306:127.0.0.1:6306 <instance>
   ```

7. **Share the UI** (optional) through a Brev link with sign-in set in the Brev console: set `UI_BIND_HOST=0.0.0.0`
   and run `./scripts/demo.sh up`. Only the UI leaves loopback; `doctor` warns, because the UI, its uploads and the
   agent have no sign-in of their own. Never share it through a link without sign-in: anyone could run the agent
   on the keys in `.env`.

## Phoenix

Phoenix runs at <http://127.0.0.1:6306>, on loopback only. All traces go to the project `knowledge-foundation`:

| Source | Spans |
|---|---|
| Hermes, through NeMo Relay | The agent turn, each model call and each tool call. Run metadata (`aiq.job.ref`, `hermes.*`) is promoted to span attributes |
| Switchyard | `libsy.run` per request, with `switchyard.route`, `session.id` (the job id) and `evidence.verdict` on escalation templates |
| retrieval | `embed`, `search` and `rerank`, under the MCP `tools/call retrieve_evidence` span |
| tables | `guard` and `duckdb`, under `tools/call query_tables` |
| prediction | `kumo`, under `tools/call predict` |

Ingest sends no spans; its stages are in the file cards and in `./scripts/demo.sh logs ingest`.

In the UI, each run's execution view links to its trace. The API finds it through
`GET /v1/jobs/async/job/{id}/trace`, which looks the job up by `aiq.job.ref` and falls back to `session.id`; the
link is `<PHOENIX_URL>/redirects/traces/<trace id>`. Set `PHOENIX_URL` in `.env` if the browser reaches Phoenix at
another address (through a tunnel, keep `http://127.0.0.1:6306`), or to empty to hide the link.

**What traces hold.** Relay exports without an allowlist or a collector. Model spans carry the system instructions
and the current turn from the last user message on, including every tool result; only earlier turns are dropped
(`enable_full_payloads = false` in `agent/profile/relay-plugins.toml`). That is why Phoenix must stay on loopback.
Traces persist in the `phoenix-data` volume until `down --volumes`.

**Routing without Phoenix.**

```bash
./scripts/demo.sh logs routing -f                                          # one line per upstream call
curl -s "127.0.0.1:4300/v1/routing/session-stats?session_id=<job id>"      # calls and tokens per model
curl -s 127.0.0.1:4300/v1/stats                                            # totals, errors, routing overhead
```

## Jobs

- One job runs at a time and four more may wait; a sixth gets `429` with `Retry-After`.
- A job has a 1,200 s deadline and Hermes run budgets (idle, no progress, tool calls); on any of them it fails with
  a message the UI shows, and its Hermes run is stopped.
- Cancelling a queued job means it never starts; cancelling a running one stops its Hermes run.
- When the API restarts, it fails every unfinished job ("The API restarted before this job finished; please
  retry.") and stops its Hermes run.
- Finished jobs stay for `JOB_RETENTION_SECONDS` (a day by default).

Ingestion jobs are separate: they live in ingest's own store, survive a restart (unfinished files are re-queued),
and never block questions. The API's settings are in [`api/README.md`](../api/README.md).

## Recording and replay

```bash
./scripts/demo.sh record --pack retail --all    # ask every question of that pack on the running stack
./scripts/demo.sh replay                        # the UI alone on every pack's recordings: no .env, keys, API or GPU
./scripts/demo.sh up                            # back to live mode (recreates only the UI)
```

`replay` swaps the UI container into replay mode on the same port; it needs at least one pack with recordings.
[Data packs](data-packs.md#recordings) covers the options and what to review before committing a bundle.

## On-demand checks

Two commands check a running deployment. You run them by hand, for example before a demo or after changing the
host. Every question they ask runs live and costs model calls.

| Command | What it checks | Needs |
|---|---|---|
| `./scripts/demo.sh test live --url URL [--pack P] [--questions ID,...] [--budget ...]` | Each industry's featured questions asked through the UI (success, resolved citations, the declared tool pills, the replay, the closing events, a latency budget), and the upload flow on "Your data": a PDF and a spreadsheet through the pipeline stages, then a question that cites both | Node.js 22 (Playwright fetches Chromium), and the URL of the deployment's UI |
| `./scripts/demo.sh eval [--pack P] [--runs N] [--questions ID,...] [--url URL] [--out DIR] [--max-wait SECONDS]` | Answer quality: the checks of the pack's `answers.yaml`, and with `GRADER_*` set an LLM grader ([eval](../eval/README.md)) | uv; by default this host's UI |

The URL is the UI's, given on the command line and never stored: `http://127.0.0.1:3300` on the host, or the same
address through an SSH tunnel. The test cannot sign in, so do not point it at a public link.

```bash
./scripts/demo.sh test live --url http://127.0.0.1:3300
./scripts/demo.sh test live --url http://127.0.0.1:3300 --pack retail --questions return-window-fees,gold-churn-risk
./scripts/demo.sh test live --url http://127.0.0.1:3300 --budget 300 --budget gold-churn-risk=600
```

`ui/test-results/live/` keeps the results as JSON. The checks are unit-tested in `ui/e2e-live/checks.test.ts`; to
try the whole test without a deployment, run it against the stand-in in [`ui/README.md`](../ui/README.md#test).

## Disk

Plan for about 150 GB of free disk with `core,parse,kumo`: the images, their build cache, the Kumo NIM (about
14 GB to download, 44 GB unpacked), the Parse weights in `parse-cache`, Milvus and the knowledge volume. Without
`kumo` (a Spark), about 60 GB less. `docker system df` shows the current split.

What grows is the build cache. Each rebuild of a changed image adds its new layers, and with Docker's classic image
store each rebuild also leaves the previous image untagged. `./scripts/demo.sh down --prune` removes both: this
project's untagged images, and Docker's unused build cache. The build cache is host-wide, not per project, so
`--prune` also clears other projects' unused cache (never their images, containers or volumes), and the next build
starts cold. To trim the cache instead while the stack runs, cap it:

```bash
docker builder prune --force --max-used-space 30gb   # keeps at most 30 GB, the most recently used
```

Either way, the next `up` may rebuild images and recreate their containers; the sandbox is recreated only if the
agent image or a tool image gets a new ID.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `error: no .env yet` | Run `./scripts/demo.sh init`, then add the keys |
| `doctor` reports a problem | Each message names the variable or host requirement; fix it and run `doctor --keys` again |
| <a id="port-collisions"></a>`127.0.0.1:<port> is already in use` | Another demo or service holds one of the block's ports. Find it with `ss -ltnp 'sport = :<port>'` or `docker ps --filter publish=<port>`, and stop it. Only the UI's port is a setting (`UI_PORT`); the others are fixed in `compose.yaml`, chosen to avoid the common ones (Phoenix's 6006, the API's 8000, Switchyard's 4000, OpenShell's 18080) |
| `the kumo profile needs an x86_64 Docker host` | On a Spark, use a remote NIM with the `prediction` profile ([above](#kumo-through-a-remote-nim)) |
| `KUMO_RELATIONAL_URL is empty` or `KUMO_API_KEY is empty` | The `prediction` profile (the default) needs a remote Kumo Relational NIM's URL and key. Set both, or drop `prediction` from `COMPOSE_PROFILES` to run without predictions |
| `kumo: GET KUMO_RELATIONAL_URL/v1/health/ready failed` | curl's line above it says why: 401 or 403 is a key the gateway refuses, a connection error a NIM that is down or a wrong URL, 503 a NIM that is still starting |
| `the parse and kumo profiles need the NVIDIA Container Toolkit` | Install it, or drop `parse` and set a hosted `PARSE_BASE_URL` |
| `up` waits a long time on `parse` | The first start downloads the Parse weights (its health check allows 15 minutes). `./scripts/demo.sh logs parse` shows the download and vLLM's start |
| A file card shows `pdf-text-layer` and a warning | Parse was disabled, unreachable or too slow, so the PDF was read from its text layer. Check `./scripts/demo.sh logs parse ingest`; for a pack, `./scripts/demo.sh data sync` tries Parse again |
| An image upload fails with `parser_unavailable` | Images need Parse: add the `parse` profile or set `PARSE_BASE_URL` |
| An upload fails with `unsupported_type`, `type_mismatch`, `empty_file` or `413` | The extension is not allowlisted, the content does not match it (a renamed file), the file is empty, or the file or request is over the limits ([ingestion](ingestion.md#limits)) |
| A pack stays `ingesting` or turns `failed` | `./scripts/demo.sh data status` shows its error; `./scripts/demo.sh logs ingest` the file's. Fix and `./scripts/demo.sh data sync` |
| The packs and data source routes answer 503 | Ingest has not written the catalog yet; wait for the first sync |
| Documents fail at `embedding` | `RETRIEVER_API_KEY` (or `INFERENCE_API_KEY`) is missing or not accepted by `RETRIEVER_BASE_URL`; `doctor --keys` checks the embed model is listed |
| Retrieval fails on a gateway with 404 on rerank | The gateway has no NVIDIA `/ranking` route: set `RETRIEVER_RERANK_MODEL=` (empty) and `./scripts/demo.sh restart switchyard` |
| Predictions say `available: false` | The remote NIM could not be reached, is still starting, refused `KUMO_API_KEY`, or rejected the query; the reason is in the answer. `doctor --keys` checks the endpoint and the key |
| Milvus restarts or is unhealthy | Docker has less than 8 GiB of memory; give it more |
| `sandbox hermes is not Ready after 180s` | The sandbox's recent log is printed just before it. Check the Docker host kernel (Linux 6.2+ with Landlock) and `./scripts/demo.sh logs openshell-preflight openshell` |
| `hermes-gateway` never turns healthy | The sandbox is not Ready, or `HERMES_API_SERVER_KEY` is shorter than 16 characters (run `init`) |
| A tool call fails while the sandbox is Ready | `./scripts/demo.sh check`, then `./scripts/demo.sh logs agent`: `DENIED` lines name the binary, host and reason |
| Model calls fail with 403 | The key's access group does not include a configured model. `doctor --keys` checks only that the endpoint lists it. See `./scripts/demo.sh logs switchyard` |
| `429` when asking a question | The queue is full (one running, four waiting); wait or cancel a job |
| A job fails: "the evidence for its tool calls was not recorded" | The API rejected a receipt that breaks the display-safe limits, or the plugin could not post it. See `./scripts/demo.sh logs api` and `./scripts/demo.sh logs agent` |
| A job fails after about 10 minutes: "Hermes stopped after exceeding its idle budget; please retry." | A model call stalled upstream and never returned. Ask again |
| The router shows a model without a tier, e.g. `nemotron-3-super-120b-a12b` | The agent's model answered "Service temporarily overloaded" twice, so Hermes finished the run on `knowledge-fallback` ([how](../infra/switchyard/README.md#routes)) |
| A run has no Phoenix link | Phoenix has no span for the job yet (Relay exports every second), or `PHOENIX_URL` is empty |
| Every `up` recreates containers or the sandbox | An image got a new ID. Build through `demo.sh`, which turns off provenance attestations; a plain `docker build` retags the image with a different ID |
| `the ontology profile needs the private submodule` | Run `git submodule update --init --checkout vendor/auto-ontology` (needs access), or drop the profile |
| Auto Ontology: "The semantic layer hasn't been created yet" | The ontology is still compiling (about 10 minutes after the first `up`; `./scripts/demo.sh logs auto-ontology-ingestion` shows `semantic: finished successfully`). If the log says `compilation disabled`, run `./scripts/demo.sh restart auto-ontology-ingestion` ([why](../tools/auto-ontology/README.md#how-the-ontology-is-built)) |
| Auto Ontology answers from old data after a sync | It keeps reading the DuckDB file it opened; run `./scripts/demo.sh restart auto-ontology` and `./scripts/demo.sh restart auto-ontology-ingestion` |
| The data viewer's ontology: "Auto Ontology serves retail.sales only" | Auto Ontology serves one source, `AUTO_ONTOLOGY_SOURCE`; the other sources have no ontology |

For the sandbox specifically, see the troubleshooting table in
[`infra/openshell/README.md`](../infra/openshell/README.md#troubleshooting).
