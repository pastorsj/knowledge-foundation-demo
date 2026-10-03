# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""`demo-eval run` end to end against a stand-in deployment: questions, oracles, runs, scores, report, exit codes."""

import json
import shutil
import uuid
from pathlib import Path

import pytest
from support import REPO
from support import market_turn
from support import ok
from support import serve

from demo_eval import cli
from demo_eval.client import Deployment
from demo_eval.client import wait

ANSWERS = """
dataset: "synthetic: test"
names: SELECT asset_id, company_name FROM main.assets WHERE asset_id IN ({ids})
questions:
  leaders:
    oracles: {leaders: {}}
    tools: [[market_scan]]
    checks:
      - {id: strongest_named, named: "leaders[0].asset_id"}
      - {id: weakest_named, named: "leaders[-1].asset_id"}
    facts: "Strongest first: {leaders: asset_id, total_return}."
"""
QUESTIONS = [
    {"id": "leaders", "question": "Who led?", "sources": ["market_data"], "tools": ["cudf"], "featured": True},
    {"id": "laggards", "question": "Who lagged?", "sources": ["market_data"], "tools": ["cudf"], "featured": True},
    {"id": "other", "question": "Anything?", "sources": ["market_data"], "tools": [], "featured": False},
]
REPORTS = {"Who led?": "AAA led and Bee Corp lagged [1].", "Who lagged?": "Nobody [1]."}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A checkout with one pack, its answer checks and an oracle, and the real tool registry."""
    pack = tmp_path / "data" / "packs" / "test-pack"
    (pack / "eval" / "oracles").mkdir(parents=True)
    (pack / "eval" / "oracles" / "leaders.sql").write_text("-- the leaders\nSELECT asset_id FROM main.assets;\n")
    (pack / "eval" / "answers.yaml").write_text(ANSWERS)
    (tmp_path / "contracts").mkdir()
    shutil.copy(REPO / "contracts" / "tool-registry.json", tmp_path / "contracts")
    return tmp_path


def deployment_routes(*, pack_id: str = "test-pack") -> dict:
    jobs: dict[str, str] = {}

    def submit(body, _headers):
        # As the API: the client's job id; the job's status and export routes exist from then on
        job_id = body["job_id"]
        if job_id in jobs:
            return 409, {"detail": f"Job already exists: {job_id}"}
        jobs[job_id] = body["input"]
        routes[("GET", f"/api/v1/jobs/async/job/{job_id}")] = ok({"job_id": job_id, "status": "success"})
        routes[("GET", f"/api/v1/jobs/async/job/{job_id}/export")] = lambda _body, _headers: (
            200,
            market_turn(REPORTS[jobs[job_id]]),
        )
        return 200, {"job_id": job_id, "status": "submitted"}

    def query(body, _headers):
        if "company_name" in body["sql"]:
            assert "'AAA', 'BBB'" in body["sql"]
            return 200, {"columns": ["asset_id", "company_name"], "rows": [["BBB", "Bee Corp"]]}
        assert body["sql"].startswith("SELECT * FROM (\n-- the leaders") and body["sql"].endswith(") LIMIT 100")
        return 200, {"columns": ["asset_id", "total_return"], "rows": [["AAA", 0.2], ["BBB", -0.1]]}

    routes = {
        ("GET", "/api/v1/pack"): ok({"id": pack_id, "version": "1.0.0", "questions": QUESTIONS}),
        ("GET", "/api/v1/data_sources"): ok(
            [{"id": "docs", "kind": "documents"}, {"id": "market_data", "kind": "structured"}]
        ),
        ("POST", "/api/v1/data_sources/market_data/query"): query,
        ("POST", "/api/v1/jobs/async/submit"): submit,
    }
    return routes


def test_a_run_asks_each_checked_question_scores_it_and_reports(repo, tmp_path, capsys):
    out = tmp_path / "out"
    with serve(deployment_routes()) as (url, seen):
        code = cli.main(
            ["--repo", str(repo), "run", "--url", url, "--out", str(out), "--questions", "leaders,laggards"]
        )

    assert code == 0
    [directory] = out.iterdir()
    assert directory.name.startswith("test-pack-")
    submitted = [body for method, path, body, _ in seen if path.endswith("/submit")]
    assert [{key: value for key, value in body.items() if key != "job_id"} for body in submitted] == [
        {"input": "Who led?", "data_sources": ["market_data"]},
        {"input": "Who lagged?", "data_sources": ["market_data"]},
    ]
    assert len({str(uuid.UUID(body["job_id"])) for body in submitted}) == 2  # each job's id, chosen by the client
    scores = {row["qid"]: row for row in json.loads((directory / "scores.json").read_text())}
    assert scores["leaders"]["det_pass"] is True  # Bee Corp is BBB's company name
    assert scores["laggards"]["det_pass"] is True
    assert json.loads((directory / "names.json").read_text()) == {"BBB": "Bee Corp"}
    assert json.loads((directory / "meta.json").read_text())["grader"] is None
    report = (directory / "report.md").read_text()
    assert "| leaders | 1 | 1/1 |" in report and "Grader: off" in report
    printed = capsys.readouterr().out
    assert "2 of 2 runs pass" in printed


def test_by_default_the_checked_questions_run_and_a_wrong_answer_fails(repo, tmp_path, capsys):
    REPORTS["Who led?"] = "BBB led [1]."
    try:
        with serve(deployment_routes()) as (url, seen):
            code = cli.main(["--repo", str(repo), "run", "--url", url, "--out", str(tmp_path / "out"), "--runs", "2"])
    finally:
        REPORTS["Who led?"] = "AAA led and Bee Corp lagged [1]."

    assert code == 1
    assert [body["input"] for _, path, body, _ in seen if path.endswith("/submit")] == ["Who led?", "Who led?"]
    printed = capsys.readouterr().out
    assert "strongest_named (2)" in printed and "0 of 2 runs pass" in printed


def test_a_saved_run_directory_is_scored_again_without_the_deployment(repo, tmp_path, capsys):
    with serve(deployment_routes()) as (url, _):
        cli.main(["--repo", str(repo), "run", "--url", url, "--out", str(tmp_path / "out"), "--questions", "leaders"])
    [directory] = (tmp_path / "out").iterdir()
    capsys.readouterr()

    assert cli.main(["--repo", str(repo), "report", str(directory)]) == 0
    assert "1 of 1 runs pass" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--pack", "us-equities"], "the deployment runs pack test-pack, not us-equities"),
        (["--questions", "nope"], "has no question nope"),
    ],
)
def test_a_request_that_does_not_fit_the_deployment_exits_64(repo, tmp_path, capsys, arguments, message):
    with serve(deployment_routes()) as (url, seen):
        code = cli.main(["--repo", str(repo), "run", "--url", url, "--out", str(tmp_path), *arguments])

    assert code == 64
    assert message in capsys.readouterr().err
    assert not [path for _, path, _, _ in seen if path.endswith("/submit")]


def test_a_pack_this_checkout_lacks_exits_64(repo, tmp_path, capsys):
    with serve(deployment_routes(pack_id="elsewhere")) as (url, _):
        assert cli.main(["--repo", str(repo), "run", "--url", url, "--out", str(tmp_path)]) == 64
    assert "no data/packs/ entry" in capsys.readouterr().err


def test_an_unreachable_deployment_exits_69(repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    assert cli.main(["--repo", str(repo), "run", "--url", "http://127.0.0.1:9", "--out", str(tmp_path)]) == 69
    assert "did not answer" in capsys.readouterr().err


def test_a_retried_submit_whose_first_try_was_accepted_starts_no_second_job(monkeypatch):
    """The API took the first try but the proxy lost its answer (a 502): the retry gets a 409 for the same job id."""
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    jobs: list[str] = []

    def submit(body, _headers):
        if body["job_id"] in jobs:
            return 409, {"detail": f"Job already exists: {body['job_id']}"}
        jobs.append(body["job_id"])
        return 502, {"detail": "Bad Gateway"}

    with serve({("POST", "/api/v1/jobs/async/submit"): submit}) as (url, seen):
        job_id = Deployment(url).submit("Who led?", ["market_data"])

    assert jobs == [job_id]
    assert [body["job_id"] for _, _, body, _ in seen] == [job_id, job_id]


def test_a_job_past_its_wait_is_cancelled_and_reported_stalled():
    clock = iter(range(0, 1000, 10))
    routes = {
        ("GET", "/api/v1/jobs/async/job/job-1"): ok({"status": "running"}),
        ("POST", "/api/v1/jobs/async/job/job-1/cancel"): ok({"cancelled": True}),
    }
    with serve(routes) as (url, seen):
        status = wait(Deployment(url), "job-1", max_seconds=25, clock=lambda: next(clock), sleep=lambda _s: None)

    assert status == {"status": "stalled", "api_status": "running"}
    assert seen[-1][:2] == ("POST", "/api/v1/jobs/async/job/job-1/cancel")


def test_with_the_grader_on_each_run_is_graded_blind_and_a_failed_grade_fails_the_run(
    repo, tmp_path, monkeypatch, capsys
):
    verdicts = iter([True, True, True, False, False, True])

    def chat(body, _headers):
        user = body["messages"][1]["content"]
        assert "REFERENCE FACTS:\nStrongest first: asset_id=AAA, total_return=0.200; asset_id=BBB" in user
        grade = {"correctness": 4, "grounding": 4, "completeness": 4, "honesty": 4, "overall": 4}
        content = json.dumps(grade | {"pass": next(verdicts), "notes": "checked"})
        return 200, {"choices": [{"message": {"content": content}}]}

    with serve(deployment_routes()) as (url, _), serve({("POST", "/v1/chat/completions"): chat}) as (grader, _):
        monkeypatch.setenv("GRADER_BASE_URL", f"{grader}/v1")
        monkeypatch.setenv("GRADER_API_KEY", "grader-key-for-tests")
        monkeypatch.setenv("GRADER_MODEL", "frontier-model")
        code = cli.main(["--repo", str(repo), "run", "--url", url, "--out", str(tmp_path / "out"), "--runs", "2"])

    assert code == 1
    [directory] = (tmp_path / "out").iterdir()
    assert sorted(path.name for path in (directory / "grades").iterdir()) == ["leaders.1.json", "leaders.2.json"]
    report = (directory / "report.md").read_text()
    assert "Grader: frontier-model, 3 samples" in report and "1 of 2 runs pass" in report
    for path in directory.rglob("*"):
        assert path.is_dir() or "grader-key-for-tests" not in path.read_text()
    assert "grader-key-for-tests" not in capsys.readouterr().err


def test_a_question_that_cannot_be_asked_is_reported_and_the_rest_still_run(repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    routes = deployment_routes()
    submit = routes[("POST", "/api/v1/jobs/async/submit")]
    routes[("POST", "/api/v1/jobs/async/submit")] = lambda body, headers: (
        (503, {"detail": "The API is starting or stopping."}) if body["input"] == "Who led?" else submit(body, headers)
    )
    with serve(routes) as (url, _):
        code = cli.main(
            ["--repo", str(repo), "run", "--url", url, "--out", str(tmp_path / "out")]
            + ["--questions"]
            + ["leaders,laggards"]
        )

    assert code == 1
    [directory] = (tmp_path / "out").iterdir()
    scores = {row["qid"]: row for row in json.loads((directory / "scores.json").read_text())}
    assert scores["leaders"]["status"] == "error" and scores["leaders"]["failed_checks"][0] == "cited"
    assert scores["laggards"]["det_pass"] is True
    assert "leaders.1: error" in capsys.readouterr().err


def test_an_oracle_the_build_refuses_exits_64_before_any_question_is_asked(repo, tmp_path, capsys):
    routes = deployment_routes()
    routes[("POST", "/api/v1/data_sources/market_data/query")] = lambda b, h: (422, {"detail": "no table assets"})
    with serve(routes) as (url, seen):
        assert cli.main(["--repo", str(repo), "run", "--url", url, "--out", str(tmp_path / "out")]) == 64

    assert "oracle leaders (leaders.sql) failed" in capsys.readouterr().err
    assert not [path for _, path, _, _ in seen if path.endswith("/submit")]
