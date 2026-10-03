# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""MCP server (streamable HTTP at /mcp) exposing query_tables, plus GET /health.

Each call reads the catalog, checks the SQL (guard.py), then runs it in a worker process (query.py). The result is
kept under 30,000 characters as the agent reads it, by dropping its last rows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import AbstractContextManager
from typing import Annotated
from typing import Any

import pydantic_core
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from openinference.semconv.trace import OpenInferenceSpanKindValues as SpanKind
from openinference.semconv.trace import SpanAttributes
from opentelemetry import trace
from opentelemetry.trace import Span
from pydantic import BaseModel
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import catalog
from . import guard
from . import query
from .settings import Settings

# 60% of Hermes' 50,000-character limit for one MCP result, as the agent reads it (tools/retrieval/budget.py).
MAX_RESULT_CHARS = 30_000
# What the execution-receipts plugin puts first in every result: {"evidence_id": "hermes-receipt:<sha256>"}.
EVIDENCE_ID = "hermes-receipt:" + "0" * 64
MAX_CONCURRENT_QUERIES = 4
DATABASE_NAME = "knowledge"  # database_name when more than one source is attached

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class AttachedDatabase(BaseModel):
    source_id: str
    alias: str = Field(description="The name the query qualifies the source's tables with: <alias>.<table>")


class QueryResult(BaseModel):
    question: str
    sql: str
    database_name: str = Field(description=f"The alias when one source is attached, else {DATABASE_NAME!r}")
    databases: list[AttachedDatabase]
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int = Field(description="Rows in this result")
    truncated: bool = Field(description="True when the query had more rows than this result holds")
    elapsed_ms: float
    warnings: list[str]


def create_server(settings: Settings) -> MCPServer:
    server = MCPServer(
        "tables",
        instructions="Run one read-only DuckDB SELECT over the structured sources selected for this run.",
    )
    slots = asyncio.Semaphore(MAX_CONCURRENT_QUERIES)

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False))
    async def query_tables(
        question: Annotated[
            str, Field(min_length=1, max_length=1000, description="The question this query answers, in words")
        ],
        sql: Annotated[
            str,
            Field(min_length=1, max_length=10_000, description="One DuckDB SELECT naming tables as <alias>.<table>"),
        ],
        source_ids: Annotated[
            list[str], Field(description="Structured sources to attach. The application sets this for each run.")
        ],
    ) -> QueryResult:
        """Run one read-only SQL SELECT (DuckDB dialect) over the selected structured sources and return its rows.

        Each source is attached under its alias; name every table as <alias>.<table> (the run instructions list
        each source's alias, tables, columns and keys). CTEs, joins (also across sources), subqueries, UNION, window
        functions and range/generate_series/unnest are fine. A result holds at most 200 rows, so aggregate, filter
        and ORDER BY ... LIMIT in SQL instead of fetching raw rows. Only one SELECT runs: no file or URL reads,
        ATTACH, COPY, PRAGMA, SET, INSTALL or LOAD. A query still running after 10 seconds is stopped.
        """
        started = time.perf_counter()
        try:
            sources = catalog.structured_sources(settings.knowledge_dir, source_ids)
            with _span("guard", SpanKind.GUARDRAIL, {SpanAttributes.INPUT_VALUE: sql}):
                guard.validate(sql, {source.alias: [table.name for table in source.tables] for source in sources})
        except (catalog.CatalogError, guard.QueryRejected) as error:
            raise ToolError(str(error)) from error

        databases = [AttachedDatabase(source_id=source.id, alias=source.alias) for source in sources]
        attachments = [query.Attachment(alias=source.alias, path=source.path) for source in sources]
        with _span("duckdb", SpanKind.TOOL, {SpanAttributes.INPUT_VALUE: sql}) as span:
            span.set_attribute(SpanAttributes.METADATA, json.dumps({"databases": [d.alias for d in databases]}))
            try:
                async with slots:
                    rows = await query.run(attachments, sql, timeout=settings.timeout_seconds)
                    # The worker's output is bounded (query.MAX_OUTPUT_BYTES), and shaping it runs off the loop.
                    result = await asyncio.to_thread(_shape, question, sql, databases, rows, started)
            except query.QueryFailed as error:
                raise ToolError(str(error)) from error
            span.set_attribute("output.row_count", len(result.rows))
        return result

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return server


def _shape(question: str, sql: str, databases: list[AttachedDatabase], rows: query.Rows, started: float) -> QueryResult:
    warnings = []
    if rows.byte_limited:
        warnings.append(
            f"Only the first {len(rows.rows)} rows were read: the rows are too large to return more. Select fewer "
            "or shorter columns, or aggregate in SQL."
        )
    elif rows.truncated:
        warnings.append(
            f"The query returned more than {query.MAX_ROWS} rows; only the first {query.MAX_ROWS} rows are "
            "shown. Aggregate or filter in SQL, or ORDER BY and LIMIT it."
        )
    if rows.clipped:
        warnings.append(
            f"{rows.clipped} values were cut to {query.MAX_CELL_CHARS} characters or {query.MAX_ITEMS} list items."
        )
    result = QueryResult(
        question=question,
        sql=sql,
        database_name=databases[0].alias if len(databases) == 1 else DATABASE_NAME,
        databases=databases,
        columns=rows.columns,
        rows=[dict(zip(rows.columns, row, strict=True)) for row in rows.rows],
        row_count=len(rows.rows),
        truncated=rows.truncated,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        warnings=warnings,
    )
    return fit(result)


def agent_chars(result: QueryResult) -> int:
    """The result's length as the agent reads it: the MCP server sends it as indented JSON text, Hermes puts that
    text in a JSON string under "result", and the execution-receipts plugin adds the evidence id."""
    text = pydantic_core.to_json(result, fallback=str, indent=2).decode()
    return len(json.dumps({"evidence_id": EVIDENCE_ID, "result": text}, ensure_ascii=False))


def fit(result: QueryResult) -> QueryResult:
    """The result with as many of its first rows as fit in MAX_RESULT_CHARS, and a warning when rows were dropped."""
    if agent_chars(result) <= MAX_RESULT_CHARS:
        return result
    total = len(result.rows)

    def keep(count: int) -> QueryResult:
        warning = (
            f"Only the first {count} of {total} rows fit in {MAX_RESULT_CHARS:,} characters. Select fewer or "
            "shorter columns, or aggregate in SQL."
        )
        return result.model_copy(
            update={
                "rows": result.rows[:count],
                "row_count": count,
                "truncated": True,
                "warnings": [*result.warnings, warning],
            }
        )

    low, high = 0, total - 1  # the largest count that fits lies in [low, high]
    while low < high:  # at most 8 steps over at most query.MAX_OUTPUT_BYTES of rows
        middle = (low + high + 1) // 2
        if agent_chars(keep(middle)) <= MAX_RESULT_CHARS:
            low = middle
        else:
            high = middle - 1
    fitted = keep(low)
    if agent_chars(fitted) <= MAX_RESULT_CHARS:
        return fitted
    # Even without rows the echo is too long: a long SQL text, question or column list that JSON escapes twice over.
    shortened = fitted.model_copy(
        update={
            "warnings": [*fitted.warnings, "The SQL and question are shortened in this result to fit."],
            "columns": [_clip(column, 64) for column in fitted.columns],
        }
    )
    limit = max(len(fitted.sql), len(fitted.question))
    while agent_chars(shortened) > MAX_RESULT_CHARS and limit > 64:
        limit //= 2
        shortened = shortened.model_copy(
            update={"sql": _clip(fitted.sql, limit), "question": _clip(fitted.question, limit)}
        )
    return shortened


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _span(name: str, kind: SpanKind, attributes: dict[str, Any]) -> AbstractContextManager[Span]:
    return tracer.start_as_current_span(
        name, attributes={SpanAttributes.OPENINFERENCE_SPAN_KIND: kind.value, **attributes}
    )


def serve(settings: Settings) -> None:
    server = create_server(settings)
    logger.info("serving query_tables over the catalog in %s on :%d/mcp", settings.knowledge_dir, settings.port)
    server.run("streamable-http", host="0.0.0.0", port=settings.port, stateless_http=True, json_response=True)
