# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""retrieve_evidence stays short enough for the agent to read whole, and keeps every passage's citation.

Hermes hides an MCP result longer than 50,000 characters from the model behind a 1,500-character preview, so the
worst case is measured here as the model would read it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from conftest import write_pack
from mcp.client import Client

from demo_retrieval import budget
from demo_retrieval import ingest
from demo_retrieval import search
from demo_retrieval.search import Hit
from demo_retrieval.search import Retriever
from demo_retrieval.server import create_server
from demo_retrieval.settings import Settings

pytestmark = pytest.mark.anyio

HERMES_MCP_LIMIT = 50_000  # Hermes v2026.9.24, tool_budget.mcp_result_size_chars
# Text that JSON escapes twice over (quotes, backslashes, line breaks), in chunks as long as ingest makes them.
NASTY = 'Item 1.05 "material" \\ cybersecurity incident\n\t' * 60


def worst_documents() -> list[dict[str, Any]]:
    """Forty long documents per source, with titles, URLs and metadata as long as the corpora have them."""
    return [
        {
            "document_id": f"{source}:{'d' * 60}:{i}",
            "source_id": source,
            "title": f"{'Regulation S-K, Subpart 229.1000, Mergers and Acquisitions (Regulation M-A) ' * 3}{i}",
            "text": NASTY * 2,
            "url": f"https://www.ecfr.gov/api/versioner/v1/full/2026-08-17/title-17.xml?{'part=229&' * 20}{i}",
            "published_at": "2026-06-30T00:00:00Z",
            "metadata": {
                "citation": "17 CFR § 229.1011 " * 8,
                "part_heading": "PART 229—STANDARD INSTRUCTIONS FOR FILING FORMS " * 5,
                "chapter_heading": "CHAPTER II—SECURITIES AND EXCHANGE COMMISSION " * 2,
                "items": "1.01,1.05,2.02,5.02,7.01,8.01,9.01",
            },
        }
        for source in ("market_news", "market_regulations")
        for i in range(40)
    ]


@pytest.fixture
async def worst(settings: Settings, tmp_path: Path) -> AsyncIterator[Retriever]:
    data_dir = tmp_path / "worst"
    write_pack(data_dir, worst_documents())
    manifest = ingest.run(settings, data_dir)
    retriever = Retriever(settings, manifest.collection)
    yield retriever
    await retriever.close()


def as_hermes_reads_it(text: str) -> str:
    """Hermes puts the MCP text content under "result"; the execution-receipts plugin adds the evidence id first."""
    return json.dumps({"evidence_id": budget.EVIDENCE_ID, "result": text}, ensure_ascii=False)


async def test_the_worst_case_fits_the_budget_and_keeps_its_citations(worst: Retriever) -> None:
    server = create_server(worst, frozenset({"market_news", "market_regulations"}))
    async with Client(server) as client:
        result = await client.call_tool(
            "retrieve_evidence",
            {
                "query": "Item 1.05 material cybersecurity incident",
                "source_ids": ["market_news", "market_regulations"],
                "top_k": 25,
            },
        )

    assert not result.is_error
    read = as_hermes_reads_it(result.content[0].text)
    assert len(read) <= budget.MAX_RESULT_CHARS < HERMES_MCP_LIMIT
    hits = result.structured_content["hits"]
    assert 1 <= len(hits) <= budget.MAX_HITS
    assert [hit["rank"] for hit in hits] == list(range(1, len(hits) + 1)), "the best passages, in rank order"
    for hit in hits:
        assert len(hit["snippet"]) <= search.MAX_SNIPPET_CHARS
        # A citation needs the document, its title, link and date; none of them is cut.
        source = next(doc for doc in worst_documents() if doc["document_id"] == hit["document_id"])
        assert (hit["title"], hit["url"], hit["published_at"]) == (
            source["title"],
            source["url"],
            source["published_at"],
        )
        assert hit["chunk_id"].startswith(hit["document_id"])
        assert hit["metadata"] == source["metadata"]


async def test_top_k_above_the_cap_returns_the_cap(retriever: Retriever) -> None:
    server = create_server(retriever, frozenset({"market_news", "market_regulations"}))
    async with Client(server) as client:
        result = await client.call_tool(
            "retrieve_evidence",
            {"query": "report", "source_ids": ["market_news", "market_regulations"], "top_k": 25},
        )

    assert len(result.structured_content["hits"]) == budget.MAX_HITS


def test_a_passage_is_never_shorter_than_a_chunk() -> None:
    assert search.MAX_SNIPPET_CHARS == ingest.CHUNK_SIZE


def test_a_long_passage_is_cut_at_a_word_and_marked() -> None:
    text = "word " * 1000
    clipped = search.clip(text, search.MAX_SNIPPET_CHARS)

    assert len(clipped) <= search.MAX_SNIPPET_CHARS
    assert clipped.endswith("word…")
    assert search.clip("short", search.MAX_SNIPPET_CHARS) == "short"


def test_a_lone_passage_too_long_is_cut_to_fit() -> None:
    hit = Hit(
        rank=1,
        score=1.0,
        vector_score=0.5,
        source_id="market_news",
        document_id="edgar:0",
        chunk_id="edgar:0:0001",
        title="T" * 20_000,
        url="https://www.sec.gov/Archives/edgar/0.htm",
        published_at=None,
        snippet="S" * 20_000,
        metadata={"citation": "C" * 20_000},
    )
    result = search.RetrievalResult(
        query="q",
        source_ids=["market_news"],
        collection="test_pack",
        collection_version="test_pack__x",
        hits=[hit],
        candidate_counts={"market_news": 1},
        models=search.Models(embed="e", rerank="r"),
        index=search.IndexInfo(type="HNSW", metric="COSINE", params={}, search_params={}),
        timings=search.Timings(embed_ms=0, search_ms=0, rerank_ms=0, total_ms=0),
    )

    fitted = budget.fit(result)

    assert budget.agent_chars(fitted) <= budget.MAX_RESULT_CHARS
    (kept,) = fitted.hits
    assert (kept.document_id, kept.chunk_id, kept.url) == (hit.document_id, hit.chunk_id, hit.url)
