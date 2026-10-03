# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Score one saved run: the generic checks every run gets, the question's own checks, and what the run cost.

Generic checks, for every question:

- ``success``: the job succeeded;
- ``cited``: the report cites at least one receipt of the run (a resolved citation);
- ``declared_tools``: the run used every tool pill the question declares (`questions.yaml` `tools`, the pills the
  UI's picker shows), as the replays' pills count them (`api/src/demo_api/pills.py`);
- ``required_tools``: every tool group of the question's `answers.yaml` entry had one tool called.

A run passes deterministically when all of them and all of the question's own checks pass.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .checks import evaluate
from .checks import grounding
from .checks import report_text
from .spec import QuestionSpec


class Registry:
    """contracts/tool-registry.json: each tool's pills."""

    def __init__(self, tools: list[dict[str, Any]]) -> None:
        self.pills = {tool["id"]: tuple(tool.get("pills") or ()) for tool in tools}

    @classmethod
    def load(cls, repo: Path) -> Registry:
        return cls(json.loads((repo / "contracts" / "tool-registry.json").read_text())["tools"])

    def used_pills(self, turn: dict[str, Any]) -> set[str]:
        """The pills of the run's completed registered tool calls (`artifact.available` events)."""
        return {
            pill
            for event in (turn or {}).get("events", [])
            if event.get("eventKind") == "artifact.available" and event.get("state") == "completed"
            for pill in self.pills.get(event.get("toolName") or "", ())
        }


def tools_called(turn: dict[str, Any]) -> list[str]:
    """The MCP tools the run called, in order (Hermes's own tools have no tool server)."""
    return [
        str(event["toolName"]).split("__")[-1]
        for event in (turn or {}).get("events", [])
        if event.get("eventKind") == "tool.completed" and event.get("toolServer") and event.get("toolName")
    ]


def resolved_citations(turn: dict[str, Any]) -> int:
    """Citations whose evidence id is a completed receipt of the run."""
    receipts = {r.get("receiptId") for r in (turn or {}).get("receipts", []) if r.get("status") == "completed"}
    citations = ((turn or {}).get("report") or {}).get("citations") or []
    return sum(1 for citation in citations if citation.get("evidenceId") in receipts)


def model_turns(turn: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        event["display"].get("attributes") or {}
        for event in (turn or {}).get("events", [])
        if event.get("eventKind") == "llm.call" and isinstance(event.get("display"), dict)
    ]


def score(
    run: dict[str, Any],
    question: QuestionSpec | None,
    *,
    declared: list[str],
    oracles: dict[str, list[dict[str, Any]]],
    names: dict[str, str],
    registry: Registry,
) -> dict[str, Any]:
    turn = run.get("turn") or {}
    status = (run.get("status") or {}).get("status")
    text = report_text(turn)
    called = tools_called(turn)
    used = set(called)
    pills = registry.used_pills(turn)
    receipts = turn.get("receipts", [])
    # A call repeated with the same arguments (a retrieval receipt has no public parameters: its content stands in)
    keys = [
        (
            r.get("toolName"),
            json.dumps(
                (r.get("content") or {}).get("publicParameters") or r.get("content"), sort_keys=True, default=str
            ),
        )
        for r in receipts
    ]
    success = status == "success"
    generic = {
        "success": success,
        "cited": resolved_citations(turn) >= 1,
        "declared_tools": set(declared) <= pills,
        "required_tools": all(group & used for group in (question.tools if question else ())),
    }
    specific = {}
    if success and question is not None:
        specific = {check.id: evaluate(check, text, turn, oracles, names) for check in question.checks}
    checks = generic | specific
    turns = model_turns(turn)
    tiers = Counter(attributes.get("tier") or "none" for attributes in turns)
    return {
        "pack": run.get("pack"),
        "qid": run.get("qid"),
        "run": run.get("run"),
        "job_id": run.get("job_id"),
        "status": status,
        "error": (run.get("status") or {}).get("error"),
        "wall_seconds": run.get("wall_seconds"),
        "det_pass": all(checks.values()),
        "checks": checks,
        "failed_checks": sorted(name for name, ok in checks.items() if not ok),
        "tools_used": sorted(used),
        "pills_used": sorted(pills),
        "tool_calls": len(called),
        "tool_errors": sum(r.get("status") != "completed" for r in receipts),
        "duplicate_calls": len(keys) - len(set(keys)),
        "citations": resolved_citations(turn),
        "turns": len(turns),
        "turns_by_tier": dict(tiers),
        "served_models": dict(Counter(attributes.get("served_model") or "?" for attributes in turns)),
        "tokens_in": sum(attributes.get("input_tokens") or 0 for attributes in turns),
        "tokens_out": sum(attributes.get("output_tokens") or 0 for attributes in turns),
        "percent_grounding": grounding(text, turn),
    }
