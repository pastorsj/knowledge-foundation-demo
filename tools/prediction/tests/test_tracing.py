# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""None of the MCP housekeeping reaches Phoenix: Hermes connects to and pings every MCP server every few minutes, and
each message would be a trace of its own. (tools/tables tests that a tool call joins the caller's trace.)"""

from __future__ import annotations

from pathlib import Path

import mcp.shared._otel as mcp_otel
import pytest
from mcp.client import Client
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import Decision

from demo_prediction.__main__ import ToolCallsOnly
from demo_prediction.server import create_server
from demo_prediction.settings import Settings

pytestmark = pytest.mark.anyio


async def test_mcp_housekeeping_is_not_traced(knowledge_dir: Path, monkeypatch: pytest.MonkeyPatch):
    spans = InMemorySpanExporter()
    provider = TracerProvider(sampler=ToolCallsOnly())
    provider.add_span_processor(SimpleSpanProcessor(spans))
    monkeypatch.setattr(mcp_otel, "_tracer", provider.get_tracer("mcp-python-sdk"))
    async with Client(create_server(Settings(knowledge_dir=knowledge_dir))) as client:
        await client.list_tools()

    assert spans.get_finished_spans() == ()


@pytest.mark.parametrize("method", ["ping", "initialize", "notifications/initialized", "tools/list"])
def test_the_sampler_drops_every_mcp_message_but_tool_calls(method: str):
    sampler = ToolCallsOnly()
    assert sampler.should_sample(None, 1, method, attributes={"mcp.method.name": method}).decision == Decision.DROP
    call = {"mcp.method.name": "tools/call"}
    assert sampler.should_sample(None, 1, "tools/call", attributes=call).decision == Decision.RECORD_AND_SAMPLE
    assert sampler.should_sample(None, 1, "kumo", attributes={}).decision == Decision.RECORD_AND_SAMPLE
