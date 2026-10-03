# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import aiohttp
import pytest
from conftest import EMBED_URL
from conftest import RERANK_URL
from conftest import FakeNvidia
from conftest import documents
from conftest import write_pack
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from demo_retrieval import ingest
from demo_retrieval import store
from demo_retrieval.datapack import CollectionManifest
from demo_retrieval.search import Retriever
from demo_retrieval.settings import Settings

pytestmark = pytest.mark.anyio

BOTH = ["market_news", "market_regulations"]


async def test_search_stays_inside_the_requested_source(retriever: Retriever, nvidia_api: FakeNvidia):
    result = await retriever.retrieve("cybersecurity incident report", ["market_news"], top_k=3)

    assert [hit.source_id for hit in result.hits] == ["market_news"] * 3
    top = result.hits[0]
    assert (top.rank, top.document_id, top.chunk_id) == (1, "edgar:0", "edgar:0:0001")
    assert top.metadata == {"form": "8-K", "cik": "0000000000"}
    assert top.url == "https://www.sec.gov/Archives/edgar/0.htm" and top.published_at == "2026-06-30T00:00:00Z"
    assert "cybersecurity incident" in top.snippet
    assert result.candidate_counts == {"market_news": 12}

    (query,) = nvidia_api.to(EMBED_URL)
    assert query.body["input_type"] == "query" and query.body["model"] == "nvidia/nemotron-3-embed-1b"


async def test_all_sources_are_reranked_together_in_one_request(retriever: Retriever, nvidia_api: FakeNvidia):
    result = await retriever.retrieve("disclose a cybersecurity incident", BOTH, top_k=8)

    (rerank,) = nvidia_api.to(RERANK_URL)
    assert len(rerank.body["passages"]) == sum(result.candidate_counts.values())
    assert all(passage.keys() == {"text"} for passage in rerank.body["passages"])  # no metadata leaves the service
    assert result.candidate_counts == {"market_news": 12, "market_regulations": 12 + 5}
    assert {hit.source_id for hit in result.hits[:2]} == set(BOTH)
    assert [hit.rank for hit in result.hits] == list(range(1, 9))
    assert [hit.score for hit in result.hits] == sorted((hit.score for hit in result.hits), reverse=True)


@pytest.mark.parametrize(
    ("source_ids", "top_k", "limits"),
    [
        (BOTH, 3, [12, 12]),
        (BOTH, 25, [100, 100]),
        ([*BOTH, "market_filings"], 25, [66, 66, 66]),  # the third source is empty; only its share matters
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
    await retriever.retrieve("periodic reports", source_ids, top_k)

    assert requested == limits


async def test_result_describes_models_index_and_timings(retriever: Retriever, manifest: CollectionManifest):
    result = await retriever.retrieve("merger agreement", BOTH, top_k=2)

    assert result.collection == "test_pack"
    assert result.collection_version == manifest.physical_collection
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
    await retriever.retrieve("stock split", BOTH, top_k=2)

    traced = {span.name: span.attributes for span in spans.get_finished_spans()}
    assert {name: attributes["openinference.span.kind"] for name, attributes in traced.items()} == {
        "embed": "EMBEDDING",
        "search": "RETRIEVER",
        "rerank": "RERANKER",
    }
    assert traced["embed"]["embedding.model_name"] == "nvidia/nemotron-3-embed-1b"
    assert traced["rerank"]["reranker.output_documents.0.document.id"] in {"edgar:9:0001", "ecfr:9:0001"}


async def test_a_reindex_is_searched_without_a_restart(
    retriever: Retriever, manifest: CollectionManifest, settings: Settings, data_dir: Path
):
    write_pack(data_dir, documents()[:4])
    rebuilt = ingest.run(settings, data_dir)

    result = await retriever.retrieve("merger agreement", BOTH, top_k=8)

    assert result.collection_version == rebuilt.physical_collection != manifest.physical_collection
    assert result.candidate_counts == {"market_news": 2, "market_regulations": 2}


async def test_rate_limits_and_server_errors_are_retried(retriever: Retriever, nvidia_api: FakeNvidia):
    nvidia_api.fail(429, 503)

    result = await retriever.retrieve("dividend increase", ["market_news"], top_k=1)

    assert result.hits[0].document_id == "edgar:7"
    assert len(nvidia_api.to(EMBED_URL)) == 3


async def test_dropped_connections_and_gateway_error_pages_are_retried(retriever: Retriever, nvidia_api: FakeNvidia):
    # The async client reports a non-JSON error body as "[###] Unknown Error", without the status.
    nvidia_api.fail(aiohttp.ServerDisconnectedError(), 502, body="<html><body>502 Bad Gateway</body></html>")

    result = await retriever.retrieve("dividend increase", ["market_news"], top_k=1)

    assert result.hits[0].document_id == "edgar:7"
    assert len(nvidia_api.to(EMBED_URL)) == 3


async def test_client_errors_are_not_retried(retriever: Retriever, nvidia_api: FakeNvidia):
    nvidia_api.fail(401)

    with pytest.raises(Exception, match=r"^\[401\]"):
        await retriever.retrieve("dividend increase", ["market_news"], top_k=1)
    assert len(nvidia_api.to(EMBED_URL)) == 1
