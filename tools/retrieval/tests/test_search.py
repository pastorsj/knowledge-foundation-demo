# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace

import aiohttp
import pytest
from conftest import BOTH
from conftest import COLLECTION
from conftest import EMBED_URL
from conftest import MANUALS
from conftest import POLICIES
from conftest import RERANK_URL
from conftest import FakeNvidia
from conftest import chunks
from conftest import index
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from demo_retrieval import store
from demo_retrieval.search import Retriever
from demo_retrieval.settings import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
async def unranked(settings: Settings, indexed: str) -> AsyncIterator[Retriever]:
    """A retriever with RETRIEVER_RERANK_MODEL set empty: no reranker."""
    retriever = Retriever(replace(settings, rerank_model=None))
    yield retriever
    await retriever.close()


async def test_search_stays_inside_the_requested_source(retriever: Retriever, nvidia_api: FakeNvidia):
    result = await retriever.retrieve("restocking fee for returned furniture", [POLICIES], top_k=3)

    assert [hit.source_id for hit in result.hits] == [POLICIES] * 3
    top = result.hits[0]
    assert (top.rank, top.document_id, top.chunk_id) == (1, f"{POLICIES}:policy-0.pdf", f"{POLICIES}:policy-0.pdf:0001")
    assert top.metadata == {"file_name": "policy-0.pdf", "page": 1}
    assert (top.title, top.url, top.published_at) == ("Policy 0", None, None)
    assert "restocking fee for returned furniture" in top.snippet
    assert result.candidate_counts == {POLICIES: 12}

    (query,) = nvidia_api.to(EMBED_URL)
    assert query.body["input_type"] == "query" and query.body["model"] == "nvidia/nemotron-3-embed-1b"


async def test_all_sources_are_reranked_together_in_one_request(retriever: Retriever, nvidia_api: FakeNvidia):
    result = await retriever.retrieve("how staff apply the gift card refund rules", BOTH, top_k=8)

    (rerank,) = nvidia_api.to(RERANK_URL)
    assert len(rerank.body["passages"]) == sum(result.candidate_counts.values())
    assert all(passage.keys() == {"text"} for passage in rerank.body["passages"])  # no metadata leaves the service
    assert result.candidate_counts == {MANUALS: 12, POLICIES: 13}
    assert {hit.source_id for hit in result.hits} == set(BOTH)
    assert result.hits[0].document_id == f"{MANUALS}:manual-4.docx"
    assert [hit.rank for hit in result.hits] == list(range(1, 9))
    assert [hit.score for hit in result.hits] == sorted((hit.score for hit in result.hits), reverse=True)


async def test_without_a_rerank_model_hits_are_ordered_by_vector_score(
    unranked: Retriever, nvidia_api: FakeNvidia, spans: InMemorySpanExporter
):
    result = await unranked.retrieve("gift card refund rules", BOTH, top_k=5)

    assert nvidia_api.to(RERANK_URL) == []
    assert result.models.rerank is None
    assert result.timings.rerank_ms == 0
    assert len(result.hits) == 5
    assert [hit.score for hit in result.hits] == [hit.vector_score for hit in result.hits]
    assert [hit.vector_score for hit in result.hits] == sorted((h.vector_score for h in result.hits), reverse=True)
    assert {hit.document_id for hit in result.hits[:2]} == {f"{POLICIES}:policy-4.pdf", f"{MANUALS}:manual-4.docx"}
    assert "rerank" not in {span.name for span in spans.get_finished_spans()}


@pytest.mark.parametrize(
    ("source_ids", "top_k", "limits"),
    [
        (BOTH, 3, [12, 12]),
        (BOTH, 25, [100, 100]),
        ([*BOTH, "workspace.documents"], 25, [66, 66, 66]),  # the third source is empty; only its share matters
    ],
    ids=["four-per-hit", "largest-top-k", "capped-by-one-rerank-request"],
)
async def test_every_source_gets_the_same_share_of_one_rerank_request(
    retriever: Retriever, monkeypatch: pytest.MonkeyPatch, source_ids: list[str], top_k: int, limits: list[int]
):
    requested = []
    search = store.search

    async def spy(client, collection, vector, source_id, limit):
        requested.append(limit)
        return await search(client, collection, vector, source_id, limit)

    monkeypatch.setattr(store, "search", spy)
    await retriever.retrieve("store opening hours", source_ids, top_k)

    assert requested == limits


async def test_result_describes_models_index_and_timings(retriever: Retriever):
    result = await retriever.retrieve("supplier payment terms", BOTH, top_k=2)

    assert result.collection == "knowledge"
    assert result.collection_version == COLLECTION
    assert result.source_ids == BOTH
    assert result.models.model_dump() == {
        "embed": "nvidia/nemotron-3-embed-1b",
        "rerank": "nvidia/llama-nemotron-rerank-vl-1b-v2",
    }
    assert result.index.model_dump() == {
        "type": "HNSW",
        "metric": "COSINE",
        "params": {"M": 16, "efConstruction": 200},
        "search_params": {"ef": 128},
    }
    timings = result.timings
    assert timings.total_ms >= timings.embed_ms + timings.search_ms + timings.rerank_ms - 0.3


async def test_embed_search_and_rerank_are_traced(retriever: Retriever, spans: InMemorySpanExporter):
    await retriever.retrieve("annual inventory count", BOTH, top_k=2)

    traced = {span.name: span.attributes for span in spans.get_finished_spans()}
    assert {name: attributes["openinference.span.kind"] for name, attributes in traced.items()} == {
        "embed": "EMBEDDING",
        "search": "RETRIEVER",
        "rerank": "RERANKER",
    }
    assert traced["embed"]["embedding.model_name"] == "nvidia/nemotron-3-embed-1b"
    assert traced["rerank"]["reranker.output_documents.0.document.id"] in {
        f"{POLICIES}:policy-10.pdf:0001",
        f"{MANUALS}:manual-10.docx:0001",
    }


async def test_a_new_collection_behind_the_alias_is_searched_without_a_restart(
    retriever: Retriever, settings: Settings
):
    """The ingest service re-points the alias (a re-embed); the next call searches the new collection."""
    index(settings.milvus_uri, "knowledge__v2", chunks()[:4])

    result = await retriever.retrieve("store opening hours", BOTH, top_k=8)

    assert result.collection_version == "knowledge__v2"
    assert result.candidate_counts == {MANUALS: 2, POLICIES: 2}


async def test_rate_limits_and_server_errors_are_retried(retriever: Retriever, nvidia_api: FakeNvidia):
    nvidia_api.fail(429, 503)

    result = await retriever.retrieve("employee discount eligibility", [POLICIES], top_k=1)

    assert result.hits[0].document_id == f"{POLICIES}:policy-7.pdf"
    assert len(nvidia_api.to(EMBED_URL)) == 3


async def test_dropped_connections_and_gateway_error_pages_are_retried(retriever: Retriever, nvidia_api: FakeNvidia):
    # The async client reports a non-JSON error body as "[###] Unknown Error", without the status.
    nvidia_api.fail(aiohttp.ServerDisconnectedError(), 502, body="<html><body>502 Bad Gateway</body></html>")

    result = await retriever.retrieve("employee discount eligibility", [POLICIES], top_k=1)

    assert result.hits[0].document_id == f"{POLICIES}:policy-7.pdf"
    assert len(nvidia_api.to(EMBED_URL)) == 3


async def test_client_errors_are_not_retried(retriever: Retriever, nvidia_api: FakeNvidia):
    nvidia_api.fail(401)

    with pytest.raises(Exception, match=r"^\[401\]"):
        await retriever.retrieve("employee discount eligibility", [POLICIES], top_k=1)
    assert len(nvidia_api.to(EMBED_URL)) == 1
