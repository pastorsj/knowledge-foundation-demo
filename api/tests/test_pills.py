# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""A recorded session's technology pills, and the recordings' agreement with the tools each question declares."""

from __future__ import annotations

import json

import pytest
import yaml
from support import REPO

from demo_api.pills import session_pills
from demo_api.registry import ToolRegistry

REGISTRY = ToolRegistry.load(REPO / "contracts" / "tool-registry.json")
PACKS = REPO / "data" / "packs"


def available(tool: str, receipt_id: str) -> dict:
    return {"eventKind": "artifact.available", "state": "completed", "toolName": tool, "artifactRefs": [receipt_id]}


def analytics(receipt_id: str, device: str, library: str) -> dict:
    engine = {"device": device, "library": library, "version": "26.6.0"}
    return {"receiptId": receipt_id, "status": "completed", "content": {"engine": engine}}


def test_a_session_has_one_pill_per_kind_in_order_with_its_engine():
    turns = [
        {
            "events": [
                {"eventKind": "tool.completed", "state": "completed", "toolName": "skill_view"},  # Hermes's own
                available("retrieve_evidence", "r1"),
                available("market_anomaly_scan", "r2"),
                {"eventKind": "tool.observed", "state": "failed", "toolName": "predict_asset_outcomes"},  # failed
            ],
            "receipts": [{"receiptId": "r1", "status": "completed"}, analytics("r2", "gpu", "cuml.accel")],
        },
        {
            "events": [available("market_scan", "r3"), available("retrieve_evidence", "r4")],
            "receipts": [analytics("r3", "gpu", "cudf.pandas"), {"receiptId": "r4", "status": "completed"}],
        },
    ]

    assert session_pills(turns, REGISTRY) == [
        {"pill": "cudf", "device": "gpu", "tools": ["market_anomaly_scan", "market_scan"]},
        {"pill": "cuml", "device": "gpu", "tools": ["market_anomaly_scan"]},
        {"pill": "retrieval", "device": None, "tools": ["retrieve_evidence"]},
    ]


def test_a_run_on_the_cpu_keeps_its_engine():
    turns = [
        {
            "events": [available("analyze_market_relationships", "r1")],
            "receipts": [analytics("r1", "cpu", "networkx")],
        }
    ]

    assert session_pills(turns, REGISTRY) == [
        {"pill": "cudf", "device": "cpu", "tools": ["analyze_market_relationships"]},
        {"pill": "cugraph", "device": "cpu", "tools": ["analyze_market_relationships"]},
    ]


def _declared() -> list[tuple[str, str, list[str], list[str] | None]]:
    return [
        (pack.name, question["id"], question["tools"], question.get("profiles"))
        for pack in sorted(PACKS.iterdir())
        for question in yaml.safe_load((pack / "questions.yaml").read_text())["questions"]
    ]


@pytest.mark.parametrize(("pack", "question", "tools", "profiles"), _declared(), ids=str)
def test_a_recorded_question_used_every_tool_it_declares(
    pack: str, question: str, tools: list[str], profiles: list[str] | None
):
    """questions.yaml `tools` (the picker's pills) against the committed recording, for every question.

    A pack with a bundle must hold every question its profile offers: only a question for another profile (e.g. a
    minute-bar one) may be missing. A pack without a bundle is skipped whole.
    """
    recordings = PACKS / pack / "recordings"
    if not (recordings / "index.json").is_file():
        pytest.skip(f"{pack} has no recordings")
    session = recordings / "sessions" / f"{question}.json"
    if not session.is_file():
        profile = json.loads((recordings / "pack.json").read_text()).get("profile")
        if profiles and profile not in profiles:
            pytest.skip(f"{pack}/{question} needs the {', '.join(profiles)} profile; the bundle is {profile}")
        pytest.fail(f"{pack}/{question} has no recording")
    used = {pill["pill"] for pill in session_pills(json.loads(session.read_text())["turns"], REGISTRY)}

    assert set(tools) <= used, f"{pack}/{question} declares {tools}, but its recording used {sorted(used)}"
