# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Passage embeddings with NVIDIAEmbeddings, the client and pins of tools/retrieval.

The client always gets an explicit base_url and api_key: left unset, LangChain falls back to the global
NVIDIA_BASE_URL / NVIDIA_API_KEY, which in this stack belong to other services. Without a key nothing is sent.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from functools import cache
from typing import Literal

import aiohttp
import requests
from langchain_nvidia_ai_endpoints import Model
from langchain_nvidia_ai_endpoints import NVIDIAEmbeddings
from langchain_nvidia_ai_endpoints import register_model
from tenacity import before_sleep_log
from tenacity import retry
from tenacity import retry_if_exception
from tenacity import stop_after_attempt
from tenacity import wait_exponential_jitter

from .models import IngestError
from .settings import Settings

BATCH = 50  # texts per embeddings request
ATTEMPTS = 8

logger = logging.getLogger(__name__)


@cache  # registration is process-wide; registering the same model again only warns
def _register(
    model_id: str,
    model_type: Literal["embedding"],
    client: Literal["NVIDIAEmbeddings"],
    endpoint: str,
) -> None:
    register_model(Model(id=model_id, model_type=model_type, client=client, endpoint=endpoint))


def _is_transient(error: BaseException) -> bool:
    """Dropped connections, timeouts, throttling and server errors. Anything else fails at once."""
    if isinstance(error, (requests.ConnectionError, requests.Timeout, aiohttp.ClientError, TimeoutError)):
        return True
    # HTTP errors are plain Exceptions whose message starts with "[<status>]". The async client writes "[###]"
    # when the error body is not JSON, as a gateway's 502/503/504 page usually is.
    status = re.match(r"\[(\d{3}|###)\]", str(error))
    return status is not None and (status[1] in {"408", "429", "###"} or status[1].startswith("5"))


# Ingest sends many requests in a row; one that fails for good fails the file, which a re-upload retries.
@retry(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(ATTEMPTS),
    wait=wait_exponential_jitter(initial=0.5, max=8),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _embed_batch(client: NVIDIAEmbeddings, texts: list[str]) -> list[list[float]]:
    return client.embed_documents(texts)  # input_type=passage


class Embedder:
    def __init__(self, settings: Settings) -> None:
        self.model = settings.embed_model
        self._settings = settings
        self._client: NVIDIAEmbeddings | None = None

    def _get_client(self) -> NVIDIAEmbeddings:
        if self._client is None:
            if not self._settings.retriever_api_key:
                raise IngestError("embedding_failed", "RETRIEVER_API_KEY is not set, so documents cannot be embedded.")
            # nemotron-3-embed-1b is not in the 1.4.3 model table. Registering it skips the blocking
            # GET /v1/models lookup the client otherwise runs at construction.
            _register(self.model, "embedding", "NVIDIAEmbeddings", "{base_url}/embeddings")
            self._client = NVIDIAEmbeddings(
                model=self.model,
                base_url=self._settings.retriever_base_url,
                api_key=self._settings.retriever_api_key,
                truncate="END",
            )
        return self._client

    def embed_documents(
        self, texts: list[str], on_progress: Callable[[int, int], None] | None = None
    ) -> list[list[float]]:
        client = self._get_client()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            try:
                vectors.extend(_embed_batch(client, texts[start : start + BATCH]))
            except Exception as error:
                raise IngestError("embedding_failed", f"The embeddings endpoint failed: {error}"[:600]) from error
            if on_progress:
                on_progress(len(vectors), len(texts))
        return vectors
