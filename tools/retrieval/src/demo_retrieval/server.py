# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""MCP server (streamable HTTP at /mcp) exposing retrieve_evidence, plus GET /health."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import budget
from . import catalog
from .search import RetrievalFailed
from .search import RetrievalResult
from .search import Retriever
from .settings import Settings

logger = logging.getLogger(__name__)


def create_server(retriever: Retriever, knowledge_dir: Path) -> MCPServer:
    server = MCPServer(
        "retrieval",
        instructions="Search the document sources selected for this run and return ranked, citable passages.",
    )

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    async def retrieve_evidence(
        query: Annotated[
            str,
            Field(min_length=1, max_length=4000, description="What the passages should say, in their words"),
        ],
        source_ids: Annotated[
            list[str], Field(description="Document sources to search. The application sets this for each run.")
        ],
        top_k: Annotated[
            int, Field(ge=1, le=25, description=f"How many passages to return; at most {budget.MAX_HITS} are returned")
        ] = 8,
    ) -> RetrievalResult:
        """Search the selected document sources (policies, manuals, contracts, reports, uploaded files) and return
        the best passages, each with its document, title and metadata (such as file name and page) for citation.

        Passages from all sources are ranked together; one call returns at most 8. Write the query as one sentence
        the passage itself would contain, such as "returned furniture carries a restocking fee", not as a list of
        keywords joined by "or". Search each topic once, and rephrase at most once: when the passages lack a
        detail, say so instead of searching again.
        """
        try:
            requested = catalog.document_sources(knowledge_dir, source_ids)
        except catalog.CatalogError as error:
            raise ToolError(str(error)) from error
        try:
            return budget.fit(await retriever.retrieve(query, requested, min(top_k, budget.MAX_HITS)))
        except RetrievalFailed as error:  # written for the agent; no host names or URLs
            raise ToolError(str(error)) from error

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "collection": retriever.collection})

    return server


def serve(settings: Settings) -> None:
    retriever = Retriever(settings)
    server = create_server(retriever, settings.knowledge_dir)
    logger.info(
        "serving the %s alias for the catalog in %s on :%d/mcp (rerank: %s)",
        retriever.collection,
        settings.knowledge_dir,
        settings.port,
        settings.rerank_model or "off",
    )
    server.run("streamable-http", host="0.0.0.0", port=settings.port, stateless_http=True, json_response=True)
