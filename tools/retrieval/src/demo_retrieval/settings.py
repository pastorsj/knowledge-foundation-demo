# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The environment contract. Nothing else in the package reads configuration from the environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

BUILD_NVIDIA_URL = "https://integrate.api.nvidia.com/v1"
API_KEY_SECRET = Path("/run/secrets/retriever_api_key")
DEFAULT_RERANK_MODEL = "nvidia/llama-nemotron-rerank-vl-1b-v2"
PORT = 8320


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str = field(repr=False)
    embed_model: str
    rerank_model: str | None  # None: no reranker; hits keep their vector-search order
    rerank_url: str | None
    milvus_uri: str
    knowledge_dir: Path = Path("/knowledge")
    port: int = PORT

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        # Compose renders unset variables as "", so empty means "use the default"; RETRIEVER_RERANK_MODEL excepted.
        def get(name: str, default: str = "") -> str:
            return env.get(name) or default

        # The key is mandatory: an empty key would make LangChain fall back to NVIDIA_API_KEY.
        api_key = get("RETRIEVER_API_KEY") or (API_KEY_SECRET.read_text().strip() if API_KEY_SECRET.exists() else "")
        if not api_key:
            raise ValueError(f"RETRIEVER_API_KEY is not set (and {API_KEY_SECRET} does not exist)")
        # Set but empty turns reranking off (a gateway without NVIDIA's /ranking route); unset keeps the default.
        rerank_model = env.get("RETRIEVER_RERANK_MODEL", DEFAULT_RERANK_MODEL).strip() or None
        return cls(
            base_url=get("RETRIEVER_BASE_URL", BUILD_NVIDIA_URL),
            api_key=api_key,
            embed_model=get("RETRIEVER_EMBED_MODEL", "nvidia/nemotron-3-embed-1b"),
            rerank_model=rerank_model,
            rerank_url=get("RETRIEVER_RERANK_URL") or None,
            milvus_uri=get("MILVUS_URI", "http://milvus:19530"),
            knowledge_dir=Path(get("KNOWLEDGE_DIR", "/knowledge")),
            port=int(get("RETRIEVAL_PORT", str(PORT))),
        )
