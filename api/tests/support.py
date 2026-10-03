# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Test helpers: a small data pack, contract receipts, and a fake Hermes Runs API."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[2]
RECEIPT_KEY = "test-receipt-key"
PACK = {
    "schema_version": "1",
    "id": "market-analysis",
    "version": "1.0.0",
    "title": "Synthetic Multi-Asset Market Analysis",
    "description": "Test pack",
    "as_of": "2026-08-31",
    "disclaimer": "Synthetic market data. Not investment advice.",
    "profile": "interactive",
    "sources": [
        {
            "id": "market_analysis_structured",
            "name": "Market Prices & Events",
            "description": "Synthetic market history in DuckDB.",
            "agent_description": "Analyze synthetic market history.",
            "kind": "structured",
            "capabilities": ["structured_retrieval", "structured_prediction", "market_analytics"],
            "synthetic": True,
            "example_questions": ["Which assets led returns?"],
        },
        {
            "id": "market_news",
            "name": "SEC Filings",
            "description": "EDGAR filings.",
            "kind": "documents",
            "capabilities": ["unstructured_retrieval"],
            "synthetic": False,
            "example_questions": [],
        },
    ],
    "questions": [
        {
            "id": "market-leaders",
            "label": "Market Leaders",
            "tag": "ANALYTICS",
            "description": "A market scan.",
            "question": "Which assets had the strongest returns?",
            "sources": ["market_analysis_structured"],
            "featured": True,
        },
        {
            "id": "filings",
            "label": "Filings",
            "tag": "DOCUMENTS",
            "description": "Document search.",
            "question": "Which filings mention outages?",
            "sources": ["market_news"],
            "featured": False,
        },
    ],
    "conversations": [
        {
            "id": "leaders-follow-up",
            "label": "Leaders Follow-up",
            "tag": "ANALYTICS",
            "description": "A scan, then a follow-up.",
            "sources": ["market_analysis_structured"],
            "turns": ["Which assets had the weakest returns?", "How volatile were those assets?"],
        },
    ],
    "structured": {
        "source": "market_analysis_structured",
        "database_name": "market_analysis",
        "database": "structured/market_analysis.duckdb",
        "tables": {},
    },
    "documents": {"collection": "aiq_market_intelligence_current", "sources": ["market_news"]},
}


def load_contract(name: str) -> Any:
    return json.loads((REPO / "contracts" / "fixtures" / name).read_text(encoding="utf-8"))


def receipt_for(job_id: str, *, kind: str = "retrieval_evidence", call_id: str = "call_1") -> dict[str, Any]:
    """A contract fixture receipt of ``kind``, re-addressed to one tool call of ``job_id``."""
    receipt = copy.deepcopy(next(r for r in load_contract("receipts.json") if r["artifactKind"] == kind))
    receipt["jobId"] = job_id
    receipt["invocationId"] = f"hermes-tool:{call_id}"
    receipt["receiptId"] = f"hermes-receipt:{job_id}:{call_id}"
    return receipt


class FakeHermes:
    """An in-memory Hermes Runs API, served through ``httpx.MockTransport``.

    Each run waits until the test calls ``finish``; its event stream then sends the scripted
    events. ``requests`` records every call, headers included.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.runs: dict[str, dict[str, Any]] = {}
        self.run_by_key: dict[str, str] = {}
        self.created = asyncio.Event()
        self.stops: list[str] = []

    def finish(
        self,
        run_id: str,
        *,
        events: list[dict[str, Any]],
        status: str = "completed",
        output: str = "",
        times: tuple[float, float] | None = None,
    ) -> None:
        """End the run; ``times`` are its status's ``created_at`` and ``updated_at`` (Unix seconds)."""
        run = self.runs[run_id]
        run["events"], run["final_status"], run["output"] = events, status, output
        if times is not None:
            run["created_at"], run["updated_at"] = times
        run["released"].set()

    async def wait_for_run(self, count: int = 1) -> str:
        async with asyncio.timeout(5):
            while len(self.runs) < count:
                await asyncio.sleep(0.01)
        return list(self.runs)[count - 1]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        parts = request.url.path.strip("/").split("/")  # v1, runs, [id, [events|stop]]
        if request.method == "POST" and parts == ["v1", "runs"]:
            key = request.headers["Idempotency-Key"]
            if key not in self.run_by_key:
                run_id = f"run-{len(self.runs) + 1}"
                self.run_by_key[key] = run_id
                body = json.loads(request.content)
                self.runs[run_id] = {"status": "running", "released": asyncio.Event(), "payload": body, "output": ""}
            return httpx.Response(202, json={"run_id": self.run_by_key[key], "status": "queued"})
        run_id = parts[2]
        if request.method == "POST" and parts[-1] == "stop":
            self.stops.append(run_id)
            if run_id not in self.runs:  # a run a previous API process started
                return httpx.Response(200, json={"run_id": run_id, "status": "cancelled"})
            self.runs[run_id]["status"] = "cancelled"
        run = self.runs[run_id]
        if request.method == "GET" and len(parts) == 4 and parts[3] == "events":
            await run["released"].wait()
            run["status"] = run["final_status"]
            lines = "".join(f"data: {json.dumps({'run_id': run_id, **event})}\n\n" for event in run["events"])
            return httpx.Response(200, text=lines, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=self.status(run_id))

    def status(self, run_id: str) -> dict[str, Any]:
        run = self.runs[run_id]
        output = run["output"] if run["status"] == "completed" else None
        times = {key: run[key] for key in ("created_at", "updated_at") if key in run}
        return {
            "run_id": run_id,
            "status": run["status"],
            "session_id": run["payload"]["session_id"],
            "output": output,
            **times,
        }


def event(name: str, **data: Any) -> dict[str, Any]:
    return {"event": name, "timestamp": time.time(), **data}
