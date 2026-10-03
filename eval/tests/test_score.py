# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Scoring one run: the generic checks every run gets, the question's own checks, and the run's costs."""

import json

import pytest
from support import REPO
from support import answer_turn
from support import event
from support import prediction_receipt

from demo_eval.score import Registry
from demo_eval.score import score
from demo_eval.spec import Check
from demo_eval.spec import QuestionSpec

REGISTRY = Registry.load(REPO)
ORACLES = {"leaders": [{"store": "Austin", "growth": 0.2474}, {"store": "Tulsa", "growth": -0.1878}]}
QUESTION = QuestionSpec(
    "store-growth",
    tools=(frozenset({"query_tables"}),),
    checks=(
        Check("strongest_named", "mentions", "leaders[0].store"),
        Check("weakest_named", "mentions", "leaders[-1].store"),
    ),
)


def run(turn, status="success"):
    return {
        "pack": "p",
        "qid": "store-growth",
        "run": 1,
        "job_id": "job-1",
        "status": {"status": status},
        "wall_seconds": 12.5,
        "turn": turn,
    }


def test_a_good_run_passes_every_check():
    row = score(
        run(answer_turn("Austin led and Tulsa lagged [1].")),
        QUESTION,
        declared=["duckdb"],
        oracles=ORACLES,
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
        ["query_tables"],
        ["duckdb"],
        1,
        1,
    )
    assert row["turns_by_tier"] == {"efficient": 1, "capable": 1}
    assert row["served_models"] == {"model-a": 1, "model-b": 1}
    assert (row["tokens_in"], row["tokens_out"]) == (150, 15)


def test_each_failure_is_named():
    turn = answer_turn("Austin led.", cited=False)
    row = score(run(turn), QUESTION, declared=["duckdb", "kumo"], oracles=ORACLES, registry=REGISTRY)

    assert row["det_pass"] is False
    assert row["failed_checks"] == ["cited", "declared_tools", "weakest_named"]


def test_a_citation_must_resolve_to_a_completed_receipt():
    turn = answer_turn("Austin led and Tulsa lagged [1].")
    turn["receipts"][0]["status"] = "failed"
    row = score(run(turn), QUESTION, declared=["duckdb"], oracles=ORACLES, registry=REGISTRY)

    assert row["checks"]["cited"] is False
    assert row["tool_errors"] == 1


def test_a_failed_job_skips_the_question_checks_and_fails():
    row = score(
        run({"events": [], "receipts": []}, status="failure"),
        QUESTION,
        declared=[],
        oracles=ORACLES,
        registry=REGISTRY,
    )

    assert row["det_pass"] is False
    assert set(row["checks"]) == {"success", "cited", "declared_tools", "required_tools"}


def test_a_question_without_answer_checks_gets_the_generic_ones():
    row = score(run(answer_turn("Something [1].")), None, declared=["duckdb"], oracles={}, registry=REGISTRY)

    assert row["det_pass"] is True
    assert set(row["checks"]) == {"success", "cited", "declared_tools", "required_tools"}


def test_repeated_calls_and_hermes_tools_are_counted_apart():
    turn = answer_turn("Austin led and Tulsa lagged [1].")
    turn["receipts"].append(dict(turn["receipts"][0], receiptId="r2"))
    turn["events"].insert(0, event("tool.completed", tool="skill_view"))  # Hermes's own: no tool server
    row = score(run(turn), QUESTION, declared=["duckdb"], oracles=ORACLES, registry=REGISTRY)

    assert (row["tool_calls"], row["duplicate_calls"]) == (1, 1)


@pytest.mark.parametrize(
    ("tool", "server", "pill"),
    [
        ("retrieve_evidence", "retrieval", "retrieval"),
        ("query_tables", "tables", "duckdb"),
        ("predict", "prediction", "kumo"),
        ("ask_question", "auto_ontology", "ontology"),
    ],
)
def test_each_tool_brings_its_pill(tool, server, pill):
    turn = answer_turn("Done [1].")
    turn["events"] = [event("tool.completed", tool=tool, server=server), event("artifact.available", tool=tool)]
    row = score(run(turn), None, declared=[pill], oracles={}, registry=REGISTRY)

    assert (row["pills_used"], row["checks"]["declared_tools"]) == ([pill], True)


def test_a_prediction_question_checks_that_kumo_scored_the_entities():
    question = QuestionSpec("churn", checks=(Check("scored", "prediction_available", True),))
    scored = answer_turn("C2 is most at risk [1].")
    scored["receipts"].append(prediction_receipt())
    unavailable = answer_turn("No prediction is available [1].")
    unavailable["receipts"].append(prediction_receipt(available=False))

    rows = [score(run(turn), question, declared=[], oracles={}, registry=REGISTRY) for turn in (scored, unavailable)]

    assert [row["checks"]["scored"] for row in rows] == [True, False]
    assert rows[1]["failed_checks"] == ["scored"] and rows[1]["tool_errors"] == 1


def test_the_committed_recordings_score_without_errors():
    """Every recorded session of every pack parses as a run (the export and the recordings share one shape)."""
    sessions = sorted((REPO / "data" / "packs").glob("*/recordings/sessions/*.json"))
    if not sessions:
        pytest.skip("no pack has recordings yet")
    for path in sessions:
        for turn in json.loads(path.read_text())["turns"]:
            row = score(run(turn, status=turn["status"]), None, declared=[], oracles={}, registry=REGISTRY)
            assert row["checks"]["success"] is (turn["status"] == "success"), path.name
