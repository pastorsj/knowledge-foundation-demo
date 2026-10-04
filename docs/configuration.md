<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Configuration

All configuration lives in `.env` at the repository root. `./scripts/demo.sh init` creates it from
[`.env.example`](../.env.example) with mode 600 and fills in the internal secrets; you add one build.nvidia.com
(`nvapi-`) key, the remote Kumo Relational NIM's URL and key for predictions, and pick the profiles. `.env` is
gitignored and must never be committed. `./scripts/demo.sh replay` needs no `.env` at all.

## How `.env` is read

- `demo.sh` never sources `.env`, because bash and Compose parse it differently. It reads the values from
  `docker compose config --environment`, so both see the same thing.
- A variable set in your shell wins over `.env`, as in Compose: `COMPOSE_PROFILES=core ./scripts/demo.sh up`.
- Keep comments on their own lines, never after a value.
- To run Compose by hand, pass the OpenShell pins along with `.env` (passing `--env-file` turns off Compose's own
  `.env` lookup):

  ```bash
  docker compose --env-file infra/openshell/versions.env --env-file .env ps
  ```

  A raw `up` skips what `demo.sh` derives (below) and the sandbox bring-up, so use `demo.sh` for anything that
  starts services.

## Variables

The sections follow `.env.example`.

### 1. Inference endpoint

The agent's models, and Auto Ontology's. Only Switchyard reads the keys on the agent path; no model key enters the
sandbox.

| Variable | Default | Meaning |
|---|---|---|
| `INFERENCE_BASE_URL` | `https://integrate.api.nvidia.com/v1` | build.nvidia.com, or any OpenAI-compatible endpoint that serves every model |
| `INFERENCE_API_KEY` | – (required) | An `nvapi-` key for build.nvidia.com, or the gateway's key. Also the retriever's when `RETRIEVER_API_KEY` is empty |
| `SWITCHYARD_ROUTES` | `passthrough.nemotron` | The routing template in `infra/switchyard/routes/` ([models and routing](models-and-routing.md)) |
| `AGENT_EFFICIENT_MODEL` | `nvidia/nemotron-3-ultra-550b-a55b` | The model that answers (every turn in `passthrough.nemotron`) |
| `AGENT_AUX_MODEL` | `nvidia/nemotron-3-super-120b-a12b` | Hermes' auxiliary calls and the overload fallback (thinking off); must differ from the efficient model |
| `AGENT_CAPABLE_MODEL`, `AGENT_JUDGE_MODEL` | – | The escalation and pinned templates' capable model and judge |
| `CAPABLE_BASE_URL`, `CAPABLE_API_KEY` | `INFERENCE_BASE_URL`; `INFERENCE_API_KEY` on that same endpoint only | A provider serving the frontier model. The inference key never goes to another host |
| `SWITCHYARD_CONFIRMATIONS` | `1` | Judge verdicts needed before escalating |
| `AUTO_ONTOLOGY_REASONING_MODEL`, `AUTO_ONTOLOGY_NON_REASONING_MODEL` | Nemotron 3 Super, Nemotron 3.5 Lightning | Auto Ontology's models (`ontology` profile) |

After editing this section, run `./scripts/demo.sh restart switchyard`; it keeps the sandbox.

### 2. Retriever endpoint

Document embedding (ingest and retrieval) and reranking (retrieval).

| Variable | Default | Meaning |
|---|---|---|
| `RETRIEVER_BASE_URL` | `https://integrate.api.nvidia.com/v1` | build.nvidia.com, a self-hosted NIM, or another OpenAI-compatible endpoint |
| `RETRIEVER_API_KEY` | `INFERENCE_API_KEY` | Set it when section 1 uses a key that is not an `nvapi-` key |
| `RETRIEVER_EMBED_MODEL` | `nvidia/nemotron-3-embed-1b` | Ingest and retrieval must use the same model. Changing it needs a rebuild of the index: `./scripts/demo.sh down --volumes`, then `up` (the packs re-ingest; uploads must be added again). Uploads are not re-embedded, a model of another dimension cannot reuse the collection, and the ingest image bakes in the model's tokenizer |
| `RETRIEVER_RERANK_MODEL` | `nvidia/llama-nemotron-rerank-vl-1b-v2` | Empty: no reranking (hits keep their vector order). Set it empty for a gateway without NVIDIA's `/ranking` API |
| `RETRIEVER_RERANK_URL` | the reranker's build.nvidia.com endpoint | A full rerank URL, e.g. a self-hosted NIM's `http://<host>:8000/v1/ranking` |

`RETRIEVER_RERANK_MODEL` tells unset from empty, so `.env` must name the model to keep reranking on
(`.env.example` does). `./scripts/demo.sh restart switchyard` also applies this section to ingest and retrieval.

### 3. What runs

| Variable | Default | Meaning |
|---|---|---|
| `COMPOSE_PROFILES` | `core,parse,prediction` | The profiles ([below](#profiles)). DGX Spark: `core,parse,prediction`; Brev or x86_64 with a GPU: `core,parse,kumo` |
| `KUMO_RELATIONAL_URL` | – (required with `prediction`) | The base URL of a remote Kumo Relational NIM, the one that serves `/v1/health/ready`. Use `https://`: the Kumo client sends the key over plain `http://` only to `localhost`, `127.0.0.1` or `::1`. Unused under `kumo`, which runs its own NIM |
| `KUMO_API_KEY` | – (required with `prediction`) | The key of the authenticating gateway in front of that NIM, sent as the `X-API-Key` header. Ignored under `kumo`: `demo.sh` passes the local NIM no key |
| `UI_PORT` | `3300` | The UI's host port |
| `UI_BIND_HOST` | `127.0.0.1` | The UI's host address. `0.0.0.0` only behind a link that requires sign-in (a Brev link with sign-in set in the Brev console): the UI, its uploads and the agent have no sign-in of their own. Every other port stays on 127.0.0.1 |
| `DEFAULT_PACK` | `retail` | The industry the UI opens on (a directory of `data/packs`) |

### 4. Ingestion

| Variable | Default | Meaning |
|---|---|---|
| `PARSE_BASE_URL` | under `parse`: `http://parse:8000/v1`; else empty | A hosted Nemotron Parse endpoint when the `parse` profile is off, e.g. `https://integrate.api.nvidia.com/v1`. Empty: PDFs from their text layer, images refused |
| `PARSE_API_KEY` | – | An `nvapi-` key for a hosted Parse endpoint; the local vLLM needs none |
| `PARSE_MODEL` | `nvidia/NVIDIA-Nemotron-Parse-2.0` | The served model id; on build.nvidia.com `nvidia/nemotron-parse-2.0` |
| `PARSE_GPU_MEMORY_UTILIZATION` | `0.15` | The vLLM server's share of GPU memory (the Spark's memory is unified: keep it small) |
| `PARSE_CONCURRENCY` | `4` | Pages in flight to Parse per document |
| `PARSE_MAX_TOKENS` | `8192` | Parse's output cap per page. 8192 fits the local vLLM's 9,000-token context; set `4000` with build.nvidia.com, which serves `nvidia/nemotron-parse-2.0` with a 4,096-token context that also holds the prompt, and refuses a cap of 4,091 or more with HTTP 400 |
| `INGEST_MAX_FILE_MB`, `INGEST_MAX_FILES`, `INGEST_MAX_REQUEST_MB` | `100`, `20`, `512` | Upload limits: per file, files per request, the request body. The UI's upload zone uses the same values |
| `INGEST_WORKERS` | `2` | Files ingested at once |
| `INGEST_STAGE_TIMEOUT_SECONDS` | `1800` | Seconds one pipeline stage of one file may take; Parse may use three quarters of it ([ingestion](ingestion.md)) |

[Ingestion](ingestion.md) explains each.

### 5. Internal secrets

Generated by `./scripts/demo.sh init` (64 random hex characters each); `init` fills only empty ones.

| Variable | Meaning |
|---|---|
| `HERMES_API_SERVER_KEY` | The API's bearer key for Hermes' Runs API inside the sandbox (at least 16 characters). Changing it recreates the sandbox on the next `up` |
| `HERMES_RECEIPT_API_KEY` | The key on the sandbox's receipt callbacks to the API; Hermes sees only an OpenShell placeholder. Must differ from the one above |
| `AUTO_ONTOLOGY_ADMIN_PASSWORD`, `AUTO_ONTOLOGY_AUTH_SECRET` | Auto Ontology's local admin password and session secret (`ontology` profile) |

### 6. Optional

| Variable | Default | Meaning |
|---|---|---|
| `AUTO_ONTOLOGY_SOURCE` | `retail.sales` | The one structured source Auto Ontology serves (`ontology` profile): its ingestion builds the ontology of this source, and the data viewer shows an ontology for it only |
| `SPEECH_INPUT_ENABLED` | `false` | The composer's microphone; transcribed by Nemotron ASR on build.nvidia.com |
| `SPEECH_API_KEY` | `RETRIEVER_API_KEY` when the retriever is build.nvidia.com | An `nvapi-` key for the ASR |
| `SPEECH_CLEANUP_MODEL`, `SPEECH_INPUT_MAX_SECONDS` | –, `60` | An optional deletion-only transcript cleanup; the recording limit |
| `JOB_RETENTION_SECONDS` | `86400` | How long finished jobs stay |
| `PHOENIX_URL` | `http://127.0.0.1:6306` | The address the browser opens Phoenix at; empty hides the link |

### Derived by `demo.sh`

`load_env` in [`scripts/lib/env.sh`](../scripts/lib/env.sh) computes what Compose interpolation cannot:

| Variable | Value |
|---|---|
| `COMPOSE_PROFILES` | `core,parse,prediction` when empty; `core` is always added for commands that run the stack |
| `PARSE_BASE_URL` | `http://parse:8000/v1` under the `parse` profile |
| `KUMO_RELATIONAL_URL` | `http://kumo-relational:8000` under the `kumo` profile, in place of `.env`'s |
| `AUTO_ONTOLOGY_URL` | `http://auto-ontology-frontend:3000` under the `ontology` profile, for the API's data viewer |
| `AGENT_FEATURES` | The tools baked into the agent image: `retrieval,tables` always; `kumo` with the `kumo` profile, or with `prediction` and a `KUMO_RELATIONAL_URL`; `ontology` with the `ontology` profile |
| `RETRIEVER_API_KEY`, `SPEECH_API_KEY`, `CAPABLE_API_KEY` | The fallbacks in the tables above, never to another host |

## Profiles

| Profile | Adds | Needs |
|---|---|---|
| `core` (always) | OpenShell gateway and Hermes forwarder, Switchyard, Phoenix, API, UI, ingest, Milvus, retrieval and tables | Docker, Linux 6.2+ |
| `parse` | Nemotron Parse 2.0 on vLLM (`parse`, :8340) | An NVIDIA GPU and the NVIDIA Container Toolkit (DGX Spark or x86_64) |
| `kumo` | The Kumo Relational NIM and the prediction server (:8322) | x86_64, an NVIDIA GPU, about 44 GB of disk for the NIM |
| `prediction` | The prediction server alone, against `KUMO_RELATIONAL_URL` | A remote Kumo Relational NIM: `KUMO_RELATIONAL_URL` and `KUMO_API_KEY` (required) |
| `ontology` | Auto Ontology (MCP :3303, web app, ingestion, pgvector) | The private `vendor/auto-ontology` submodule; builds on arm64 (DGX Spark) and x86_64 |
| `replay` | The UI alone on the recorded sessions | Nothing; `./scripts/demo.sh replay` sets it |
| `build`, `tools` | The agent image build and the OpenShell CLI | Used by `demo.sh` only |

`./scripts/demo.sh test compose` renders every supported set (`core`, `core,prediction`, `core,parse`,
`core,parse,kumo`, `core,parse,prediction`, `core,parse,kumo,ontology`, `core,parse,prediction,ontology`, `replay`,
`build,tools`) with no `.env`, and fails if any port leaves 127.0.0.1.

## Hardware

| Host | Profiles | Notes |
|---|---|---|
| Replay | `replay` (`./scripts/demo.sh replay`) | Any Docker host; no keys, no GPU |
| DGX Spark (GB10, arm64) | `core,parse,prediction` | Parse on the GPU with `PARSE_GPU_MEMORY_UTILIZATION=0.15`. Predictions through a remote Kumo Relational NIM: `KUMO_RELATIONAL_URL` and `KUMO_API_KEY` ([Spark mode](operations.md#dgx-spark-mode)) |
| Brev or another x86_64 GPU VM | `core,parse,kumo` | Linux, an NVIDIA GPU, driver 535+ and the NVIDIA Container Toolkit. The Kumo NIM is about 14 GB to download and 44 GB on disk and runs with a 16 GB shared-memory segment ([Brev VM mode](operations.md#brev-vm-mode)) |
| Any Linux host without a GPU | `core,prediction`, with a hosted `PARSE_BASE_URL` | Parse on build.nvidia.com; predictions through a remote Kumo Relational NIM |

Every host needs Docker Engine 28+, Compose 2.30+, a Docker host kernel of 6.2 or later (OpenShell needs Landlock
ABI 3) and at least 8 GiB of memory for Docker (Milvus). The agent, embedding and rerank models are always hosted;
only Parse and Kumo run locally.

## `doctor`

`./scripts/demo.sh doctor` checks:

- the host: Docker, Compose and kernel versions; the NVIDIA runtime for `parse` and `kumo`; an x86_64 host for
  `kumo` (on arm64 it names the remote-NIM alternative); Docker memory for Milvus;
- the ports the active profiles publish (3300, 4300, 6306, 8300, 8320, 8321, 8330, 18380, 18381; 8340 with
  `parse`, 8322 with `kumo` or `prediction`, 3303 with `ontology`) are free, skipped while the stack runs;
- `.env`: known profiles, the submodule for `ontology`, the template and its model ids, key shapes on
  build.nvidia.com, a Parse endpoint (a warning), with `prediction` (and not `kumo`) a `KUMO_RELATIONAL_URL` and a
  `KUMO_API_KEY` (`https://`, or `http://` to localhost only), voice input, and the generated secrets.

`up` runs the same checks except ports and keys. Messages name variables, never their values.

`doctor --keys` also asks each endpoint for its model list with your key and looks for every id the template
uses, the Auto Ontology models with `ontology`, and the embed model on the retriever endpoint. It checks listing,
not access: some gateways list models outside a key's access group, and those still return 403 when called. With
`prediction`, it also asks the remote Kumo NIM for `GET $KUMO_RELATIONAL_URL/v1/health/ready` with `KUMO_API_KEY`
as `X-API-Key`. curl reads every key from its standard input, never from the command line.

## State and exit codes

- `.demo/` holds `demo.sh`'s lifecycle state (the OpenShell volume records described in
  [OpenShell](openshell.md#known-issues-in-openshell-012)). It is gitignored.
- `.build/` holds the patched Auto Ontology source that `tools/auto-ontology/prepare.sh` writes.

| Exit code | Meaning |
|---|---|
| 0 | Success |
| 1 | A check failed (`check`, a question `record` could not answer) |
| 2 | Usage error |
| 64 | `.env` or profile problem (`doctor`) |
| 69 | A host requirement or a service is missing (Docker unreachable, ingest not answering, gateway or sandbox not ready) |
