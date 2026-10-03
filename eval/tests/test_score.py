# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Scoring one run: the generic checks every run gets, the question's own checks, and the run's costs."""

import json

from support import REPO
from support import event
from support import market_turn

from demo_eval.score import Registry
from demo_eval.score import score
from demo_eval.spec import Check
from demo_eval.spec import QuestionSpec

REGISTRY = Registry.load(REPO)
ORACLES = {"leaders": [{"asset_id": "PEAX", "total_return": 0.2474}, {"asset_id": "VIAS", "total_return": -0.1878}]}
QUESTION = QuestionSpec(
    "market-leaders",
    tools=(frozenset({"market_scan"}),),
    checks=(
        Check("strongest_named", "named", "leaders[0].asset_id"),
        Check("weakest_named", "named", "leaders[-1].asset_id"),
    ),
)


def run(turn, status="success"):
    return {
        "pack": "p",
        "qid": "market-leaders",
        "run": 1,
        "job_id": "job-1",
        "status": {"status": status},
        "wall_seconds": 12.5,
        "turn": turn,
    }


def test_a_good_run_passes_every_check():
    row = score(
        run(market_turn("PEAX led and VIAS lagged [1].")),
        QUESTION,
        declared=["cudf"],
        oracles=ORACLES,
        names={},
        registry=REGISTRY,
    )

    assert row["det_pass"] is True
    assert row["checks"] == {
        "success": True,
        "cited": True,
        "declared_tools": True,
        "required_tools": True,
        "strongest_named": True,
        "weakest_named": True,
    }
    assert (row["tools_used"], row["pills_used"], row["tool_calls"], row["citations"]) == (
        ["market_scan"],
        ["cudf"],
        1,
        1,
    )
    assert row["turns_by_tier"] == {"efficient": 1, "capable": 1}
    assert row["served_models"] == {"model-a": 1, "model-b": 1}
    assert (row["tokens_in"], row["tokens_out"]) == (150, 15)


def test_each_failure_is_named():
    turn = market_turn("PEAX led.", cited=False)
    row = score(run(turn), QUESTION, declared=["cudf", "cuml"], oracles=ORACLES, names={}, registry=REGISTRY)

    assert row["det_pass"] is False
    assert row["failed_checks"] == ["cited", "declared_tools", "weakest_named"]


def test_a_citation_must_resolve_to_a_completed_receipt():
    turn = market_turn("PEAX led and VIAS lagged [1].")
    turn["receipts"][0]["status"] = "failed"
    row = score(run(turn), QUESTION, declared=["cudf"], oracles=ORACLES, names={}, registry=REGISTRY)

    assert row["checks"]["cited"] is False
    assert row["tool_errors"] == 1


def test_a_failed_job_skips_the_question_checks_and_fails():
    row = score(
        run({"events": [], "receipts": []}, status="failure"),
        QUESTION,
        declared=[],
        oracles=ORACLES,
        names={},
        registry=REGISTRY,
    )

    assert row["det_pass"] is False
    assert set(row["checks"]) == {"success", "cited", "declared_tools", "required_tools"}


def test_a_question_without_answer_checks_gets_the_generic_ones():
    row = score(run(market_turn("Something [1].")), None, declared=["cudf"], oracles={}, names={}, registry=REGISTRY)

    assert row["det_pass"] is True
    assert set(row["checks"]) == {"success", "cited", "declared_tools", "required_tools"}


def test_repeated_calls_and_hermes_tools_are_counted_apart():
    turn = market_turn("PEAX led and VIAS lagged [1].")
    turn["receipts"].append(dict(turn["receipts"][0], receiptId="r2"))
    turn["events"].insert(0, event("tool.completed", tool="skill_view"))  # Hermes's own: no tool server
    row = score(run(turn), QUESTION, declared=["cudf"], oracles=ORACLES, names={}, registry=REGISTRY)

    assert (row["tool_calls"], row["duplicate_calls"]) == (1, 1)


def test_the_committed_recordings_score_without_errors():
    """Every recorded synthetic-market session parses as a run (the export and the recordings share one shape)."""
    sessions = sorted((REPO / "data" / "packs" / "synthetic-market" / "recordings" / "sessions").glob("*.json"))
    assert sessions
    for path in sessions:
        for turn in json.loads(path.read_text())["turns"]:
            row = score(run(turn, status=turn["status"]), None, declared=[], oracles={}, names={}, registry=REGISTRY)
            assert row["checks"]["success"] is (turn["status"] == "success"), path.name
