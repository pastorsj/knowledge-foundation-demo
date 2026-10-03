#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Checks the UI's screenshot baselines (the `visual` Playwright project) in the official Playwright
# Docker image, whose version is the one in ui/package-lock.json, the way CI does.
#
#   e2e/visual/run.sh                 install and build in the container, then compare
#   e2e/visual/run.sh --update        the same, then write new baselines for the views that changed
#   e2e/visual/run.sh --reuse-build   compare using the host's node_modules and build (CI: a Linux
#                                     x86-64 host that ran `npm ci` and `npm run build`)
#
# Needs Docker and Node (to read the lockfile). Without --reuse-build, node_modules and .next live
# in container volumes, so a macOS checkout's own are neither used nor touched.
# Extra arguments go to `playwright test`, e.g. `-g 'Kumo'`.
set -euo pipefail

UI=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
ROOT=$(cd "$UI/.." && pwd)

update=() reuse=false args=()
for arg in "$@"; do
  case $arg in
    --update) update=(--update-snapshots) ;;
    --reuse-build) reuse=true ;;
    *) args+=("$arg") ;;
  esac
done

version=$(node -p "require('$UI/package-lock.json').packages['node_modules/@playwright/test'].version")
image="mcr.microsoft.com/playwright:v$version-noble"

volumes=(-v "$ROOT:/repo")
setup=
if ! $reuse; then
  # Linux node_modules and build of the container's own, over the checkout's
  volumes+=(-v /repo/ui/node_modules -v /repo/ui/.next)
  setup='npm ci --no-audit --no-fund && npm run build && '
fi

# Files written into the checkout get the caller's owner back (the container runs as root).
owner="$(id -u):$(id -g)"
script="${setup}E2E_VISUAL=1 npx playwright test --project=visual \"\$@\"; status=\$?
chown -R $owner e2e/visual/__screenshots__ test-results 2>/dev/null || true
exit \$status"

exec docker run --rm --init --ipc=host \
  --name "${VISUAL_CONTAINER_NAME:-ui-visual-$$}" \
  "${volumes[@]}" -w /repo/ui \
  -e CI -e HOME=/tmp -e NEXT_TELEMETRY_DISABLED=1 \
  -e FONTCONFIG_FILE=/repo/ui/e2e/visual/fonts.conf \
  "$image" bash -c "$script" visual ${update[@]+"${update[@]}"} ${args[@]+"${args[@]}"}
