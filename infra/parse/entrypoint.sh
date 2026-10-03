#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Serve NVIDIA Nemotron Parse 2.0 the way its model card prescribes
# (https://huggingface.co/nvidia/NVIDIA-Nemotron-Parse-2.0, "vLLM serve"):
# fetch the card's runtime patch from the pinned revision, put it on PYTHONPATH so vLLM ties the output
# head to the decoder embeddings, then `vllm serve` with the card's flags. max_tokens per request must
# stay below the served context (9,000); the ingest service asks for 8,192.
set -eu

: "${PARSE_REVISION:?}"
model=nvidia/NVIDIA-Nemotron-Parse-2.0

patch_root=$(python3 - <<PY
from huggingface_hub import snapshot_download
print(snapshot_download("$model", revision="$PARSE_REVISION", allow_patterns="vllm_tied_patch/sitecustomize.py"))
PY
)
export PYTHONPATH="${patch_root}/vllm_tied_patch${PYTHONPATH:+:$PYTHONPATH}"

exec vllm serve "$model" \
  --revision "$PARSE_REVISION" \
  --dtype bfloat16 \
  --max-num-seqs 8 \
  --limit-mm-per-prompt '{"image": 1}' \
  --trust-remote-code \
  --gpu-memory-utilization "${PARSE_GPU_MEMORY_UTILIZATION:-0.15}" \
  --port 8000
