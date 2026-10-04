# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""What reaches Phoenix from query_tables: tool calls in the caller's trace, and none of the MCP housekeeping."""

from __future__ import annotations

import mcp.shared._otel as mcp_otel
import pytest
from conftest import SALES
from mcp.client import Client
from mcp.server.mcpserver import MCPServer
from opentelemetry import context
from opentelemetry import propagate
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import Decision

from demo_tables.__main__ import ToolCallsOnly

pytestmark = pytest.mark.anyio

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"  # the agent's trace, as Hermes sends it in the request's _meta


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    """The MCP SDK's spans, sampled as `demo-tables serve` samples them."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ToolCallsOnly())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(mcp_otel, "_tracer", provider.get_tracer("mcp-python-sdk"))
    return exporter


async def test_mcp_housekeeping_is_not_traced(server: MCPServer, spans: InMemorySpanExporter):
    """Hermes connects to and pings every MCP server every few minutes: each message would be a trace of its own."""
    async with Client(server) as client:
        await client.list_tools()

    assert spans.get_finished_spans() == ()


async def test_a_tool_call_joins_the_callers_trace(server: MCPServer, spans: InMemorySpanExporter):
    arguments = {
        "question": "How many orders?",
        "sql": "SELECT count(*) FROM retail_sales.orders",
        "source_ids": [SALES],
    }
    token = context.attach(propagate.extract({"traceparent": f"00-{TRACE_ID}-b7ad6b7169203331-01"}))
    try:
        async with Client(server) as client:
            result = await client.call_tool("query_tables", arguments)
    finally:
        context.detach(token)

    assert not result.is_error
    (call,) = [span for span in spans.get_finished_spans() if span.name == "tools/call query_tables"]
    assert format(call.context.trace_id, "032x") == TRACE_ID
    assert {span.attributes["mcp.method.name"] for span in spans.get_finished_spans()} == {"tools/call"}


@pytest.mark.parametrize("method", ["ping", "initialize", "notifications/initialized", "tools/list"])
def test_the_sampler_drops_every_mcp_message_but_tool_calls(method: str):
    sampler = ToolCallsOnly()
    assert sampler.should_sample(None, 1, method, attributes={"mcp.method.name": method}).decision == Decision.DROP
    assert sampler.should_sample(None, 1, "duckdb", attributes={}).decision == Decision.RECORD_AND_SAMPLE
