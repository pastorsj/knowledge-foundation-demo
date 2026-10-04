# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""MCP server (streamable HTTP at /mcp) exposing predict, plus GET /health.

The tool is registered whether or not a Kumo endpoint is configured: its schema never depends on the data or the
deployment. Without an endpoint a call answers `available: false` with the reason.
"""

from __future__ import annotations

import logging
from typing import Annotated

import anyio.to_thread
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from .prediction import KUMO_MAX_ENTITIES
from .prediction import MAX_ROWS
from .prediction import PredictionError
from .prediction import PredictionResult
from .prediction import Predictor
from .settings import Settings

logger = logging.getLogger(__name__)


def create_server(settings: Settings) -> MCPServer:
    server = MCPServer(
        "prediction",
        instructions="Predict outcomes per entity with NVIDIA Kumo Relational over the selected structured source.",
    )
    predictor = Predictor(settings)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
    async def predict(
        question: Annotated[
            str, Field(min_length=1, max_length=1000, description="The question this prediction answers, in words")
        ],
        pql: Annotated[
            str,
            Field(
                min_length=1,
                max_length=2000,
                description="PQL (PREDICT <target> FOR EACH <table>.<primary key> [WHERE ...]) or template:<id>",
            ),
        ],
        source_ids: Annotated[
            list[str], Field(description="Structured sources to predict over. The application sets this for each run.")
        ],
        anchor_time: Annotated[
            str | None,
            Field(
                max_length=64,
                description="ISO 8601 time the prediction starts from; omit it for a template's own anchor, or else "
                "the latest timestamp in the data",
            ),
        ] = None,
    ) -> PredictionResult:
        """Predict an outcome per entity with NVIDIA Kumo Relational (a relational foundation model) over the
        selected structured source's tables, linked by their keys.

        Write PQL over the tables as the run instructions list them, without the source's alias:
        PREDICT <target> FOR EACH <table>.<primary key> [WHERE <condition on that table>]. The target is an
        aggregate over a linked table within a window of (start, end, unit) after the anchor time, compared for a
        yes/no outcome, such as COUNT(orders.*, 0, 90, days) = 0; an aggregate alone for a number, such as
        SUM(orders.net_amount, 0, 30, days); or a column of the entity table for a class. Or pass
        pql="template:<id>" to run one of the source's prediction templates, with its anchor time. Kumo scores up to
        1,000 entities (400 for RANK TOP k) in primary-key order, so narrow a larger population with FOR EACH ...
        WHERE <a condition on that table's own columns>; the result lists the 25 highest. When Kumo is unavailable
        the result says so (available: false, with the reason): report that instead of estimating the outcome yourself.
        """
        try:
            return await anyio.to_thread.run_sync(predictor.predict, pql, source_ids, anchor_time)
        except PredictionError as error:
            raise ToolError(str(error)) from error

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "kumo_configured": settings.kumo_url is not None})

    return server


def serve(settings: Settings) -> None:
    server = create_server(settings)
    logger.info(
        "serving predict over the catalog in %s on :%d/mcp (Kumo: %s; %d entities scored, %d rows returned at most)",
        settings.knowledge_dir,
        settings.port,
        settings.kumo_url or "not configured",
        KUMO_MAX_ENTITIES,
        MAX_ROWS,
    )
    server.run("streamable-http", host="0.0.0.0", port=settings.port, stateless_http=True, json_response=True)
