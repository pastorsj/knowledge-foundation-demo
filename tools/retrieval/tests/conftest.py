# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Offline fixtures: a fake build.nvidia.com at the HTTP layer, Milvus Lite, and a small data pack."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import aiohttp
import pytest
import requests
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from requests.adapters import HTTPAdapter

from demo_retrieval import ingest
from demo_retrieval.datapack import CollectionManifest
from demo_retrieval.search import Retriever
from demo_retrieval.settings import Settings

API_KEY = "nvapi-test-dummy"
EMBED_URL = "https://integrate.api.nvidia.com/v1/embeddings"
RERANK_URL = "https://ai.api.nvidia.com/v1/retrieval/nvidia/llama-nemotron-rerank-vl-1b-v2/reranking"
DIMENSION = 64

TOPICS = [
    "cybersecurity incident",
    "quarterly earnings",
    "merger agreement",
    "executive departure",
    "share buyback",
    "credit facility",
    "product recall",
    "dividend increase",
    "auditor change",
    "stock split",
    "board appointment",
    "bankruptcy filing",
]
LONG_TEXT = " ".join(["Issuers file periodic reports on a fixed schedule."] * 200)  # splits into several chunks

SPANS = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(SPANS))
trace.set_tracer_provider(_provider)


@dataclass
class Call:
    url: str
    headers: dict[str, str]
    body: dict[str, Any]


@dataclass
class FakeNvidia:
    """Answers embeddings and reranking requests the way the NVIDIA APIs do, and records them."""

    calls: list[Call] = field(default_factory=list)
    failures: list[tuple[int, bytes] | Exception] = field(default_factory=list)  # answered before succeeding

    def fail(self, *failures: int | Exception, body: str = '{"title": "Injected failure"}') -> None:
        """Answer the next requests with these HTTP statuses (with `body`), or raise these transport errors."""
        self.failures += [f if isinstance(f, Exception) else (f, body.encode()) for f in failures]

    def to(self, url: str) -> list[Call]:
        return [call for call in self.calls if call.url == url]

    def reply(self, url: str, headers: Any, body: dict[str, Any]) -> tuple[int, bytes]:
        self.calls.append(Call(url, dict(headers), body))
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        if url.endswith("/embeddings"):
            data = [{"index": i, "embedding": embed(text)} for i, text in enumerate(body["input"])]
            return 200, json.dumps({"data": data}).encode()
        if url.endswith(("/reranking", "/ranking")):
            query = words(body["query"]["text"])
            scores = [len(query & words(passage["text"])) for passage in body["passages"]]
            order = sorted(range(len(scores)), key=lambda i: -scores[i])
            return 200, json.dumps({"rankings": [{"index": i, "logit": float(scores[i])} for i in order]}).encode()
        raise AssertionError(f"unexpected request to {url}")


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def embed(text: str) -> list[float]:
    """A hashed bag of words, so texts that share words get similar vectors."""
    vector = [0.0] * DIMENSION
    for word in words(text):
        vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIMENSION] += 1.0
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector]


@pytest.fixture(autouse=True)
def nvidia_api(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> FakeNvidia | None:
    """Every test talks to the fake, except @live tests, which talk to the real endpoints."""
    if request.node.get_closest_marker("live"):
        return None
    fake = FakeNvidia()

    def send(adapter: HTTPAdapter, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        response = requests.Response()
        response.status_code, response._content = fake.reply(request.url, request.headers, json.loads(request.body))
        response.url, response.request = request.url, request
        return response

    class AsyncResponse:
        def __init__(self, status: int, body: bytes) -> None:
            self.status, self.reason, self.headers = status, "", {}
            self._body = body

        async def read(self) -> bytes:
            return self._body

        async def text(self) -> str:
            return self._body.decode()

        def release(self) -> None:
            pass

    async def post(session: aiohttp.ClientSession, method: str, url: str, **kwargs: Any) -> AsyncResponse:
        return AsyncResponse(*fake.reply(str(url), kwargs["headers"], kwargs["json"]))

    monkeypatch.setattr(HTTPAdapter, "send", send)
    monkeypatch.setattr(aiohttp.ClientSession, "_request", post)
    return fake


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def spans() -> InMemorySpanExporter:
    SPANS.clear()
    return SPANS


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.from_env({"RETRIEVER_API_KEY": API_KEY, "MILVUS_URI": str(tmp_path / "milvus.db")})


def documents() -> list[dict[str, Any]]:
    rows = []
    for i, topic in enumerate(TOPICS):
        rows.append(
            {
                "document_id": f"edgar:{i}",
                "source_id": "market_news",
                "title": f"Current report: {topic}",
                "text": f"Issuer {i} reports a {topic} in its current report on Form 8-K.",
                "url": f"https://www.sec.gov/Archives/edgar/{i}.htm",
                "published_at": "2026-06-30T00:00:00Z",
                "metadata": {"form": "8-K", "cik": f"{i:010d}"},
            }
        )
        rows.append(
            {
                "document_id": f"ecfr:{i}",
                "source_id": "market_regulations",
                "title": f"17 CFR 229.{100 + i}",
                "text": f"Registrants must describe any {topic} in the required disclosure.",
                "url": f"https://www.ecfr.gov/current/title-17/section-229.{100 + i}",
                "metadata": {"part": "229", "section": f"229.{100 + i}"},
            }
        )
    rows.append(
        {
            "document_id": "ecfr:long",
            "source_id": "market_regulations",
            "title": "17 CFR 240.13a-13",
            "text": LONG_TEXT,
            "metadata": {"part": "240", "section": "240.13a-13"},
        }
    )
    return rows


def write_pack(data_dir: Path, rows: list[dict[str, Any]]) -> None:
    (data_dir / "corpus").mkdir(parents=True, exist_ok=True)
    pack = {
        "id": "test-pack",
        "sources": [
            {"id": "market_structured", "kind": "structured"},
            {"id": "market_news", "kind": "documents"},
            {"id": "market_regulations", "kind": "documents"},
        ],
        "documents": {"collection": "test_pack"},
    }
    (data_dir / "pack.json").write_text(json.dumps(pack))
    (data_dir / "corpus" / "documents.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    write_pack(tmp_path / "active", documents())
    return tmp_path / "active"


@pytest.fixture
def manifest(settings: Settings, data_dir: Path, nvidia_api: FakeNvidia) -> CollectionManifest:
    built = ingest.run(settings, data_dir)
    nvidia_api.calls.clear()
    return built


@pytest.fixture
async def retriever(settings: Settings, manifest: CollectionManifest) -> AsyncIterator[Retriever]:
    retriever = Retriever(settings, manifest.collection)
    yield retriever
    await retriever.close()
