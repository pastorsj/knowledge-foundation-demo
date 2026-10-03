# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The optional grader: configured from the environment only, blind, majority-voted, and never leaking its key."""

import json
from itertools import cycle

import pytest
from support import market_turn
from support import serve

from demo_eval.grader import Grader
from demo_eval.grader import GraderConfigError
from demo_eval.grader import parse_verdict
from demo_eval.grader import prompt

KEY = "fake-grader-key"
VERDICT = {"correctness": 4, "grounding": 5, "completeness": 4, "honesty": 5, "overall": 4, "pass": True, "notes": "ok"}
RUN = {"question": "Who led?", "status": {"status": "success"}, "turn": market_turn("PEAX led [1].")}


def completion(verdict) -> dict:
    content = verdict if isinstance(verdict, str) else json.dumps(verdict)
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def test_the_grader_is_off_unless_configured_and_refuses_half_a_configuration():
    assert Grader.from_env({}) is None
    with pytest.raises(GraderConfigError, match="missing GRADER_API_KEY, GRADER_MODEL"):
        Grader.from_env({"GRADER_BASE_URL": "https://grader.example/v1"})
    grader = Grader.from_env(
        {"GRADER_BASE_URL": "u", "GRADER_API_KEY": KEY, "GRADER_MODEL": "m", "GRADER_SAMPLES": "5"}
    )
    assert (grader.model, grader.samples) == ("m", 5)


def test_the_prompt_is_blind_to_the_model_and_carries_the_evidence():
    text = prompt(dataset="synthetic: test", question="Who led?", facts="PEAX led.", run=RUN)
    assert "DATASET: synthetic: test" in text and "REFERENCE FACTS:\nPEAX led." in text
    assert '"evidence_id": "r1"' in text and "REPORT:\nPEAX led [1]." in text
    assert "model-a" not in text and "efficient" not in text


def test_three_samples_are_majority_voted_over_structured_output():
    replies = cycle([VERDICT, {**VERDICT, "pass": False, "overall": 2}, VERDICT])
    with serve({("POST", "/v1/chat/completions"): lambda body, headers: (200, completion(next(replies)))}) as (
        url,
        seen,
    ):
        verdict = Grader(f"{url}/v1", KEY, "frontier-model").grade(dataset="d", question="Who led?", facts="f", run=RUN)

    assert verdict["pass"] is True and verdict["overall"] == pytest.approx(3.33)
    assert len(seen) == 3
    _, _, body, headers = seen[0]
    assert headers["authorization"] == f"Bearer {KEY}"
    assert body["model"] == "frontier-model" and body["response_format"]["type"] == "json_schema"


def test_an_endpoint_without_structured_output_is_asked_in_the_prompt_alone():
    def chat(body, _headers):
        if "response_format" in body:
            return 400, {"error": {"message": f"response_format is not supported (key {KEY})"}}
        return 200, completion("Here is the grade:\n```json\n" + json.dumps(VERDICT) + "\n```")

    with serve({("POST", "/v1/chat/completions"): chat}) as (url, seen):
        grader = Grader(f"{url}/v1", KEY, "m", samples=1)
        verdict = grader.grade(dataset="d", question="q", facts="f", run=RUN)

    assert verdict["pass"] is True
    assert [("response_format" in body) for _, _, body, _ in seen] == [True, False]


def test_errors_are_kept_without_the_key_and_an_incomplete_grade_is_none(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    with serve({("POST", "/v1/chat/completions"): lambda b, h: (401, {"error": f"bad key {KEY}"})}) as (url, _):
        grader = Grader(f"{url}/v1", KEY, "m", samples=2)
        assert grader.grade(dataset="d", question="q", facts="f", run=RUN) is None

    assert grader.errors and all(KEY not in error and "[redacted]" in error for error in grader.errors)


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({**VERDICT, "overall": 9}),
        json.dumps({**VERDICT, "pass": "yes"}),
        json.dumps({key: value for key, value in VERDICT.items() if key != "honesty"}),
    ],
)
def test_a_reply_that_is_not_a_valid_grade_is_refused(content):
    assert parse_verdict(content) is None
