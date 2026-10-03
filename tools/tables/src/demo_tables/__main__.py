# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""demo-tables serve: the query_tables MCP server over the shared knowledge catalog."""

from __future__ import annotations

import argparse
import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from . import server
from .settings import Settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="demo-tables", description=__doc__)
    parser.add_argument("command", nargs="?", choices=["serve"], default="serve", help="serve query_tables")
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _export_traces()
    server.serve(Settings.from_env())


def _export_traces() -> None:
    """Send the guard and DuckDB spans to OTEL_EXPORTER_OTLP_TRACES_ENDPOINT (Phoenix), when it is set."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        return
    provider = TracerProvider(resource=Resource.create({"service.name": "tables"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)


if __name__ == "__main__":
    main()
