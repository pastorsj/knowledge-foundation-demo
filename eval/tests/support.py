# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Test helpers: a small HTTP server with canned JSON routes, and runs built from a few lines of report."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]

# (method, path) -> handler(body, headers) -> (status, json); a route's path may include the query string
Route = Callable[[Any, dict[str, str]], tuple[int, Any]]


@contextmanager
def serve(routes: dict[tuple[str, str], Route]) -> Iterator[tuple[str, list[tuple[str, str, Any, dict[str, str]]]]]:
    """A server on a free loopback port; yields its URL and the requests it got (method, path, body, headers)."""
    seen: list[tuple[str, str, Any, dict[str, str]]] = []

    class Handler(BaseHTTPRequestHandler):
        def _handle(self, method: str) -> None:
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else None
            headers = {key.lower(): value for key, value in self.headers.items()}
            seen.append((method, self.path, body, headers))
            route = routes.get((method, self.path)) or routes.get((method, self.path.split("?")[0]))
            status, answer = route(body, headers) if route else (404, {"detail": "not found"})
            data = json.dumps(answer).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - the http.server API
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def log_message(self, *_: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()


def ok(answer: Any) -> Route:
    return lambda _body, _headers: (200, answer)


def event(kind: str, *, tool: str | None = None, server: str | None = None, refs: tuple[str, ...] = (), **attrs: Any):
    return {
        "eventKind": kind,
        "state": "completed",
        "toolName": tool,
        "toolServer": server,
        "artifactRefs": list(refs),
        "display": {"label": kind, "attributes": attrs},
    }


def receipt(receipt_id: str, kind: str, tool: str, content: dict[str, Any], status: str = "completed"):
    return {"receiptId": receipt_id, "artifactKind": kind, "toolName": tool, "status": status, "content": content}


def prediction_receipt(*, available: bool = True, status: str | None = None) -> dict[str, Any]:
    """A Kumo prediction receipt as the export carries it (camelCase): scored customers, or unavailable."""
    rows = [{"entityId": "C2", "probability": 0.81}, {"entityId": "C1", "probability": 0.34}] if available else []
    content = {"available": available, "reason": None if available else "No Kumo endpoint", "rows": rows}
    return receipt(
        "r9",
        "structured_prediction",
        "mcp__prediction__predict",
        content,
        status or ("completed" if available else "failed"),
    )


def answer_turn(markdown: str, *, cited: bool = True, rows: list[Any] | None = None) -> dict[str, Any]:
    """One successful query_tables run whose report is ``markdown``; its one receipt holds ``rows``."""
    content = {"databaseName": "retail_sales", "sql": "SELECT 1", "rows": rows or []}
    return {
        "jobId": "job-1",
        "status": "success",
        "report": {
            "markdown": markdown,
            "citations": [{"number": 1, "evidenceId": "r1"}] if cited else [],
        },
        "events": [
            event("tool.completed", tool="query_tables", server="tables"),
            event("artifact.available", tool="query_tables", server="tables", refs=("r1",)),
            event("llm.call", served_model="model-a", tier="efficient", input_tokens=100, output_tokens=10),
            event("llm.call", served_model="model-b", tier="capable", input_tokens=50, output_tokens=5),
        ],
        "receipts": [receipt("r1", "structured_query", "mcp__tables__query_tables", content)],
    }
