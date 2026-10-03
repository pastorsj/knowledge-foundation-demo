# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Test helpers: the fixture knowledge catalog, contract receipts, and a fake Hermes Runs API."""

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
# The shared catalog fixture: the retail pack (retail.policies documents, retail.sales tables) and the workspace
CATALOG = REPO / "contracts" / "fixtures" / "catalog"
DOCUMENTS = "retail.policies"
TABLES = "retail.sales"


def manifest_path(knowledge_dir: Path, kind: str, manifest_id: str) -> Path:
    """``catalog/packs/<id>.json`` (``kind`` packs) or ``catalog/sources/<id>.json`` (``kind`` sources)."""
    return knowledge_dir / "catalog" / kind / f"{manifest_id}.json"


def read_manifest(knowledge_dir: Path, kind: str, manifest_id: str) -> dict[str, Any]:
    return json.loads(manifest_path(knowledge_dir, kind, manifest_id).read_text(encoding="utf-8"))


def write_manifest(knowledge_dir: Path, kind: str, manifest: dict[str, Any]) -> None:
    path = manifest_path(knowledge_dir, kind, manifest["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def update_manifest(knowledge_dir: Path, kind: str, manifest_id: str, **changes: Any) -> dict[str, Any]:
    manifest = read_manifest(knowledge_dir, kind, manifest_id) | changes
    write_manifest(knowledge_dir, kind, manifest)
    return manifest


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
