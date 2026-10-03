# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import httpx
import pytest
from mcp.client import Client
from mcp.server.mcpserver import MCPServer

from demo_retrieval.search import Retriever
from demo_retrieval.server import create_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def server(retriever: Retriever) -> MCPServer:
    return create_server(retriever, frozenset({"market_news", "market_regulations"}))


async def test_tool_schema(server: MCPServer):
    async with Client(server) as client:
        (tool,) = (await client.list_tools()).tools

    assert tool.name == "retrieve_evidence"
    assert tool.input_schema["properties"].keys() == {"query", "source_ids", "top_k"}
    assert set(tool.input_schema["required"]) == {"query", "source_ids"}
    assert {"hits", "models", "index", "timings", "collection", "collection_version"} <= tool.output_schema[
        "properties"
    ].keys()
    assert tool.annotations.read_only_hint


async def test_the_description_keeps_form_names_out_of_queries(server: MCPServer):
    """A query with "8-K" returns cover pages: every filing's first passage repeats the form name and dates."""
    async with Client(server) as client:
        (tool,) = (await client.list_tools()).tools

    description = " ".join(tool.description.split())
    assert "When you search filings, write the query as one sentence the passage itself would contain" in description
    assert "not as a list of keywords" in description
    assert "Leave form names (8-K, 6-K)" in description and "cover page" in description
    assert "metadata gives its form and filing date" in description
    assert "for filings, no form names" in tool.input_schema["properties"]["query"]["description"]


async def test_retrieve_evidence_returns_structured_hits(server: MCPServer):
    async with Client(server) as client:
        result = await client.call_tool(
            "retrieve_evidence",
            {"query": "auditor change", "source_ids": ["market_regulations", "market_news"], "top_k": 3},
        )

    assert not result.is_error
    content = result.structured_content
    assert content["source_ids"] == ["market_news", "market_regulations"]
    assert len(content["hits"]) == 3
    assert {"document_id", "source_id", "title", "url", "snippet", "score", "rank"} <= content["hits"][0].keys()


@pytest.mark.parametrize(
    "source_ids",
    [[], ["market_structured"], ["market_news", "web"]],
    ids=["empty", "not-a-document-source", "unknown"],
)
async def test_sources_outside_the_pack_are_rejected(server: MCPServer, source_ids: list[str]):
    async with Client(server) as client:
        result = await client.call_tool("retrieve_evidence", {"query": "auditor change", "source_ids": source_ids})

    assert result.is_error
    assert "must be a non-empty subset of ['market_news', 'market_regulations']" in result.content[0].text


async def test_invalid_arguments_are_rejected(server: MCPServer):
    async with Client(server) as client:
        result = await client.call_tool("retrieve_evidence", {"query": "x", "source_ids": ["market_news"], "top_k": 99})

    assert result.is_error


async def test_health(server: MCPServer):
    transport = httpx.ASGITransport(app=server.streamable_http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://retrieval") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "collection": "test_pack"}
