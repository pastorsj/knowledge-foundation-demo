# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# The catalog contracts and the pack schemas come from named build contexts:
#   docker build --build-context contracts=contracts/catalog --build-context packschemas=data/schemas \
#     -t knowledge-foundation/ingest:local ingest
ARG PYTHON_IMAGE=python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

FROM ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /opt/demo-ingest
# Dependencies first, so source and README edits reuse the cached layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
# The embed model's tokenizer (files only, no weights), so chunks are sized in the model's own tokens offline.
ARG EMBED_TOKENIZER=nvidia/Nemotron-3-Embed-1B-BF16
ARG EMBED_TOKENIZER_REVISION=c0c9fea93ea424587517f2c59e20db9f1d6bf615
RUN .venv/bin/python -c "import sys; from huggingface_hub import snapshot_download; \
snapshot_download(sys.argv[1], revision=sys.argv[2], local_dir='/opt/demo-ingest/tokenizer', \
allow_patterns=['tokenizer*', 'special_tokens_map.json', '*.model'])" "$EMBED_TOKENIZER" "$EMBED_TOKENIZER_REVISION" \
    && rm -rf /opt/demo-ingest/tokenizer/.cache \
    && test -f /opt/demo-ingest/tokenizer/tokenizer.json
# DuckDB's excel extension (read_xlsx), installed where the runtime user can load it without a network.
RUN .venv/bin/python -c "import duckdb; duckdb.connect(config={'extension_directory': \
'/opt/demo-ingest/duckdb-extensions'}).execute('INSTALL excel')"
COPY README.md ./
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

FROM ${PYTHON_IMAGE}
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home-dir /nonexistent app \
    && install -d -o app -g app /knowledge
COPY --from=build /opt/demo-ingest/.venv /opt/demo-ingest/.venv
COPY --from=build /opt/demo-ingest/tokenizer /opt/demo-ingest/tokenizer
COPY --from=build /opt/demo-ingest/duckdb-extensions /opt/demo-ingest/duckdb-extensions
COPY --from=contracts source-manifest.schema.json pack-manifest.schema.json /opt/demo-ingest/contracts/catalog/
COPY --from=packschemas pack.schema.json questions.schema.json /opt/demo-ingest/schemas/
ENV PATH=/opt/demo-ingest/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    KNOWLEDGE_DIR=/knowledge \
    CATALOG_SCHEMA_DIR=/opt/demo-ingest/contracts/catalog \
    PACK_SCHEMA_DIR=/opt/demo-ingest/schemas \
    INGEST_TOKENIZER_DIR=/opt/demo-ingest/tokenizer \
    DUCKDB_EXTENSION_DIRECTORY=/opt/demo-ingest/duckdb-extensions \
    HF_HUB_OFFLINE=1 \
    NVIDIA_USAGE_TELEMETRY_ENABLED=false
USER 10001:10001
# The knowledge catalog; mount the knowledge volume here. A new volume inherits the app user as owner.
VOLUME /knowledge
# Listens on :8330. No EXPOSE (AGENTS.md: images must build reproducibly).
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8330/health', timeout=4)"]
CMD ["demo-ingest", "serve"]
