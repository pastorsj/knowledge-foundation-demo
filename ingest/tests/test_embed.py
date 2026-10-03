# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
from dataclasses import replace
from typing import Any

import pytest
import requests
from conftest import embed
from requests.adapters import HTTPAdapter
from tenacity import wait_none

from demo_ingest import embed as embedding
from demo_ingest.models import IngestError
from demo_ingest.settings import Settings

EMBED_URL = "https://integrate.api.nvidia.com/v1/embeddings"


class FakeEndpoint:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        self.failures: list[int | Exception] = []

    def send(self, request: requests.PreparedRequest) -> requests.Response:
        body = json.loads(request.body)
        self.calls.append((request.url, dict(request.headers), body))
        response = requests.Response()
        response.url, response.request = request.url, request
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            response.status_code, response._content = failure, b'{"title": "Injected failure"}'
            return response
        data = [{"index": i, "embedding": embed(text)} for i, text in enumerate(body["input"])]
        response.status_code, response._content = 200, json.dumps({"data": data}).encode()
        return response


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> FakeEndpoint:
    fake = FakeEndpoint()

    def send(adapter: HTTPAdapter, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        return fake.send(request)

    monkeypatch.setattr(HTTPAdapter, "send", send)
    return fake


def test_passages_are_embedded_in_batches_of_50(settings: Settings, endpoint: FakeEndpoint):
    progress: list[tuple[int, int]] = []
    texts = [f"passage {i}" for i in range(120)]

    vectors = embedding.Embedder(settings).embed_documents(texts, on_progress=lambda k, n: progress.append((k, n)))

    assert len(vectors) == 120 and len(vectors[0]) == 2048
    assert [len(body["input"]) for _, _, body in endpoint.calls] == [50, 50, 20]
    assert all(url == EMBED_URL for url, _, _ in endpoint.calls)
    _, headers, body = endpoint.calls[0]
    assert (body["model"], body["input_type"], body["truncate"]) == ("nvidia/nemotron-3-embed-1b", "passage", "END")
    assert headers["Authorization"] == "Bearer nvapi-test-dummy"
    assert progress == [(50, 120), (100, 120), (120, 120)]


def test_transient_errors_are_retried(settings: Settings, endpoint: FakeEndpoint, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(embedding, "_embed_batch", embedding._embed_batch.retry_with(wait=wait_none()))
    endpoint.failures = [requests.ConnectionError("reset"), 429, 500, 503]

    vectors = embedding.Embedder(settings).embed_documents(["a", "b"])

    assert len(vectors) == 2 and len(endpoint.calls) == 5


def test_a_permanent_error_fails_the_file(settings: Settings, endpoint: FakeEndpoint):
    endpoint.failures = [401]

    with pytest.raises(IngestError) as error:
        embedding.Embedder(settings).embed_documents(["a"])

    assert error.value.code == "embedding_failed"
    assert len(endpoint.calls) == 1


def test_without_a_key_nothing_is_sent(settings: Settings, endpoint: FakeEndpoint, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-must-not-be-used")

    with pytest.raises(IngestError) as error:
        embedding.Embedder(replace(settings, retriever_api_key=None)).embed_documents(["a"])

    assert error.value.code == "embedding_failed" and "RETRIEVER_API_KEY" in error.value.message
    assert endpoint.calls == []


@pytest.mark.live
def test_live_embedding(settings: Settings):
    key = os.environ.get("RETRIEVER_API_KEY")
    if not key:
        pytest.skip("RETRIEVER_API_KEY is not set")
    vectors = embedding.Embedder(replace(settings, retriever_api_key=key)).embed_documents(["hello"])
    assert len(vectors[0]) == 2048
