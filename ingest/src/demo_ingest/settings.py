# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The environment contract. Nothing else in the package reads configuration from the environment.

Secrets come from an environment variable, else from a Compose secret file in ``/run/secrets``. NVIDIA_API_KEY and
NVIDIA_BASE_URL are never read: every client gets an explicit base URL and key.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

BUILD_NVIDIA_URL = "https://integrate.api.nvidia.com/v1"
SECRETS_DIR = Path("/run/secrets")
IMAGE_ROOT = Path("/opt/demo-ingest")
# In a source checkout the contracts sit at the repo root; the image copies them under /opt/demo-ingest.
_REPO = Path(__file__).resolve().parents[3]


def _first_existing(*candidates: Path) -> Path:
    return next((path for path in candidates if path.exists()), candidates[-1])


@dataclass(frozen=True)
class Settings:
    knowledge_dir: Path = Path("/knowledge")
    packs_dir: Path = Path("/packs")
    milvus_uri: str = "http://milvus:19530"
    collection_alias: str = "knowledge"
    # Empty disables Nemotron Parse: PDFs then use their text layer and images fail.
    parse_base_url: str = "http://parse:8000/v1"
    parse_model: str = "nvidia/NVIDIA-Nemotron-Parse-2.0"
    parse_api_key: str | None = field(default=None, repr=False)
    retriever_base_url: str = BUILD_NVIDIA_URL
    retriever_api_key: str | None = field(default=None, repr=False)
    embed_model: str = "nvidia/nemotron-3-embed-1b"
    workers: int = 2
    max_file_mb: int = 100
    max_files: int = 20
    parse_concurrency: int = 4
    # Parse's output cap per page. The local vLLM serves a 9000-token context, so 8192 fits. build.nvidia.com serves
    # nvidia/nemotron-parse-2.0 with a 4096-token context that also holds the prompt (6 tokens; the image does not
    # count), so it refuses 4091 or more with HTTP 400: set 4000 there.
    parse_max_tokens: int = 8192
    stage_timeout_s: float = 1800
    # The embed model's Hugging Face tokenizer, for the chunker's token counts.
    tokenizer_dir: Path = IMAGE_ROOT / "tokenizer"
    catalog_schema_dir: Path = field(
        default_factory=lambda: _first_existing(IMAGE_ROOT / "contracts" / "catalog", _REPO / "contracts" / "catalog")
    )
    pack_schema_dir: Path = field(
        default_factory=lambda: _first_existing(IMAGE_ROOT / "schemas", _REPO / "data" / "schemas")
    )

    @property
    def parse_enabled(self) -> bool:
        return bool(self.parse_base_url)

    @property
    def db_path(self) -> Path:
        return self.knowledge_dir / "ingest" / "ingest.sqlite3"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ, *, secrets_dir: Path = SECRETS_DIR) -> Settings:
        defaults = cls()

        # Compose renders unset variables as "", so empty means "use the default" (except PARSE_BASE_URL).
        def get(name: str, default: str) -> str:
            return env.get(name) or default

        def secret(name: str) -> str | None:
            path = secrets_dir / name.lower()
            value = env.get(name) or (path.read_text().strip() if path.is_file() else "")
            return value or None

        return cls(
            knowledge_dir=Path(get("KNOWLEDGE_DIR", str(defaults.knowledge_dir))),
            packs_dir=Path(get("PACKS_DIR", str(defaults.packs_dir))),
            milvus_uri=get("MILVUS_URI", defaults.milvus_uri),
            parse_base_url=env.get("PARSE_BASE_URL", defaults.parse_base_url).rstrip("/"),
            parse_model=get("PARSE_MODEL", defaults.parse_model),
            parse_api_key=secret("PARSE_API_KEY"),
            retriever_base_url=get("RETRIEVER_BASE_URL", defaults.retriever_base_url).rstrip("/"),
            retriever_api_key=secret("RETRIEVER_API_KEY"),
            embed_model=get("RETRIEVER_EMBED_MODEL", defaults.embed_model),
            workers=int(get("INGEST_WORKERS", str(defaults.workers))),
            max_file_mb=int(get("INGEST_MAX_FILE_MB", str(defaults.max_file_mb))),
            max_files=int(get("INGEST_MAX_FILES", str(defaults.max_files))),
            parse_concurrency=int(get("PARSE_CONCURRENCY", str(defaults.parse_concurrency))),
            parse_max_tokens=int(get("PARSE_MAX_TOKENS", str(defaults.parse_max_tokens))),
            stage_timeout_s=float(get("INGEST_STAGE_TIMEOUT_SECONDS", str(defaults.stage_timeout_s))),
            tokenizer_dir=Path(get("INGEST_TOKENIZER_DIR", str(defaults.tokenizer_dir))),
            catalog_schema_dir=Path(get("CATALOG_SCHEMA_DIR", str(defaults.catalog_schema_dir))),
            pack_schema_dir=Path(get("PACK_SCHEMA_DIR", str(defaults.pack_schema_dir))),
        )
