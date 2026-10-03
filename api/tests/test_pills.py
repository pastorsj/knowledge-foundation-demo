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
QUESTIONS_SCHEMA_VERSION = "3"


def available(tool: str, receipt_id: str) -> dict:
    return {"eventKind": "artifact.available", "state": "completed", "toolName": tool, "artifactRefs": [receipt_id]}


def test_a_session_has_one_pill_per_kind_in_order():
    turns = [
        {
            "events": [
                {"eventKind": "tool.completed", "state": "completed", "toolName": "skill_view"},  # Hermes's own
                available("predict", "r1"),
                available("retrieve_evidence", "r2"),
                {"eventKind": "tool.observed", "state": "failed", "toolName": "ask_question"},  # failed
            ],
        },
        {
            "events": [available("query_tables", "r3"), available("retrieve_evidence", "r4")],
        },
    ]

    assert session_pills(turns, REGISTRY) == [
        {"pill": "retrieval", "tools": ["retrieve_evidence"]},
        {"pill": "duckdb", "tools": ["query_tables"]},
        {"pill": "kumo", "tools": ["predict"]},
    ]


def test_unregistered_and_retired_tools_have_no_pill():
    turns = [{"events": [available("market_scan", "r1"), available("memory", "r2")]}]

    assert session_pills(turns, REGISTRY) == []


def _declared() -> list[tuple[str, str, list[str]]]:
    """Every question of every pack in the current questions.yaml format; packs of an older format are retired."""
    declared = []
    for pack in sorted(PACKS.iterdir()) if PACKS.is_dir() else []:
        questions = yaml.safe_load((pack / "questions.yaml").read_text())
        if questions.get("schema_version") == QUESTIONS_SCHEMA_VERSION:
            declared += [(pack.name, question["id"], question["tools"]) for question in questions["questions"]]
    return declared


@pytest.mark.parametrize(("pack", "question", "tools"), _declared(), ids=str)
def test_a_recorded_question_used_every_tool_it_declares(pack: str, question: str, tools: list[str]):
    """questions.yaml `tools` (the picker's pills) against the committed recording, for every question.

    A pack with a bundle must hold every question; a pack without a bundle is skipped whole.
    """
    recordings = PACKS / pack / "recordings"
    if not (recordings / "index.json").is_file():
        pytest.skip(f"{pack} has no recordings")
    session = recordings / "sessions" / f"{question}.json"
    if not session.is_file():
        pytest.fail(f"{pack}/{question} has no recording")
    used = {pill["pill"] for pill in session_pills(json.loads(session.read_text())["turns"], REGISTRY)}

    assert set(tools) <= used, f"{pack}/{question} declares {tools}, but its recording used {sorted(used)}"
