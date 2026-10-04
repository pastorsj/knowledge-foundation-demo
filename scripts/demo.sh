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

readonly COMPOSE_PROJECT=knowledge-foundation # the name in compose.yaml
readonly UP_TIMEOUT=3600 # the first start downloads Nemotron Parse and builds every image
readonly PYTHON_PROJECTS="agent api eval ingest tools/retrieval tools/tables tools/prediction tools/auto-ontology"
readonly RUFF=ruff@0.16.9 # the version in .pre-commit-config.yaml
# Profile sets `test compose` renders; each must be valid with no .env.
readonly PROFILE_SETS="core core,prediction core,parse core,parse,kumo core,parse,prediction core,parse,kumo,ontology
  core,parse,prediction,ontology replay build,tools"

usage() {
  cat <<'EOF'
Usage: ./scripts/demo.sh <command> [options]

Setup
  init                    create .env from .env.example and generate its internal secrets
  doctor [--keys]         check the host, .env and profiles; --keys also asks each endpoint
                          for its model list (authenticated, keys never printed)

Run
  up [--no-build]         build, start Milvus, Nemotron Parse, ingest, the tools, Switchyard, Phoenix
                          and the API, then the OpenShell sandbox, the Hermes forwarder and the UI.
                          The industry packs ingest in the background (see status)
  down [--volumes] [--prune]
                          delete the sandbox, then stop everything (--volumes: also delete the knowledge
                          catalog, uploads and traces; --prune: also remove this project's untagged images
                          and Docker's unused build cache, which is host-wide)
  restart SERVICE         agent: recreate the sandbox; switchyard: apply sections 1 and 2 of .env
                          (routes, models, endpoints, keys) to Switchyard, the API, ingest, retrieval
                          and Auto Ontology; anything else: docker compose restart SERVICE
  status                  services, the sandbox, Switchyard routes, the industry packs and URLs
  logs [agent|routing|SERVICE...] [-f]
                          agent: Hermes in the sandbox; routing: Switchyard's routing decisions
  check                   prove the sandbox boundary on the running stack

Data and recordings
  data sync               ingest every industry pack whose files changed (data/packs), on the running stack
  data status             each pack's ingestion status
  data validate           check every pack's pack.yaml and questions.yaml against data/schemas
  data generate PACK      regenerate a pack's files from its seeded generator (uv on the host)
  record --pack PACK [ARGS...]
                          record that pack's featured questions from the running stack into
                          data/packs/PACK/recordings (ARGS go to `demo-api record`)
  replay                  serve the UI on the recorded sessions only: no .env, keys or GPU

Development
  test [unit|ui|e2e|contracts|compose|switchyard|all]
                          default: unit ui contracts compose

On demand (run by hand)
  test live --url URL [--pack P] [--questions ID,...] [--budget SECONDS|ID=SECONDS]...
                          ask a pack's featured questions, and the upload flow, on a running
                          deployment through its UI and API, and check each answer and replay
  eval [--pack P] [--runs N] [--questions ID,...] [--url URL] [--out DIR] [--max-wait SECONDS]
                          answer-quality eval of a running deployment (default: this host's UI):
                          answer checks, plus an LLM grader when GRADER_* are set (eval/README.md)

Configuration comes from .env (see .env.example); a shell variable overrides it.
Profiles come from COMPOSE_PROFILES (default core,parse,prediction).
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
  if has_profile ontology; then
    "$ROOT/tools/auto-ontology/prepare.sh"
  fi
  if $build; then
    log "building images (profiles $COMPOSE_PROFILES; agent features: ${AGENT_FEATURES:-none})"
    COMPOSE_PROFILES=$COMPOSE_PROFILES,build,tools dc build
  fi
  log "starting Milvus, Nemotron Parse, ingest, the tools, Switchyard, Phoenix and the API"
  # shellcheck disable=SC2046 # one service per word
  dc up -d --wait --wait-timeout "$UP_TIMEOUT" $(backend_services)
  openshell_up
  log "starting the UI"
  dc up -d --wait ui
  summary
}

# What the sandbox calls, by profile, plus ingest (which the API forwards uploads to) and the Auto Ontology web
# app the API signs in to for the data viewer's ontology. Milvus, Parse and the migrations start as dependencies.
backend_services() {
  local services="switchyard phoenix ingest api retrieval tables" profile
  for profile in ${COMPOSE_PROFILES//,/ }; do
    case $profile in
      parse) services="$services parse" ;;
      kumo) services="$services kumo-relational prediction" ;;
      prediction) services="$services prediction" ;;
      ontology) services="$services auto-ontology-mcp auto-ontology-frontend" ;;
    esac
  done
  echo "$services"
}

# The industry packs' ingestion status, one line per pack, from the ingest service.
packs_status() {
  local status
  if ! status=$(curl -fsS --max-time 5 http://127.0.0.1:8330/v1/packs/status 2>/dev/null); then
    echo "  ingest is not answering"
    return 0
  fi
  python3 -c '
import json, sys
for pack in json.load(sys.stdin).get("packs", []):
    error = " (" + pack["error"] + ")" if pack.get("error") else ""
    pack_id, status = pack["id"], pack["status"]
    done, total = pack.get("files_done", 0), pack.get("files_total", 0)
    print(f"  {pack_id:<20} {status:<10} {done}/{total} files{error}")' <<<"$status"
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
  Phoenix   http://127.0.0.1:6306   (on a remote host, use an SSH tunnel)
  Models    $SWITCHYARD_ROUTES on $(endpoint_host "$INFERENCE_BASE_URL"): efficient $AGENT_EFFICIENT_MODEL,
            ${capable}${judge}aux $AGENT_AUX_MODEL
  Parse     ${PARSE_BASE_URL:-none (PDFs from their text layer; images refused)}
  Features  $AGENT_FEATURES
Industry packs (ingesting in the background; ./scripts/demo.sh data status):
$(packs_status)
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
  else
    dc --profile '*' down
  fi
  if $prune; then
    prune_builds
  fi
}

# What rebuilds leave behind. With Docker's classic image store, each rebuild leaves the previous
# image untagged; only those Compose labelled with this project are removed. With the containerd
# image store the old image goes away, but its layers stay in BuildKit's build cache. The build cache is
# host-wide, not per project, so this also clears other projects' unused cache (never their images,
# containers or volumes).
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

# Apply sections 1 and 2 of .env (the inference and retriever endpoints, their models and keys) to everything
# that reads them. The sandbox is kept. The API recreates only if its model ids changed, and Auto Ontology if its
# settings did; --no-deps leaves the one-shots alone. Compose never compares a secret's value, so the services
# holding a key as a secret are always recreated: Switchyard, ingest and retrieval (the retriever key defaults to
# the inference key).
restart_inference() {
  dc up -d --wait --force-recreate switchyard
  dc up -d --wait --no-deps --force-recreate ingest retrieval
  dc up -d --wait --no-deps api
  if has_profile ontology; then
    dc up -d --wait --no-deps auto-ontology auto-ontology-ingestion
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
    # Compose warns "No services to build" on each run of the CLI container; drop that line only.
    cli_sh 'json=$(openshell sandbox get hermes -o json 2>/dev/null) && echo "$json" | jq -r "$1" ||
      echo "sandbox: none"' "$endpoints" 2> >(grep -v 'msg="No services to build"' >&2)
  else
    echo "sandbox: the OpenShell gateway is not running"
  fi
  if routes=$(curl -fsS --max-time 3 http://127.0.0.1:4300/v1/models 2>/dev/null); then
    echo "switchyard: $SWITCHYARD_ROUTES on $(endpoint_host "$INFERENCE_BASE_URL"), routes:" \
      "$(echo "$routes" | tr ',' '\n' | sed -n 's/.*"id": *"\([^"]*\)".*/\1/p' | tr '\n' ' ')"
  fi
  echo "profiles: $COMPOSE_PROFILES; agent features: ${AGENT_FEATURES:-none}"
  echo "industry packs:"
  packs_status
  echo "UI: http://127.0.0.1:$UI_PORT  Phoenix: http://127.0.0.1:6306"
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

# replay: the UI alone on every pack's recordings. Works without .env.
cmd_replay() {
  [ $# -eq 0 ] || die "$EXIT_USAGE" "usage: demo.sh replay"
  export COMPOSE_PROFILES=replay UI_MODE=replay PHOENIX_URL=
  load_env
  compgen -G "$ROOT/data/packs/*/recordings/index.json" >/dev/null ||
    die "$EXIT_CONFIG" "no pack has recordings yet: record them on a running stack" \
      "(./scripts/demo.sh record --pack PACK)"
  dc up -d --build --wait ui
  log "replay: http://127.0.0.1:$UI_PORT"
}

# record --pack PACK [ARGS...]: `demo-api record` against the running API, written to that pack's recordings.
cmd_record() {
  local pack=""
  if [ "${1:-}" = --pack ] && [ $# -ge 2 ]; then
    pack=$2
    shift 2
  fi
  [ -n "$pack" ] && [ -f "$ROOT/data/packs/$pack/pack.yaml" ] ||
    die "$EXIT_USAGE" "usage: demo.sh record --pack PACK [ARGS...] (a directory of data/packs)"
  load_env
  require_env
  local out=data/packs/$pack/recordings
  mkdir -p "$ROOT/$out"
  # As the host user, so the files are the user's; --no-deps leaves the running stack alone.
  dc run --rm --no-deps --user "$(id -u):$(id -g)" -v "$ROOT/$out:/out" --entrypoint demo-api api \
    record --pack "$pack" --out /out "$@"
  git -C "$ROOT" status --short -- "$out"
  log "review $out before committing: it holds questions, answers, evidence excerpts and model names"
}

# data sync|status|validate|generate PACK
cmd_data() {
  case ${1:-} in
    sync)
      curl -fsS --max-time 10 -X POST http://127.0.0.1:8330/v1/packs/sync >/dev/null ||
        die "$EXIT_UNAVAILABLE" "ingest is not answering on 127.0.0.1:8330: ./scripts/demo.sh up"
      log "pack sync started; ./scripts/demo.sh data status follows it"
      ;;
    status) packs_status ;;
    validate)
      command -v uv >/dev/null || die "$EXIT_CONFIG" "data validate runs with uv on the host: install uv"
      uv run --quiet --with jsonschema --with pyyaml python "$ROOT/scripts/validate_packs.py" "$ROOT"
      ;;
    generate)
      [ $# -eq 2 ] && [ -f "$ROOT/data/packs/$2/generator/build.py" ] ||
        die "$EXIT_USAGE" "usage: demo.sh data generate PACK (a pack with generator/build.py)"
      command -v uv >/dev/null || die "$EXIT_CONFIG" "data generate runs with uv on the host: install uv"
      uv run "$ROOT/data/packs/$2/generator/build.py"
      ;;
    *) die "$EXIT_USAGE" "usage: demo.sh data sync|status|validate|generate PACK" ;;
  esac
}

# test [SUITE...] | test live --url URL ...
cmd_test() {
  case ${1:-} in
    live)
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
      *) die "$EXIT_USAGE" "usage: demo.sh test [unit|ui|e2e|contracts|compose|switchyard|all], or test live --url URL" ;;
    esac
  done
}

test_unit() {
  local project
  for project in $PYTHON_PROJECTS; do
    log "$project"
    (cd "$ROOT/$project" && uv run --locked pytest -q -m "not gpu and not slow and not live")
  done
  # The whole tree, as pre-commit lints it: the uv projects, the pack generators, scripts/ and infra/.
  (cd "$ROOT" && uvx "$RUFF" check . && uvx "$RUFF" format --check .)
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

# test live --url URL [--pack P] [--questions ID,...] [--budget SECONDS|ID=SECONDS]...: a pack's featured questions
# (every industry pack by default), asked one at a time through a running deployment's UI (ui/e2e-live), and the
# upload flow on "Your data". The URL is the UI's, passed here and never stored: http://127.0.0.1:3300 on the host,
# an SSH tunnel to it, or a link to it. Each question runs live and costs model calls. Manual only.
test_live() {
  local url="" pack="" questions="" budgets="" install=(npx playwright install chromium)
  local usage="usage: demo.sh test live --url URL [--pack P] [--questions ID,...] [--budget SECONDS|ID=SECONDS]..."
  while [ $# -gt 0 ]; do
    case $1 in
      --url | --pack | --questions | --budget) [ $# -ge 2 ] || die "$EXIT_USAGE" "$usage" ;;
      *) die "$EXIT_USAGE" "$usage" ;;
    esac
    case $1 in
      --url) url=$2 ;;
      --pack) pack=$2 ;;
      --questions) questions=$2 ;;
      --budget) budgets=${budgets:+$budgets,}$2 ;;
    esac
    shift 2
  done
  case $url in
    http://?* | https://?*) ;;
    *) die "$EXIT_USAGE" "$usage (the deployment's UI, e.g. http://127.0.0.1:3300)" ;;
  esac
  command -v npx >/dev/null || die "$EXIT_CONFIG" "test live drives a browser with Playwright: install Node.js 22"
  if [ "$(uname -s)" = Linux ]; then
    install=(npx playwright install --with-deps chromium)
  fi
  (cd "$ROOT/ui" && { [ -d node_modules ] || npm ci; } && "${install[@]}" &&
    LIVE_URL=$url LIVE_PACK=$pack LIVE_QUESTIONS=$questions LIVE_BUDGETS=$budgets \
      npx playwright test --config playwright.live.config.ts)
}

# eval [--pack P] [--runs N] [--questions ID,...] [--url URL] [--out DIR] [--max-wait SECONDS]: the answer-quality
# eval (eval/README.md) on a running deployment, by default this host's UI. The optional grader reads
# GRADER_BASE_URL, GRADER_API_KEY and GRADER_MODEL from the environment only. Each question runs live and costs model calls.
cmd_eval() {
  local url="" args=() usage="usage: demo.sh eval [--pack P] [--runs N] [--questions ID,...] [--url URL] [--out DIR]"
  usage+=" [--max-wait SECONDS]"
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
    --entrypoint /opt/switchyard/tests/render-all.sh knowledge-foundation/switchyard:local
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
