#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# The demo's lifecycle. Run ./scripts/demo.sh --help for the commands.
# Needs Docker Engine 28+, Compose 2.30+, bash (3.2 is fine) and curl; `test` also needs uv and Node.
# shellcheck source-path=SCRIPTDIR
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=lib/common.sh
. "$ROOT/scripts/lib/common.sh"
# shellcheck source=lib/env.sh
. "$ROOT/scripts/lib/env.sh"
# shellcheck source=lib/doctor.sh
. "$ROOT/scripts/lib/doctor.sh"
# shellcheck source=lib/openshell.sh
. "$ROOT/scripts/lib/openshell.sh"

readonly COMPOSE_PROJECT=market-demo # the name in compose.yaml
readonly UP_TIMEOUT=3600 # the first data build downloads SEC EDGAR and embeds the corpus
readonly PYTHON_PROJECTS="agent api data data/generate eval tools/retrieval tools/market-analytics tools/auto-ontology"
readonly RUFF=ruff@0.16.9 # the version in .pre-commit-config.yaml
# Profile sets `test compose` renders; each must be valid with no .env.
readonly PROFILE_SETS="core core,retrieval,analytics core,retrieval,analytics-gpu,kumo
  core,retrieval,analytics,kumo,ontology replay build,tools"

usage() {
  cat <<'EOF'
Usage: ./scripts/demo.sh <command> [options]

Setup
  init                    create .env from .env.example and generate its internal secrets
  doctor [--keys]         check the host, .env and profiles; --keys also asks each endpoint
                          for its model list (authenticated, keys never printed)

Run
  up [--no-build]         build, prepare data, start the tools, Switchyard, Phoenix and the API,
                          then the OpenShell sandbox, the Hermes forwarder and the UI
  down [--volumes] [--prune]
                          delete the sandbox, then stop everything (--volumes: also delete data;
                          --prune: also remove this project's untagged images and Docker's unused
                          build cache, which is host-wide)
  restart SERVICE         agent: recreate the sandbox; switchyard: apply section 1 of .env
                          (routes, models, endpoints, keys) to Switchyard, the API, Auto Ontology
                          and retrieval; anything else: docker compose restart SERVICE
  status                  services, the sandbox, Switchyard routes and URLs
  logs [agent|routing|SERVICE...] [-f]
                          agent: Hermes in the sandbox; routing: Switchyard's routing decisions
  check                   prove the sandbox boundary on the running stack

Data and recordings
  data fetch [DATASET...] [--verify-only]
                          fetch the pack's external datasets into DATA_SOURCE_DIR from each
                          DATA_SOURCE_<ID> in .env, and verify them file by file
  data prepare            build the active data pack (and the corpus and index with retrieval);
                          on a running stack, also restart the tools and, if needed, the sandbox
  data reindex            rebuild the retrieval index, e.g. after changing the embed model (with
                          analytics-gpu, also its CPU/GPU comparison)
  data generate [ARGS...]  write the synthetic-market text with NeMo Data Designer and Nemotron, with
                          uv on the host (ARGS go to demo-data-generate, e.g. --profile large)
  data validate|verify|list|clean [--all]
  record [ARGS...]        record sessions from the running stack into
                          data/packs/$DATA_PACK/recordings (ARGS go to `demo-api record`)
  replay                  serve the UI on the recorded sessions only: no .env, keys or GPU

Development
  test [unit|ui|e2e|contracts|compose|switchyard|all]
                          default: unit ui contracts compose

On demand (run by hand)
  test live --url URL [--questions ID,...] [--budget SECONDS|ID=SECONDS]...
                          ask the active pack's featured questions on a running deployment
                          through its UI and API, and check each answer, replay and latency
  test gpu [--perf]       on an NVIDIA GPU host: the CPU/GPU parity tests; --perf also checks
                          the running analytics-gpu stack's speedups against the A100 floors
  eval [--pack P] [--runs N] [--questions ID,...] [--url URL]
                          answer-quality eval of a running deployment (default: this host's UI):
                          oracle checks, plus an LLM grader when GRADER_* are set (eval/README.md)

Configuration comes from .env (see .env.example); a shell variable overrides it.
Profiles come from COMPOSE_PROFILES (default core,retrieval,analytics).
EOF
}

# up [--no-build]
cmd_up() {
  local build=true
  case ${1:-} in
    "") ;;
    --no-build) build=false ;;
    *) die "$EXIT_USAGE" "usage: demo.sh up [--no-build]" ;;
  esac
  load_env
  require_env
  doctor_for_up
  mkdir -p "$DATA_SOURCE_DIR" # before Compose, which would create it as root
  if has_profile ontology; then
    "$ROOT/tools/auto-ontology/prepare.sh"
  fi
  if $build; then
    log "building images (profiles $COMPOSE_PROFILES; agent features: ${AGENT_FEATURES:-none})"
    COMPOSE_PROFILES=$COMPOSE_PROFILES,build,tools dc build
  fi
  log "preparing data and starting the tools, Switchyard, Phoenix and the API"
  # shellcheck disable=SC2046 # one service per word
  dc up -d --wait --wait-timeout "$UP_TIMEOUT" $(backend_services)
  measure_retrieval_indexes
  openshell_up
  log "starting the UI"
  dc up -d --wait ui
  summary
}

# What the sandbox calls, by profile, plus the Auto Ontology web app the API signs in to for the
# data viewer's ontology. The data and migration one-shots run as their dependencies.
backend_services() {
  local services="switchyard phoenix api" profile
  for profile in ${COMPOSE_PROFILES//,/ }; do
    case $profile in
      retrieval) services="$services retrieval" ;;
      analytics) services="$services market-analytics" ;;
      analytics-gpu) services="$services market-analytics-gpu" ;;
      kumo) services="$services kumo-relational" ;;
      ontology) services="$services auto-ontology-mcp auto-ontology-frontend" ;;
    esac
  done
  echo "$services"
}

summary() {
  local capable="" judge=""
  if uses_capable_model; then
    capable="capable $AGENT_CAPABLE_MODEL on $(endpoint_host "$CAPABLE_BASE_URL"), "
  fi
  if template_models | grep -qx AGENT_JUDGE_MODEL; then
    judge="judge $AGENT_JUDGE_MODEL, "
  fi
  cat >&2 <<EOF

The demo is up.
  UI        http://127.0.0.1:$UI_PORT
  Phoenix   http://127.0.0.1:6006   (on a remote host, use an SSH tunnel)
  Models    $SWITCHYARD_ROUTES on $(endpoint_host "$INFERENCE_BASE_URL"): efficient $AGENT_EFFICIENT_MODEL,
            ${capable}${judge}aux $AGENT_AUX_MODEL
  Data      $DATA_PACK
Next: ./scripts/demo.sh check
EOF
}

endpoint_host() {
  local host=${1#*://}
  echo "${host%%/*}"
}

# down [--volumes] [--prune]: the sandbox first, then Compose. Never --remove-orphans: the project
# is shared.
cmd_down() {
  local volumes=false prune=false arg
  for arg in "$@"; do
    case $arg in
      --volumes) volumes=true ;;
      --prune) prune=true ;;
      *) die "$EXIT_USAGE" "usage: demo.sh down [--volumes] [--prune]" ;;
    esac
  done
  load_env
  openshell_down
  log "stopping the stack"
  if $volumes; then
    dc --profile '*' down --volumes
    # Compose removes only the active pack's Auto Ontology database; the other packs' go too.
    docker volume ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" \
      --filter label=com.docker.compose.volume=auto-ontology-db | xargs -r docker volume rm
  else
    dc --profile '*' down
  fi
  if $prune; then
    prune_builds
  fi
}

# What rebuilds leave behind. With Docker's classic image store, each rebuild leaves the previous
# image untagged; only those Compose labelled with this project are removed. With the containerd
# image store the old image goes away, but its layers stay in BuildKit's build cache, which grows by
# about 15 GB per rebuild of the RAPIDS image. The build cache is host-wide, not per project, so this
# also clears other projects' unused cache (never their images, containers or volumes).
prune_builds() {
  log "removing the untagged images of earlier $COMPOSE_PROJECT builds"
  docker image prune --force --filter "label=com.docker.compose.project=$COMPOSE_PROJECT"
  log "removing Docker's unused build cache (host-wide); the next build starts cold"
  docker builder prune --force
  docker system df
}

# restart SERVICE
cmd_restart() {
  [ $# -eq 1 ] || die "$EXIT_USAGE" "usage: demo.sh restart agent|switchyard|SERVICE"
  load_env
  require_env
  case $1 in
    agent) openshell_up --recreate ;;
    switchyard) restart_inference ;;
    *) dc restart "$1" ;;
  esac
}

# Apply section 1 of .env (the inference endpoint, its models and keys) to everything that reads it. The sandbox
# is kept. The API recreates only if its model ids changed, and Auto Ontology if its settings did; --no-deps
# leaves the one-shots alone. Compose never compares a secret's value, so the services holding the inference
# key as a secret are always recreated: Switchyard, and retrieval (its key defaults to the inference key).
restart_inference() {
  dc up -d --wait --force-recreate switchyard
  dc up -d --wait --no-deps api
  if has_profile ontology; then
    dc up -d --wait --no-deps auto-ontology auto-ontology-ingestion
  fi
  if has_profile retrieval; then
    dc up -d --wait --no-deps --force-recreate retrieval
  fi
}

cmd_status() {
  [ $# -eq 0 ] || die "$EXIT_USAGE" "usage: demo.sh status"
  local routes endpoints
  load_env
  dc ps -a --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
  echo
  if gateway_ready; then
    endpoints='"sandbox: \(.phase)", (.endpoint_statuses[]
      | "  \(.host):\(.ports | map(tostring) | join(",")) \(.path) \(.last_result)")'
    # shellcheck disable=SC2016 # expanded by the container's shell
    cli_sh 'json=$(openshell sandbox get hermes -o json 2>/dev/null) && echo "$json" | jq -r "$1" ||
      echo "sandbox: none"' "$endpoints"
  else
    echo "sandbox: the OpenShell gateway is not running"
  fi
  if routes=$(curl -fsS --max-time 3 http://127.0.0.1:4000/v1/models 2>/dev/null); then
    echo "switchyard: $SWITCHYARD_ROUTES on $(endpoint_host "$INFERENCE_BASE_URL"), routes:" \
      "$(echo "$routes" | tr ',' '\n' | sed -n 's/.*"id": *"\([^"]*\)".*/\1/p' | tr '\n' ' ')"
  fi
  echo "data pack: $DATA_PACK; profiles: $COMPOSE_PROFILES; agent features: ${AGENT_FEATURES:-none}"
  echo "UI: http://127.0.0.1:$UI_PORT  Phoenix: http://127.0.0.1:6006"
}

# logs [agent|routing|SERVICE...] [-f]
cmd_logs() {
  load_env
  case ${1:-} in
    agent)
      shift
      if [ "${1:-}" = -f ]; then cli logs "$SANDBOX" --tail; else cli logs "$SANDBOX"; fi
      ;;
    routing)
      shift
      if [ "${1:-}" = -f ]; then
        dc exec switchyard tail -n 50 -f /var/lib/switchyard/routing.jsonl
      else
        dc exec switchyard tail -n 50 /var/lib/switchyard/routing.jsonl
      fi
      ;;
    *) dc logs "$@" ;;
  esac
}

# replay: the UI alone on the pack's recordings. Works without .env.
cmd_replay() {
  [ $# -eq 0 ] || die "$EXIT_USAGE" "usage: demo.sh replay"
  export COMPOSE_PROFILES=replay UI_MODE=replay PHOENIX_URL=
  load_env
  [ -f "$ROOT/data/packs/$DATA_PACK/recordings/index.json" ] ||
    die "$EXIT_CONFIG" "data pack $DATA_PACK has no recordings yet: record them on a running stack" \
      "(./scripts/demo.sh record), or replay another pack's, e.g. DATA_PACK=synthetic-market"
  dc up -d --build --wait ui
  log "replay: http://127.0.0.1:$UI_PORT (data pack $DATA_PACK)"
}

# record [ARGS...]: `demo-api record` against the running API, written to the pack's recordings.
cmd_record() {
  load_env
  require_env
  local out=data/packs/$DATA_PACK/recordings
  mkdir -p "$ROOT/$out"
  # As the host user, so the files are the user's; --no-deps leaves the running stack alone.
  dc run --rm --no-deps --user "$(id -u):$(id -g)" -v "$ROOT/$out:/out" --entrypoint demo-api api \
    record --out /out "$@"
  git -C "$ROOT" status --short -- "$out"
  log "review $out before committing: it holds questions, answers, evidence excerpts and model names"
}

# data fetch|prepare|reindex|validate|verify|list|clean [--all]
cmd_data() {
  local action=${1:-} service
  load_env
  mkdir -p "$DATA_SOURCE_DIR" # before Compose, which would create it as root
  case $action in
    # Build the data image first: a no-op when it is current, and after a code update never the old image.
    fetch | prepare | validate | verify | list | clean) COMPOSE_PROFILES=core dc build --quiet data ;;
  esac
  case $action in
    fetch)
      shift
      data_fetch "$@"
      ;;
    prepare)
      dc run --rm --no-deps data prepare --structured
      if has_profile retrieval; then
        dc run --rm --no-deps data-corpus
        reindex
      fi
      # Market analytics and retrieval keep the build they resolved at startup; restart them on the new one
      # (`up --wait` waits until they are healthy again).
      for service in market-analytics market-analytics-gpu retrieval; do
        if [ -n "$(dc ps -q --status running "$service" 2>/dev/null)" ]; then
          dc restart "$service" && dc up -d --wait --no-deps "$service"
        fi
      done
      # Hermes lists the tools once, when the sandbox starts: recreate the sandbox if the build changed them.
      if gateway_ready && [ -n "$(sandbox_state)" ]; then
        require_env
        openshell_up
      fi
      ;;
    reindex) reindex ;;
    generate)
      shift
      data_generate "$@"
      ;;
    validate | verify | list | clean)
      shift
      dc run --rm --no-deps data "$action" "$@"
      ;;
    *) die "$EXIT_USAGE" "usage: demo.sh data fetch|prepare|reindex|generate|validate|verify|list|clean [--all]" ;;
  esac
}

# data fetch [DATASET...] [--verify-only]: the pack's external datasets into DATA_SOURCE_DIR/<id>, each verified
# file by file against the manifest the pack pins (docs/data-platform.md). DATA_SOURCE_<ID> in .env says where
# dataset <id> comes from:
#   a URL (https, s3, gs, hf)      fetched in the data image, which gets only that scheme's credentials
#   a directory or host:/path      copied with rsync here on the host, which has the files and the SSH keys
#   nothing                        what is already in place is verified
data_fetch() {
  local arg id variable source verify_only=false datasets=() options
  for arg in "$@"; do
    case $arg in
      --verify-only) verify_only=true ;;
      -*) die "$EXIT_USAGE" "usage: demo.sh data fetch [DATASET...] [--verify-only]" ;;
      *) datasets+=("$arg") ;;
    esac
  done
  if [ ${#datasets[@]} -eq 0 ]; then
    # shellcheck disable=SC2207 # dataset ids have no spaces
    datasets=($(pack_external | cut -d' ' -f1))
  fi
  if [ ${#datasets[@]} -eq 0 ]; then
    log "data pack $DATA_PACK has no external datasets"
    return 0
  fi
  for id in "${datasets[@]}"; do
    variable=DATA_SOURCE_$(printf '%s' "$id" | tr 'a-z-' 'A-Z_')
    source=$(env_value "$variable")
    source=${source#file://}
    # As the host user, so the files are the user's; HOME for the libraries' caches.
    options=(--rm --no-deps --user "$(id -u):$(id -g)" -e HOME=/tmp)
    if $verify_only || [ -z "$source" ]; then
      log "$id: verifying $DATA_SOURCE_DIR/$id"
      dc run "${options[@]}" data-fetch fetch "$id" --verify-only
    elif [[ $source == *://* ]]; then
      log "$id: fetching from ${source%%\?*}" # a pre-signed URL's query is a credential
      export "$variable=$source"
      options+=(-e "$variable")
      fetch_credentials "$source"
      dc run "${options[@]}" data-fetch fetch "$id"
    else
      log "$id: copying $source into $DATA_SOURCE_DIR/$id with rsync"
      mkdir -p "$DATA_SOURCE_DIR/$id"
      rsync -a --partial --exclude '.*' "${source%/}/" "$DATA_SOURCE_DIR/$id/"
      dc run "${options[@]}" data-fetch fetch "$id" --verify-only
    fi
    # The services read it as their own users, whatever modes it arrived with (rsync -a keeps the source's).
    chmod -R a+rX "$DATA_SOURCE_DIR/$id"
  done
}

# fetch_credentials URL: add to the caller's `options` the credentials URL's scheme reads, for those .env sets.
# Values are exported and passed as -e NAME, so they never appear on a command line.
fetch_credentials() {
  local names="" name value
  case $1 in
    http://* | https://*) names=DATA_SOURCE_HTTP_TOKEN ;;
    s3://*) names="AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_REGION AWS_ENDPOINT_URL" ;;
    hf://*) names=HF_TOKEN ;;
    gs://* | gcs://*)
      value=$(env_value GOOGLE_APPLICATION_CREDENTIALS) # a host path to a service-account file
      if [ -n "$value" ]; then
        options+=(-v "$value:/run/gcs.json:ro" -e GOOGLE_APPLICATION_CREDENTIALS=/run/gcs.json)
      fi
      ;;
  esac
  for name in $names; do
    value=$(env_value "$name")
    if [ -n "$value" ]; then
      export "$name=$value"
      options+=(-e "$name")
    fi
  done
}

# data generate [ARGS...]: the synthetic-market pack's text (data/generate/README.md). The keys are passed in
# the environment of this one command only. An empty DATA_DESIGNER_API_KEY falls back to INFERENCE_API_KEY only
# when DATA_DESIGNER_BASE_URL is INFERENCE_BASE_URL, or for an nvapi- key on build.nvidia.com; demo-data-generate
# decides, and stops otherwise. Paths in ARGS are relative to the current directory.
data_generate() {
  command -v uv >/dev/null || die "$EXIT_CONFIG" "data generate runs with uv on the host: install uv"
  DATA_DESIGNER_API_KEY=$(env_value DATA_DESIGNER_API_KEY) \
    INFERENCE_API_KEY=$INFERENCE_API_KEY INFERENCE_BASE_URL=$INFERENCE_BASE_URL \
    DATA_DESIGNER_BASE_URL=$(env_value DATA_DESIGNER_BASE_URL) \
    DATA_DESIGNER_MODEL=$(env_value DATA_DESIGNER_MODEL) \
    DATA_DESIGNER_PARALLEL=$(env_value DATA_DESIGNER_PARALLEL) \
    SEC_USER_AGENT=$SEC_USER_AGENT \
    uv run --project "$ROOT/data/generate" --locked demo-data-generate "$@"
}

reindex() {
  dc build --quiet retrieval # retrieval-index's image, as the data image above
  dc up -d --wait milvus
  dc run --rm --no-deps retrieval-index
  measure_retrieval_indexes
}

# analytics-gpu with retrieval: the Benchmark tab's Milvus row, the build's CPU index against its copy in the GPU
# Milvus, measured once per build. Answers search the CPU index only, so a GPU Milvus that does not start (its GPU
# memory taken, say) leaves the comparison out with a warning, and is stopped so it holds no GPU memory.
measure_retrieval_indexes() {
  has_profile retrieval && has_profile analytics-gpu || return 0
  log "measuring the Milvus CPU index against its GPU copy (the Benchmark tab), once per build"
  if ! retrieval_benchmark; then
    warn "no Milvus CPU/GPU comparison for this build; answers are unaffected" \
      "(./scripts/demo.sh logs retrieval-benchmark milvus-gpu)"
    dc stop milvus-gpu >/dev/null 2>&1 || true
  fi
}

# The retrieval-benchmark one-shot, with ARGS (e.g. `benchmark --guard`), once the GPU Milvus is healthy.
retrieval_benchmark() {
  dc up -d --wait milvus-gpu && dc run --rm --no-deps retrieval-benchmark "$@"
}

# test [SUITE...] | test live --url URL ... | test gpu [--perf]
cmd_test() {
  case ${1:-} in
    live | gpu)
      local suite=$1
      shift
      log "test $suite"
      "test_$suite" "$@"
      return
      ;;
  esac
  local suites=${*:-unit ui contracts compose} suite
  [ "$suites" != all ] || suites="unit ui e2e contracts compose switchyard"
  for suite in $suites; do
    case $suite in
      unit | ui | e2e | contracts | compose | switchyard) log "test $suite" && "test_$suite" ;;
      *) die "$EXIT_USAGE" "usage: demo.sh test [unit|ui|e2e|contracts|compose|switchyard|all], test live --url URL," \
        "or test gpu [--perf]" ;;
    esac
  done
}

test_unit() {
  local project
  for project in $PYTHON_PROJECTS; do
    log "$project"
    (cd "$ROOT/$project" && uv run --locked pytest -q -m "not gpu and not slow and not live")
  done
  # infra/phoenix holds the one Python file outside the uv projects.
  # shellcheck disable=SC2086 # one project per word
  (cd "$ROOT" && uvx "$RUFF" check $PYTHON_PROJECTS infra/phoenix &&
    uvx "$RUFF" format --check $PYTHON_PROJECTS infra/phoenix)
}

test_ui() {
  (cd "$ROOT/ui" && npm ci && npm run lint && npm run type-check && npm run test:ci)
}

# On Linux, Playwright also installs Chromium's system libraries (apt, through sudo);
# a server image such as Ubuntu 22.04 on a cloud VM lacks them.
test_e2e() {
  local install=(npx playwright install chromium)
  if [ "$(uname -s)" = Linux ]; then
    install=(npx playwright install --with-deps chromium)
  fi
  (cd "$ROOT/ui" && { [ -d node_modules ] || npm ci; } && npm run build && "${install[@]}" && npm run e2e)
}

# test live --url URL [--questions ID,...] [--budget SECONDS|ID=SECONDS]...: the active pack's featured questions,
# asked one at a time through a running deployment's UI (ui/e2e-live). The URL is the UI's, passed here and never
# stored: http://127.0.0.1:3100 on the host, an SSH tunnel to it, or a link to it. Each question runs live and costs
# model calls. Manual only: CI never runs it.
test_live() {
  local url="" questions="" budgets="" install=(npx playwright install chromium)
  local usage="usage: demo.sh test live --url URL [--questions ID,...] [--budget SECONDS|ID=SECONDS]..."
  while [ $# -gt 0 ]; do
    case $1 in
      --url | --questions | --budget) [ $# -ge 2 ] || die "$EXIT_USAGE" "$usage" ;;
      *) die "$EXIT_USAGE" "$usage" ;;
    esac
    case $1 in
      --url) url=$2 ;;
      --questions) questions=$2 ;;
      --budget) budgets=${budgets:+$budgets,}$2 ;;
    esac
    shift 2
  done
  case $url in
    http://?* | https://?*) ;;
    *) die "$EXIT_USAGE" "$usage (the deployment's UI, e.g. http://127.0.0.1:3100)" ;;
  esac
  command -v npx >/dev/null || die "$EXIT_CONFIG" "test live drives a browser with Playwright: install Node.js 22"
  if [ "$(uname -s)" = Linux ]; then
    install=(npx playwright install --with-deps chromium)
  fi
  (cd "$ROOT/ui" && { [ -d node_modules ] || npm ci; } && "${install[@]}" &&
    LIVE_URL=$url LIVE_QUESTIONS=$questions LIVE_BUDGETS=$budgets npx playwright test --config playwright.live.config.ts)
}

# test gpu [--perf]: on a host with an NVIDIA GPU, the CPU/GPU parity tests of market analytics (their RAPIDS venv
# takes about 9 GB on the first run). --perf then measures the running analytics-gpu stack: the active pack's
# eval/perf.yaml cases through market analytics' POST /benchmark, and the Milvus index comparison measured again into
# the guard's own file, each against its floor (eval/README.md). It changes nothing the deployment serves: the
# Benchmark tab keeps the comparison measured at `up`. On a host without a GPU it skips, and succeeds.
test_gpu() {
  local perf=false
  case "$*" in
    "") ;;
    --perf) perf=true ;;
    *) die "$EXIT_USAGE" "usage: demo.sh test gpu [--perf]" ;;
  esac
  if ! command -v nvidia-smi >/dev/null || ! nvidia-smi -L >/dev/null 2>&1; then
    log "no NVIDIA GPU on this host: skipping the GPU tests"
    return 0
  fi
  command -v uv >/dev/null || die "$EXIT_CONFIG" "test gpu runs with uv on the host: install uv"
  log "GPU parity of market analytics (cudf.pandas, cuml.accel, nx-cugraph against pandas)"
  (cd "$ROOT/tools/market-analytics" && uv sync --locked --extra gpu-cu12 &&
    uv run --locked --extra gpu-cu12 pytest -q -m gpu)
  if $perf; then
    gpu_perf
  fi
}

# The GPU guard on the running stack. Its inputs are copied out of the demo-data volume: the active build's
# pack.json (which pack and profile) and the Milvus comparison, measured again first with `benchmark --guard` into
# retrieval-benchmark-guard.json. The API serves retrieval-benchmark.json, which only measure_retrieval_indexes writes.
gpu_perf() {
  local work since status=0 retrieval=(--no-retrieval)
  load_env
  require_env
  has_profile analytics-gpu ||
    die "$EXIT_CONFIG" "test gpu --perf measures the running GPU service: add analytics-gpu to COMPOSE_PROFILES" \
      "and run ./scripts/demo.sh up"
  [ -n "$(dc ps -q --status running market-analytics-gpu 2>/dev/null)" ] ||
    die "$EXIT_UNAVAILABLE" "market-analytics-gpu is not running: run ./scripts/demo.sh up"
  work=$(mktemp -d)
  dc exec -T api cat /data/active/pack.json >"$work/pack.json"
  if has_profile retrieval; then
    log "measuring the Milvus CPU index and its GPU copy again, for this check only (the Benchmark tab keeps up's)"
    # The guard fails a comparison measured before this time: when measuring again fails, the file keeps an older one
    since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    retrieval_benchmark benchmark --guard ||
      warn "measuring the Milvus comparison again failed: ./scripts/demo.sh logs retrieval-benchmark milvus-gpu"
    dc exec -T api cat /data/active/retrieval-benchmark-guard.json >"$work/retrieval.json" 2>/dev/null || true
    retrieval=(--retrieval-benchmark "$work/retrieval.json" --measured-since "$since")
  fi
  log "GPU guard: the active pack's cases against their floors"
  uv run --project "$ROOT/eval" --locked demo-eval --repo "$ROOT" perf --build "$work/pack.json" "${retrieval[@]}" ||
    status=$?
  rm -rf "$work"
  return "$status"
}

# eval [--pack P] [--runs N] [--questions ID,...] [--url URL] [--out DIR]: the answer-quality eval (eval/README.md)
# on a running deployment, by default this host's UI. The optional grader reads GRADER_BASE_URL, GRADER_API_KEY and
# GRADER_MODEL from the environment only. Each question runs live and costs model calls.
cmd_eval() {
  local url="" args=() usage="usage: demo.sh eval [--pack P] [--runs N] [--questions ID,...] [--url URL] [--out DIR]"
  while [ $# -gt 0 ]; do
    case $1 in
      --url | --pack | --runs | --questions | --out | --max-wait) [ $# -ge 2 ] || die "$EXIT_USAGE" "$usage" ;;
      *) die "$EXIT_USAGE" "$usage" ;;
    esac
    if [ "$1" = --url ]; then
      url=$2
    else
      args+=("$1" "$2")
    fi
    shift 2
  done
  command -v uv >/dev/null || die "$EXIT_CONFIG" "eval runs with uv on the host: install uv"
  if [ -z "$url" ]; then
    load_env
    url=http://127.0.0.1:$UI_PORT
  fi
  uv run --project "$ROOT/eval" --locked demo-eval --repo "$ROOT" run --url "$url" ${args[@]+"${args[@]}"}
}

test_contracts() {
  "$ROOT/scripts/gen-contracts.sh" --check
}

# Every profile set renders with only the OpenShell pins: no .env, as in CI and `replay`.
test_compose() {
  local profiles name
  # Compose refuses to start a service whose secret's variable is unset: each must be in .env.example,
  # generated by init, or exported by load_env (env.sh)
  while read -r name; do
    grep -q "^$name=" "$ROOT/.env.example" || grep -qw "$name" <<<"$GENERATED_SECRETS" ||
      grep -Eq "^  export .*\b$name\b" "$ROOT/scripts/lib/env.sh" ||
      die "$EXIT_CONFIG" "secret variable $name is neither in .env.example, generated, nor exported by load_env"
  done < <(sed -n 's/^  [a-z_]*: { environment: \([A-Z_]*\) }.*/\1/p' "$ROOT/compose.yaml")
  for profiles in $PROFILE_SETS; do
    COMPOSE_PROFILES=$profiles docker compose -f "$ROOT/compose.yaml" --env-file "$VERSIONS_FILE" config -q
    # Every port is on loopback; UI_BIND_HOST moves the UI's alone.
    if UI_BIND_HOST='' published_hosts "$profiles" | grep -v ' 127\.0\.0\.1$' ||
      UI_BIND_HOST=0.0.0.0 published_hosts "$profiles" | grep -v -e ' 127\.0\.0\.1$' -e '^ui 0\.0\.0\.0$'; then
      die "$EXIT_CONFIG" "compose publishes a port beyond 127.0.0.1 (profiles $profiles)"
    fi
    case ,$profiles, in
      *,core,* | *,replay,*)
        UI_BIND_HOST=0.0.0.0 published_hosts "$profiles" | grep -qx 'ui 0\.0\.0\.0' ||
          die "$EXIT_CONFIG" "UI_BIND_HOST does not reach the ui port (profiles $profiles)"
        ;;
    esac
    log "compose config: $profiles"
  done
}

# The host addresses Compose publishes on, one "service address" line per port.
published_hosts() {
  COMPOSE_PROFILES=$1 docker compose -f "$ROOT/compose.yaml" --env-file "$VERSIONS_FILE" config |
    awk '/^services:/ { in_services = 1; next }
      /^[^ ]/ { in_services = 0 }
      in_services && /^  [^ ]/ { service = $1; sub(/:$/, "", service) }
      in_services && $1 == "host_ip:" { print service, $2 }'
}

# Builds the image `up` runs: a plain `docker build` would retag it with a different ID.
test_switchyard() {
  dc build -q switchyard >/dev/null
  docker run --rm -v "$ROOT/infra/switchyard/tests:/opt/switchyard/tests:ro" \
    --entrypoint /opt/switchyard/tests/render-all.sh market-demo/switchyard:local
}

main() {
  local command=${1:-}
  case $command in
    "" | -h | --help | help) usage ;;
    init | doctor | up | down | restart | status | logs | replay | record | data | test | check | eval)
      shift
      "cmd_$command" "$@"
      ;;
    *)
      usage >&2
      exit "$EXIT_USAGE"
      ;;
  esac
}

main "$@"
