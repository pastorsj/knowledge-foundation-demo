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
from conftest import MANUALS
from conftest import POLICIES
from conftest import index
from mcp.client import Client

from demo_retrieval import budget
from demo_retrieval import search
from demo_retrieval import store
from demo_retrieval.search import Hit
from demo_retrieval.search import Retriever
from demo_retrieval.server import create_server
from demo_retrieval.settings import Settings

pytestmark = pytest.mark.anyio

HERMES_MCP_LIMIT = 50_000  # Hermes v2026.9.24, tool_budget.mcp_result_size_chars
# Text that JSON escapes twice over (quotes, backslashes, line breaks), longer than a passage may be.
NASTY = 'Section 4.2 "restocking" \\ fee for returned furniture\n\t' * 60


def worst_chunks() -> list[dict[str, Any]]:
    """Forty long chunks per source, with titles, URLs and metadata as long as real documents have them."""
    return [
        {
            "chunk_id": f"{source}:{'d' * 60}:{i}:0001",
            "source_id": source,
            "document_id": f"{source}:{'d' * 60}:{i}",
            "title": f"{'Northwind Retail Store Operations Manual, Part 4, Returns and Exchanges ' * 3}{i}",
            "text": NASTY,
            "url": f"https://intranet.example.com/policies/returns.pdf?{'section=4&' * 20}{i}",
            "published_at": "2026-06-30T00:00:00Z",
            "citation": "Northwind Retail Policy § 4.2 " * 8,
            "section_heading": "SECTION 4—RETURNS, EXCHANGES AND RESTOCKING FEES " * 5,
            "chapter_heading": "CHAPTER II—STORE OPERATIONS " * 2,
            "pages": "1,2,3,4,5,6,7",
        }
        for source in (MANUALS, POLICIES)
        for i in range(40)
    ]


def metadata(chunk: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in chunk.items() if key not in store.SCALAR_FIELDS}


@pytest.fixture
async def worst(settings: Settings) -> AsyncIterator[Retriever]:
    index(settings.milvus_uri, "knowledge__worst", worst_chunks())
    retriever = Retriever(settings)
    yield retriever
    await retriever.close()


def as_hermes_reads_it(text: str) -> str:
    """Hermes puts the MCP text content under "result"; the execution-receipts plugin adds the evidence id first."""
    return json.dumps({"evidence_id": budget.EVIDENCE_ID, "result": text}, ensure_ascii=False)


async def test_the_worst_case_fits_the_budget_and_keeps_its_citations(worst: Retriever, knowledge_dir: Path) -> None:
    server = create_server(worst, knowledge_dir)
    async with Client(server) as client:
        result = await client.call_tool(
            "retrieve_evidence",
            {"query": "restocking fee for returned furniture", "source_ids": [MANUALS, POLICIES], "top_k": 25},
        )

    assert not result.is_error, result.content[0].text
    read = as_hermes_reads_it(result.content[0].text)
    assert len(read) <= budget.MAX_RESULT_CHARS < HERMES_MCP_LIMIT
    hits = result.structured_content["hits"]
    assert 1 <= len(hits) <= budget.MAX_HITS
    assert [hit["rank"] for hit in hits] == list(range(1, len(hits) + 1)), "the best passages, in rank order"
    for hit in hits:
        assert len(hit["snippet"]) <= search.MAX_SNIPPET_CHARS
        # A citation needs the document, its title, link and date; none of them is cut.
        source = next(chunk for chunk in worst_chunks() if chunk["chunk_id"] == hit["chunk_id"])
        assert (hit["title"], hit["url"], hit["published_at"]) == (
            source["title"],
            source["url"],
            source["published_at"],
        )
        assert hit["document_id"] == source["document_id"]
        assert hit["metadata"] == metadata(source)


async def test_top_k_above_the_cap_returns_the_cap(retriever: Retriever, knowledge_dir: Path) -> None:
    server = create_server(retriever, knowledge_dir)
    async with Client(server) as client:
        result = await client.call_tool(
            "retrieve_evidence", {"query": "store", "source_ids": [MANUALS, POLICIES], "top_k": 25}
        )

    assert len(result.structured_content["hits"]) == budget.MAX_HITS


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
        source_id=POLICIES,
        document_id=f"{POLICIES}:policy-0.pdf",
        chunk_id=f"{POLICIES}:policy-0.pdf:0001",
        title="T" * 20_000,
        url="https://intranet.example.com/policies/0.pdf",
        published_at=None,
        snippet="S" * 20_000,
        metadata={"citation": "C" * 20_000},
    )
    result = search.RetrievalResult(
        query="q",
        source_ids=[POLICIES],
        collection="knowledge",
        collection_version="knowledge__v1",
        hits=[hit],
        candidate_counts={POLICIES: 1},
        models=search.Models(embed="e", rerank=None),
        index=search.IndexInfo(type="HNSW", metric="COSINE", params={}, search_params={}),
        timings=search.Timings(embed_ms=0, search_ms=0, rerank_ms=0, total_ms=0),
    )

    fitted = budget.fit(result)

    assert budget.agent_chars(fitted) <= budget.MAX_RESULT_CHARS
    (kept,) = fitted.hits
    assert (kept.document_id, kept.chunk_id, kept.url) == (hit.document_id, hit.chunk_id, hit.url)
