# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Offline fixtures: a fake build.nvidia.com at the HTTP layer, Milvus Lite, and a small knowledge catalog.

The catalog is the shared contract's fixture (contracts/fixtures/catalog, copied into tests/fixtures) plus a few
sources derived from it. The ingest service owns the Milvus collection in the stack; here the tests write it the
way ingest does: one collection, `knowledge__v1`, behind the alias `knowledge`, with store.create_collection's schema.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
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
from pymilvus import MilvusClient
from requests.adapters import HTTPAdapter

from demo_retrieval import store
from demo_retrieval.search import Retriever
from demo_retrieval.settings import Settings

API_KEY = "nvapi-test-dummy"
EMBED_URL = "https://integrate.api.nvidia.com/v1/embeddings"
RERANK_URL = "https://ai.api.nvidia.com/v1/retrieval/nvidia/llama-nemotron-rerank-vl-1b-v2/reranking"
DIMENSION = 64
FIXTURES = Path(__file__).parent / "fixtures" / "catalog"
COLLECTION = "knowledge__v1"

TOPICS = [
    "restocking fee for returned furniture",
    "store opening hours on public holidays",
    "loss prevention incident report",
    "supplier payment terms and invoices",
    "gift card refund rules",
    "price matching a competitor",
    "damaged goods on delivery",
    "employee discount eligibility",
    "holiday staffing rota",
    "cash handling at closing",
    "annual inventory count",
    "product recall procedure",
]
POLICIES = "retail.policies"
MANUALS = "retail.manuals"  # a second documents source, derived from retail.policies
BOTH = [MANUALS, POLICIES]

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


def fixture_source(source_id: str) -> dict[str, Any]:
    return json.loads((FIXTURES / "sources" / f"{source_id}.json").read_text())


def write_source(knowledge_dir: Path, manifest: dict[str, Any]) -> None:
    """Write a source manifest the way ingest does: to a temporary name, then renamed into place."""
    sources = knowledge_dir / "catalog" / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    staged = sources / f".{manifest['id']}.json.tmp"
    staged.write_text(json.dumps(manifest, indent=2))
    staged.replace(sources / f"{manifest['id']}.json")


def derived_source(source_id: str, **overrides: Any) -> dict[str, Any]:
    """retail.policies under another id, e.g. a second documents source or one in another status."""
    pack_id = source_id.split(".", maxsplit=1)[0]
    return {**fixture_source(POLICIES), "id": source_id, "pack_id": pack_id, "files": [], **overrides}


@pytest.fixture
def knowledge_dir(tmp_path: Path) -> Path:
    """retail.policies (documents, ready), retail.sales (structured), retail.manuals (documents, ready),
    retail.drafts (documents, empty), retail.archive (documents, failed), workspace.documents (documents, ingesting)."""
    root = tmp_path / "knowledge"
    (root / "catalog" / "sources").mkdir(parents=True)
    for path in (FIXTURES / "sources").glob("*.json"):
        shutil.copy(path, root / "catalog" / "sources" / path.name)
    write_source(root, derived_source(MANUALS, name="Store Manuals"))
    write_source(root, derived_source("retail.drafts", status="empty"))
    write_source(root, derived_source("retail.archive", status="failed"))
    write_source(root, derived_source("workspace.documents", status="ingesting"))
    return root


@pytest.fixture
def settings(tmp_path: Path, knowledge_dir: Path) -> Settings:
    return Settings.from_env(
        {"RETRIEVER_API_KEY": API_KEY, "MILVUS_URI": str(tmp_path / "milvus.db"), "KNOWLEDGE_DIR": str(knowledge_dir)}
    )


def chunk(source_id: str, document_id: str, number: int, title: str, text: str, **metadata: Any) -> dict[str, Any]:
    """One row as the ingest service writes it: the schema's columns plus dynamic metadata."""
    return {
        **metadata,
        "chunk_id": f"{document_id}:{number:04d}",
        "source_id": source_id,
        "document_id": document_id,
        "title": title,
        "url": None,
        "published_at": None,
        "text": text,
    }


def chunks() -> list[dict[str, Any]]:
    rows = []
    for i, topic in enumerate(TOPICS):
        rows.append(
            chunk(
                POLICIES,
                f"{POLICIES}:policy-{i}.pdf",
                1,
                f"Policy {i}",
                f"Northwind policy {i} sets the {topic} for every store.",
                file_name=f"policy-{i}.pdf",
                page=1,
            )
        )
        rows.append(
            chunk(
                MANUALS,
                f"{MANUALS}:manual-{i}.docx",
                1,
                f"Store manual {i}",
                f"The store manual explains how staff apply the {topic} at the till.",
                file_name=f"manual-{i}.docx",
            )
        )
    rows.append(chunk(POLICIES, f"{POLICIES}:policy-0.pdf", 2, "Policy 0", "Exceptions are listed on the next page."))
    return rows


def index(uri: str, collection: str, rows: list[dict[str, Any]], alias: str = store.ALIAS) -> None:
    """Write a collection and point the alias at it, as the ingest service does."""
    client = MilvusClient(uri=uri)
    try:
        store.create_collection(client, collection, dimension=DIMENSION)
        client.insert(collection, [{**row, store.VECTOR_FIELD: embed(row["text"])} for row in rows])
        if alias in client.list_aliases()["aliases"]:
            client.alter_alias(collection, alias)
        else:
            client.create_alias(collection, alias)
    finally:
        client.close()


@pytest.fixture
def indexed(settings: Settings) -> str:
    index(settings.milvus_uri, COLLECTION, chunks())
    return COLLECTION


@pytest.fixture
async def retriever(settings: Settings, indexed: str) -> AsyncIterator[Retriever]:
    retriever = Retriever(settings)
    yield retriever
    await retriever.close()
