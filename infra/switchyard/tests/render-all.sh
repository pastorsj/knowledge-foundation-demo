#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Render every routes template with sample settings through entrypoint.sh, validate each with the
# real switchyard-server (--dry-run), and check that bad configuration exits 64. Offline, with a
# dummy key. Needs switchyard-server and envsubst on PATH, so run it in the image:
#
#   docker run --rm -v "$PWD/infra/switchyard/tests:/opt/switchyard/tests:ro" \
#     --entrypoint /opt/switchyard/tests/render-all.sh knowledge-foundation/switchyard:local
#
# or locally with a switchyard-server 0.3.0 binary:
#
#   PATH=/dir/with/switchyard-server:$PATH infra/switchyard/tests/render-all.sh
#
# shellcheck disable=SC2016,SC2329 # '${' patterns are literal; helpers run through expect
set -eu

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
failed=0

# In the image, dry runs open the real routing log, which proves uid 1000 can write it.
state_dir=/var/lib/switchyard
[ -w "$state_dir" ] || state_dir=$work/state

# run_entrypoint TEMPLATE [NAME=value...]: run `entrypoint.sh --dry-run` with only the sample
# settings for the template's endpoint plus the overrides. Nothing from the caller's
# environment leaks in. Output goes to $work/out, the rendered config to $work/routes.toml.
run_entrypoint() {
  template=$1
  shift
  case $template in
    *.nemotron-gpt)
      set -- INFERENCE_BASE_URL=https://integrate.api.nvidia.com/v1 \
        CAPABLE_BASE_URL=https://capable.example.com/v1 CAPABLE_API_KEY=dummy-capable-not-a-key \
        AGENT_EFFICIENT_MODEL=nvidia/nemotron-3-ultra-550b-a55b \
        AGENT_CAPABLE_MODEL=gpt-6-sol AGENT_JUDGE_MODEL=judge/gpt-6-sol \
        AGENT_AUX_MODEL=nvidia/nemotron-3-super-120b-a12b "$@"
      ;;
    *.nemotron-claude)
      set -- INFERENCE_BASE_URL=https://integrate.api.nvidia.com/v1 \
        CAPABLE_BASE_URL=https://capable.example.com/v1 CAPABLE_API_KEY=dummy-capable-not-a-key \
        AGENT_EFFICIENT_MODEL=nvidia/nemotron-3-ultra-550b-a55b \
        AGENT_CAPABLE_MODEL=claude-opus-5-5 AGENT_JUDGE_MODEL=judge/claude-opus-5-5 \
        AGENT_AUX_MODEL=nvidia/nemotron-3-super-120b-a12b "$@"
      ;;
    *)
      set -- INFERENCE_BASE_URL=https://integrate.api.nvidia.com/v1 \
        AGENT_EFFICIENT_MODEL=nvidia/nemotron-3-super-120b-a12b \
        AGENT_CAPABLE_MODEL=nvidia/nemotron-3-ultra-550b-a55b \
        AGENT_JUDGE_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b \
        AGENT_AUX_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b "$@"
      ;;
  esac
  env -i PATH="$PATH" TMPDIR="$work" SWITCHYARD_STATE_DIR="$state_dir" \
    INFERENCE_API_KEY_FILE="$work/secret" INFERENCE_API_KEY=dummy-not-a-key \
    CAPABLE_API_KEY_FILE="$work/capable-secret" \
    SWITCHYARD_ROUTES="$template" SWITCHYARD_CONFIRMATIONS=1 "$@" \
    "$root/entrypoint.sh" --dry-run >"$work/out" 2>&1
}

# exits STATUS TEMPLATE [NAME=value...]: succeed when run_entrypoint exits with STATUS.
exits() {
  want=$1
  shift
  if run_entrypoint "$@"; then got=0; else got=$?; fi
  [ "$got" = "$want" ] && return
  echo "    exit $got, want $want:"
  sed 's/^/    /' "$work/out"
  return 1
}

# expect DESCRIPTION COMMAND...: report whether COMMAND succeeds.
expect() {
  description=$1
  shift
  if "$@"; then
    echo "ok - $description"
  else
    echo "not ok - $description"
    failed=1
  fi
}

not() { ! "$@"; }

all_routes=$(printf '%s\n' knowledge knowledge-aux knowledge-capable knowledge-efficient \
  knowledge-fallback)

for path in "$root"/routes/*.toml.tmpl; do
  name=$(basename "$path" .toml.tmpl)
  used=$(grep -o '\${[A-Z_]*}' "$path" | tr -d '${}' | grep -vx JUDGE_PROMPT | sort -u)
  declared=$(sed -n 's/^# requires://p' "$path" | tr ' ' '\n' | sed '/^$/d' | sort -u)
  expect "$name: '# requires:' lists exactly the settings it substitutes" [ "$used" = "$declared" ]

  expect "$name: renders and passes --dry-run" exits 0 "$name"
  expect "$name: leaves no placeholder" not grep -q '\${' "$work/routes.toml"
  served=$(sed -n 's/^server OK: //p' "$work/out" | head -n 1 | tr -d ' ' | tr ',' '\n' | sort)
  # passthrough has no capable model, so no knowledge-capable route.
  case $name in
    passthrough.*) want_routes=$(echo "$all_routes" | grep -vx knowledge-capable) ;;
    *) want_routes=$all_routes ;;
  esac
  expect "$name: serves its knowledge routes" [ "$served" = "$want_routes" ]
done

expect "the judge prompt is inlined without its comment block" exits 0 escalation.nemotron-gpt
expect "  ...its text is present" grep -q 'escalation judge for an enterprise knowledge assistant' "$work/routes.toml"
expect "  ...its comment is not" not grep -q -e '<!--' -e 'inlines everything' "$work/routes.toml"
expect "SWITCHYARD_CONFIRMATIONS=2 is rendered" exits 0 escalation.nemotron SWITCHYARD_CONFIRMATIONS=2
expect "  ...into the escalation block" grep -q 'confirmations = 2,' "$work/routes.toml"
expect "templates without a judge ignore an empty SWITCHYARD_CONFIRMATIONS" \
  exits 0 passthrough.nemotron SWITCHYARD_CONFIRMATIONS=

printf dummy-from-secret-file >"$work/secret"
expect "the key is read from the secret file" exits 0 escalation.nemotron-gpt INFERENCE_API_KEY=
: >"$work/secret"
expect "the secret file wins over the environment" exits 64 escalation.nemotron-gpt
rm "$work/secret"

# The capable model's endpoint defaults to the inference endpoint, and its key to the inference
# key on that endpoint only.
expect "the capable endpoint defaults to the inference endpoint" exits 0 escalation.nemotron CAPABLE_BASE_URL=
expect "  ...in the capable client" [ "$(grep -c 'base_url = "https://integrate.api.nvidia.com/v1"' "$work/routes.toml")" = 3 ]
expect "  ...and with the inference key" grep -q 'on the inference endpoint with the inference key' "$work/out"
expect "a separate capable endpoint is rendered" exits 0 escalation.nemotron-gpt
expect "  ...into the Responses and judge clients only" \
  [ "$(grep -c 'base_url = "https://capable.example.com/v1"' "$work/routes.toml")" = 2 ]
expect "  ...which reads CAPABLE_API_KEY" grep -q 'api_key_env = "CAPABLE_API_KEY"' "$work/routes.toml"
printf dummy-capable-key >"$work/capable-secret"
expect "the capable key is read from its secret file" exits 0 pinned-capable.nemotron-gpt
expect "  ...and reported as its own" grep -q 'with its own key' "$work/out"
: >"$work/capable-secret"
expect "an empty capable secret file for another endpoint exits 64" exits 64 pinned-capable.nemotron-gpt
expect "  ...rather than send the inference key there" grep -q 'set CAPABLE_API_KEY' "$work/out"
expect "an empty capable secret file on the inference endpoint means the inference key" \
  exits 0 pinned-capable.nemotron-gpt CAPABLE_BASE_URL=
expect "  ...and is reported so" grep -q 'on the inference endpoint with the inference key' "$work/out"
expect "a capable endpoint equal to the inference endpoint also takes the inference key" \
  exits 0 pinned-capable.nemotron-gpt CAPABLE_BASE_URL=https://integrate.api.nvidia.com/v1
expect "passthrough needs no capable key, whatever the capable endpoint" \
  exits 0 passthrough.nemotron CAPABLE_BASE_URL=https://capable.example.com/v1
rm "$work/capable-secret"
expect "an empty CAPABLE_API_KEY for another endpoint exits 64" exits 64 escalation.nemotron-gpt CAPABLE_API_KEY=

# Claude speaks the Anthropic Messages API: its clients have that format, and its targets carry no
# Responses-only option (Switchyard rejects reasoning_effort on that client).
messages_client_on_capable_endpoint() {
  grep -A1 '^format = "anthropic_messages"' "$work/routes.toml" | grep -q 'base_url = "https://capable.example.com/v1"'
}
# section NAME: the rendered TOML section [NAME], up to the next section.
section() {
  awk -v want="[$1]" '$1 == want { on = 1; print; next } /^\[/ { on = 0 } on' "$work/routes.toml"
}
# has SECTION TEXT: the rendered section [SECTION] contains TEXT.
has() { section "$1" | grep -qF "$2"; }
# The frontier escalation templates judge with a model from the capable endpoint, on a client of its own.
for name in escalation.nemotron-gpt escalation.nemotron-claude; do
  expect "$name: the judge is AGENT_JUDGE_MODEL" exits 0 "$name"
  expect "  ...on its own client" has targets.judge 'llm_client = "judge"'
  expect "  ...which is on the capable endpoint" has llm_clients.judge 'base_url = "https://capable.example.com/v1"'
  expect "  ...with the capable key" has llm_clients.judge 'api_key_env = "CAPABLE_API_KEY"'
  expect "  ...without Nemotron's chat_template_kwargs" not has targets.judge chat_template_kwargs
  expect "  ...and a Nemotron aux model serves auxiliary and fallback calls" \
    has targets.aux 'id = "nvidia/nemotron-3-super-120b-a12b"'
done
expect "escalation.nemotron-gpt: the judge uses the Responses API" exits 0 escalation.nemotron-gpt
expect "  ...with low effort" has targets.judge 'reasoning_effort = "low"'
expect "  ...in the Responses format" has llm_clients.judge 'format = "openai_responses"'
expect "escalation.nemotron-claude: the judge uses the Anthropic Messages API" exits 0 escalation.nemotron-claude
expect "  ...in the Messages format" has llm_clients.judge 'format = "anthropic_messages"'
for name in escalation.nemotron-claude pinned-capable.nemotron-claude; do
  expect "$name: the capable model uses the Anthropic Messages client" exits 0 "$name"
  expect "  ...on the capable endpoint" messages_client_on_capable_endpoint
  expect "  ...without reasoning_effort or store" not grep -q -e '^reasoning_effort' -e 'store = ' "$work/routes.toml"
  expect "  ...with an empty CAPABLE_API_KEY for another endpoint, exits 64" exits 64 "$name" CAPABLE_API_KEY=
done
expect "passthrough needs no capable model" exits 0 passthrough.nemotron AGENT_CAPABLE_MODEL=
expect "  ...and renders none" not grep -q -e 'CAPABLE' -e 'targets.capable' "$work/routes.toml"

for name in SWITCHYARD_ROUTES INFERENCE_BASE_URL INFERENCE_API_KEY AGENT_EFFICIENT_MODEL \
  AGENT_CAPABLE_MODEL AGENT_JUDGE_MODEL AGENT_AUX_MODEL SWITCHYARD_CONFIRMATIONS; do
  expect "empty $name exits 64" exits 64 escalation.nemotron-gpt "$name="
done
expect "empty INFERENCE_BASE_URL exits 64 even with CAPABLE_BASE_URL set" \
  exits 64 escalation.nemotron INFERENCE_BASE_URL= CAPABLE_BASE_URL=https://capable.example.com/v1
expect "an unknown template exits 64" exits 64 escalation.nemotron-gpt SWITCHYARD_ROUTES=no-such-template
expect "efficient = capable exits 64" \
  exits 64 escalation.nemotron-gpt AGENT_CAPABLE_MODEL=nvidia/nemotron-3-ultra-550b-a55b
expect "efficient = judge exits 64" \
  exits 64 escalation.nemotron-gpt AGENT_JUDGE_MODEL=nvidia/nemotron-3-ultra-550b-a55b
expect "capable = judge exits 64" exits 64 escalation.nemotron AGENT_JUDGE_MODEL=nvidia/nemotron-3-ultra-550b-a55b
expect "passthrough: efficient = aux exits 64" \
  exits 64 passthrough.nemotron AGENT_AUX_MODEL=nvidia/nemotron-3-super-120b-a12b
expect "escalation.nemotron-gpt: efficient = aux exits 64" \
  exits 64 escalation.nemotron-gpt AGENT_AUX_MODEL=nvidia/nemotron-3-ultra-550b-a55b
expect "passthrough needs no judge model" exits 0 passthrough.nemotron AGENT_JUDGE_MODEL=
expect "the aux model may be the judge model (the all-Nemotron default)" exits 0 escalation.nemotron
expect "  ...which serves the aux route" grep -q 'id = "nvidia/nemotron-3.5-lightning-30b-a3b"' "$work/routes.toml"
expect "passthrough ignores the unused capable model" \
  exits 0 passthrough.nemotron AGENT_CAPABLE_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
for value in 0 3 two; do
  expect "SWITCHYARD_CONFIRMATIONS=$value exits 64" exits 64 escalation.nemotron-gpt "SWITCHYARD_CONFIRMATIONS=$value"
done
expect "a base URL without a scheme fails --dry-run" \
  exits 1 escalation.nemotron-gpt INFERENCE_BASE_URL=integrate.api.nvidia.com/v1
expect "a capable base URL without a scheme fails --dry-run" \
  exits 1 escalation.nemotron-gpt CAPABLE_BASE_URL=capable.example.com/v1

exit "$failed"
