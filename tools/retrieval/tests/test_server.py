# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from conftest import MANUALS
from conftest import POLICIES
from conftest import derived_source
from conftest import write_source
from mcp.client import Client
from mcp.server.mcpserver import MCPServer

from demo_retrieval.search import Retriever
from demo_retrieval.server import create_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def server(retriever: Retriever, knowledge_dir: Path) -> MCPServer:
    return create_server(retriever, knowledge_dir)


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


async def test_the_description_is_about_enterprise_documents(server: MCPServer):
    async with Client(server) as client:
        (tool,) = (await client.list_tools()).tools

    description = " ".join(tool.description.split())
    assert "document sources" in description
    assert "one sentence the passage itself would contain" in description
    for market_word in ("filing", "8-K", "Form"):
        assert market_word not in description


async def test_retrieve_evidence_returns_structured_hits(server: MCPServer):
    async with Client(server) as client:
        result = await client.call_tool(
            "retrieve_evidence",
            {"query": "cash handling at closing", "source_ids": [POLICIES, MANUALS, POLICIES], "top_k": 3},
        )

    assert not result.is_error, result.content[0].text
    content = result.structured_content
    assert content["source_ids"] == [MANUALS, POLICIES]
    assert content["collection"] == "knowledge" and content["collection_version"] == "knowledge__v1"
    assert len(content["hits"]) == 3
    assert {"document_id", "source_id", "title", "url", "snippet", "score", "rank"} <= content["hits"][0].keys()


async def test_an_ingesting_source_is_searched(server: MCPServer):
    """What an ingesting source already holds stays usable; this one holds nothing yet."""
    async with Client(server) as client:
        result = await client.call_tool(
            "retrieve_evidence", {"query": "gift card refund rules", "source_ids": ["workspace.documents"]}
        )

    assert not result.is_error, result.content[0].text
    assert result.structured_content["hits"] == []
    assert result.structured_content["candidate_counts"] == {"workspace.documents": 0}


@pytest.mark.parametrize(
    ("source_ids", "message"),
    [
        ([], "select at least one document source"),
        (["retail.unknown"], "retail.unknown is not in the knowledge catalog"),
        (["retail.sales"], "retail.sales is a structured source, not a documents source"),
        ([POLICIES, "retail.drafts"], "retail.drafts has status 'empty'"),
        (["retail.archive"], "retail.archive has status 'failed'"),
        (["../../etc/passwd"], "'../../etc/passwd' is not a source id"),
        (["retail.policies/../retail.sales"], "is not a source id"),
    ],
    ids=["empty", "unknown", "structured", "empty-status", "failed-status", "path", "nested-path"],
)
async def test_sources_that_cannot_be_searched_are_refused(server: MCPServer, source_ids: list[str], message: str):
    async with Client(server) as client:
        result = await client.call_tool("retrieve_evidence", {"query": "restocking fee", "source_ids": source_ids})

    assert result.is_error
    assert message in result.content[0].text


async def test_the_catalog_is_read_on_every_call(server: MCPServer, knowledge_dir: Path):
    async with Client(server) as client:
        before = await client.call_tool("retrieve_evidence", {"query": "fee", "source_ids": ["retail.handbook"]})
        write_source(knowledge_dir, derived_source("retail.handbook"))
        after = await client.call_tool("retrieve_evidence", {"query": "fee", "source_ids": ["retail.handbook"]})
        write_source(knowledge_dir, derived_source("retail.handbook", status="failed"))
        failed = await client.call_tool("retrieve_evidence", {"query": "fee", "source_ids": ["retail.handbook"]})

    assert before.is_error and not after.is_error and failed.is_error


async def test_a_manifest_must_carry_its_own_id(server: MCPServer, knowledge_dir: Path):
    sources = knowledge_dir / "catalog" / "sources"
    write_source(knowledge_dir, derived_source("retail.other"))
    (sources / "retail.other.json").rename(sources / "retail.handbook.json")

    async with Client(server) as client:
        result = await client.call_tool("retrieve_evidence", {"query": "fee", "source_ids": ["retail.handbook"]})

    assert result.is_error
    assert "names another source" in result.content[0].text


async def test_invalid_arguments_are_rejected(server: MCPServer):
    async with Client(server) as client:
        result = await client.call_tool("retrieve_evidence", {"query": "x", "source_ids": [POLICIES], "top_k": 99})

    assert result.is_error


async def test_health(server: MCPServer):
    transport = httpx.ASGITransport(app=server.streamable_http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://retrieval") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "collection": "knowledge"}
