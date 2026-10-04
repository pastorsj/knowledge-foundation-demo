# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""demo-prediction serve: the predict MCP server over the shared knowledge catalog."""

from __future__ import annotations

import argparse
import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.sdk.trace.sampling import Decision
from opentelemetry.sdk.trace.sampling import ParentBased
from opentelemetry.sdk.trace.sampling import Sampler
from opentelemetry.sdk.trace.sampling import SamplingResult

from . import server
from .settings import Settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="demo-prediction", description=__doc__)
    parser.add_argument("command", nargs="?", choices=["serve"], default="serve", help="serve predict")
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _export_traces()
    server.serve(Settings.from_env())


def _export_traces() -> None:
    """Send the Kumo spans to OTEL_EXPORTER_OTLP_TRACES_ENDPOINT (Phoenix), when it is set."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        return
    provider = TracerProvider(resource=Resource.create({"service.name": "prediction"}), sampler=ToolCallsOnly())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)


class ToolCallsOnly(Sampler):
    """The SDK's default sampler, minus the MCP SDK's spans for anything but ``tools/call``.

    The MCP SDK opens a span for every message, and Hermes pings each MCP server every few minutes and lists its
    tools on every connection: each of those would be a one-span trace of its own in Phoenix, burying the jobs.
    """

    _default = ParentBased(ALWAYS_ON)

    def should_sample(  # noqa: PLR0917 - the Sampler interface
        self, parent_context, trace_id, name, kind=None, attributes=None, links=None, trace_state=None
    ):
        method = (attributes or {}).get("mcp.method.name")
        if method is not None and method != "tools/call":
            return SamplingResult(Decision.DROP)
        return self._default.should_sample(parent_context, trace_id, name, kind, attributes, links, trace_state)

    def get_description(self) -> str:
        return "ToolCallsOnly"


if __name__ == "__main__":
    main()
