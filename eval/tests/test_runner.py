# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""`demo-eval run` end to end against a stand-in deployment: questions, oracles, runs, scores, report, exit codes."""

import json
import shutil
import uuid
from pathlib import Path

import pytest
from support import REPO
from support import answer_turn
from support import ok
from support import serve

from demo_eval import cli
from demo_eval.client import Deployment
from demo_eval.client import wait
from demo_eval.oracles import OracleError
from demo_eval.oracles import structured_source

ANSWERS = """
dataset: "synthetic: test"
questions:
  leaders:
    oracles: {leaders: {}}
    tools: [[query_tables]]
    checks:
      - {id: strongest_named, mentions: "leaders[0].store"}
      - {id: weakest_named, mentions: "leaders[-1].store"}
    facts: "Strongest first: {leaders: store, growth}."
"""
# questions.yaml lists a pack's own source ids; the catalog's are <pack>.<source>
QUESTIONS = [
    {"id": "leaders", "question": "Who led?", "sources": ["sales"], "tools": ["duckdb"], "featured": True},
    {
        "id": "laggards",
        "question": "Who lagged?",
        "sources": ["sales", "policies"],
        "tools": ["duckdb"],
        "featured": True,
    },
    {"id": "other", "question": "Anything?", "sources": ["policies"], "tools": ["retrieval"], "featured": False},
]
SOURCES = [
    {"id": "test-pack.policies", "kind": "documents"},
    {"id": "test-pack.sales", "kind": "structured"},
]
REPORTS = {"Who led?": "Austin led and Tulsa lagged [1].", "Who lagged?": "Nobody [1]."}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A checkout with one pack, its answer checks and an oracle, and the real tool registry."""
    pack = tmp_path / "data" / "packs" / "test-pack"
    (pack / "eval" / "oracles").mkdir(parents=True)
    (pack / "eval" / "oracles" / "leaders.sql").write_text("-- the leaders\nSELECT store FROM sales;\n")
    (pack / "eval" / "answers.yaml").write_text(ANSWERS)
    (tmp_path / "contracts").mkdir()
    shutil.copy(REPO / "contracts" / "tool-registry.json", tmp_path / "contracts")
    return tmp_path


@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    """A checkout with the tool registry only: the pack's files are not here."""
    (tmp_path / "contracts").mkdir()
    shutil.copy(REPO / "contracts" / "tool-registry.json", tmp_path / "contracts")
    return tmp_path


def deployment_routes(*, reports: dict[str, str] | None = None, sources: list[dict] | None = None) -> dict:
    """A deployment with two packs: test-pack (documents and structured sources) and the workspace."""
    jobs: dict[str, str] = {}
    answers = reports or REPORTS

    def submit(body, _headers):
        # As the API: the client's job id; the job's status and export routes exist from then on
        job_id = body["job_id"]
        if job_id in jobs:
            return 409, {"detail": f"Job already exists: {job_id}"}
        jobs[job_id] = body["input"]
        routes[("GET", f"/api/v1/jobs/async/job/{job_id}")] = ok({"job_id": job_id, "status": "success"})
        routes[("GET", f"/api/v1/jobs/async/job/{job_id}/export")] = lambda _body, _headers: (
            200,
            answer_turn(answers[jobs[job_id]]),
        )
        return 200, {"job_id": job_id, "status": "submitted"}

    def query(body, _headers):
        assert body["sql"].startswith("SELECT * FROM (\n-- the leaders") and body["sql"].endswith(") LIMIT 100")
        return 200, {"columns": ["store", "growth"], "rows": [["Austin", 0.2], ["Tulsa", -0.1]]}

    packs = [
        {"id": "test-pack", "kind": "industry", "title": "Test", "status": "ready"},
        {"id": "workspace", "kind": "workspace", "title": "Your data", "status": "empty"},
    ]
    routes = {
        ("GET", "/api/v1/packs"): ok({"packs": packs}),
        ("GET", "/api/v1/pack?id=test-pack"): ok({"id": "test-pack", "version": "1.0.0", "questions": QUESTIONS}),
        ("GET", "/api/v1/data_sources?pack=test-pack"): ok(sources or SOURCES),
        ("POST", "/api/v1/data_sources/test-pack.sales/query"): query,
        ("POST", "/api/v1/jobs/async/submit"): submit,
    }
    return routes


def submitted(seen) -> list[dict]:
    return [body for _, path, body, _ in seen if path.endswith("/submit")]


def test_a_run_asks_a_pack_s_checked_questions_with_its_catalog_sources_scores_and_reports(repo, tmp_path, capsys):
    out = tmp_path / "out"
    with serve(deployment_routes()) as (url, seen):
        code = cli.main(
            ["--repo", str(repo), "run", "--url", url, "--out", str(out), "--pack", "test-pack"]
            + ["--questions", "leaders,laggards"]
        )

    assert code == 0
    [directory] = out.iterdir()
    assert directory.name.startswith("test-pack-")
    assert [{key: value for key, value in body.items() if key != "job_id"} for body in submitted(seen)] == [
        {"input": "Who led?", "data_sources": ["test-pack.sales"], "pack_id": "test-pack"},
        {"input": "Who lagged?", "data_sources": ["test-pack.sales", "test-pack.policies"], "pack_id": "test-pack"},
    ]
    assert len({str(uuid.UUID(body["job_id"])) for body in submitted(seen)}) == 2  # each job's id, chosen by the client
    assert ("GET", "/api/v1/pack?id=test-pack") in [(method, path) for method, path, _, _ in seen]
    scores = {row["qid"]: row for row in json.loads((directory / "scores.json").read_text())}
    assert scores["leaders"]["det_pass"] is True
    assert scores["laggards"]["det_pass"] is True
    assert json.loads((directory / "oracles.json").read_text()) == {
        "leaders": [{"store": "Austin", "growth": 0.2}, {"store": "Tulsa", "growth": -0.1}]
    }
    assert json.loads((directory / "runs" / "laggards.1.json").read_text())["sources"] == [
        "test-pack.sales",
        "test-pack.policies",
    ]
    assert json.loads((directory / "meta.json").read_text())["grader"] is None
    report = (directory / "report.md").read_text()
    assert "| leaders | 1 | 1/1 |" in report and "Grader: off" in report
    printed = capsys.readouterr().out
    assert "2 of 2 runs pass" in printed


def test_without_a_pack_the_first_industry_the_deployment_lists_is_asked(repo, tmp_path):
    with serve(deployment_routes()) as (url, seen):
        code = cli.main(["--repo", str(repo), "run", "--url", url, "--out", str(tmp_path / "out")])

    assert code == 0
    assert ("GET", "/api/v1/packs") in [(method, path) for method, path, _, _ in seen]
    assert [body["pack_id"] for body in submitted(seen)] == ["test-pack"]


def test_by_default_the_checked_questions_run_and_a_wrong_answer_fails(repo, tmp_path, capsys):
    reports = REPORTS | {"Who led?": "Tulsa led [1]."}
    with serve(deployment_routes(reports=reports)) as (url, seen):
        code = cli.main(["--repo", str(repo), "run", "--url", url, "--out", str(tmp_path / "out"), "--runs", "2"])

    assert code == 1
    assert [body["input"] for body in submitted(seen)] == ["Who led?", "Who led?"]
    printed = capsys.readouterr().out
    assert "strongest_named (2)" in printed and "0 of 2 runs pass" in printed


def test_a_pack_without_an_answers_file_asks_its_featured_questions_with_the_generic_checks(
    bare_repo, tmp_path, capsys
):
    with serve(deployment_routes()) as (url, seen):
        code = cli.main(["--repo", str(bare_repo), "run", "--url", url, "--out", str(tmp_path / "out")])

    assert code == 0
    assert [body["input"] for body in submitted(seen)] == ["Who led?", "Who lagged?"]  # the featured ones
    assert "this checkout has no data/packs/test-pack" in capsys.readouterr().err
    [directory] = (tmp_path / "out").iterdir()
    scores = json.loads((directory / "scores.json").read_text())
    assert {name for row in scores for name in row["checks"]} == {
        "success",
        "cited",
        "declared_tools",
        "required_tools",
    }


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
        (["--pack", "retail"], "the deployment has no pack retail; it offers test-pack, workspace"),
        (["--questions", "nope"], "has no question nope"),
    ],
)
def test_a_request_that_does_not_fit_the_deployment_exits_64(repo, tmp_path, capsys, arguments, message):
    with serve(deployment_routes()) as (url, seen):
        code = cli.main(["--repo", str(repo), "run", "--url", url, "--out", str(tmp_path), *arguments])

    assert code == 64
    assert message in capsys.readouterr().err
    assert not submitted(seen)


def test_a_deployment_that_lists_no_industry_needs_a_pack_exits_64(repo, tmp_path, capsys):
    routes = deployment_routes()
    routes[("GET", "/api/v1/packs")] = ok({"packs": [{"id": "workspace", "kind": "workspace"}]})
    with serve(routes) as (url, _):
        assert cli.main(["--repo", str(repo), "run", "--url", url, "--out", str(tmp_path)]) == 64
    assert "offers no industry pack: give --pack" in capsys.readouterr().err


def test_an_unreachable_deployment_exits_69(repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    assert cli.main(["--repo", str(repo), "run", "--url", "http://127.0.0.1:9", "--out", str(tmp_path)]) == 69
    assert "did not answer" in capsys.readouterr().err


def test_the_deployment_is_the_ui_on_port_3300_unless_a_url_is_given(repo, tmp_path, monkeypatch):
    urls = []

    def run_eval(options, deployment, grader, log):
        urls.append(deployment.url)
        return tmp_path, []

    monkeypatch.setattr(cli, "run_eval", run_eval)
    cli.main(["--repo", str(repo), "run", "--out", str(tmp_path)])
    cli.main(["--repo", str(repo), "run", "--out", str(tmp_path), "--url", "http://spark:3300/"])

    assert urls == ["http://127.0.0.1:3300", "http://spark:3300"]


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
        job_id = Deployment(url).submit("Who led?", ["test-pack.sales"], "test-pack")

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
        assert "REFERENCE FACTS:\nStrongest first: store=Austin, growth=0.200; store=Tulsa" in user
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


def test_an_oracle_the_data_source_refuses_exits_64_before_any_question_is_asked(repo, tmp_path, capsys):
    routes = deployment_routes()
    routes[("POST", "/api/v1/data_sources/test-pack.sales/query")] = lambda b, h: (422, {"detail": "no table sales"})
    with serve(routes) as (url, seen):
        assert cli.main(["--repo", str(repo), "run", "--url", url, "--out", str(tmp_path / "out")]) == 64

    assert "oracle leaders (leaders.sql) failed" in capsys.readouterr().err
    assert not submitted(seen)


def test_a_pack_with_several_structured_sources_names_the_one_its_oracles_query(repo, tmp_path, capsys):
    sources = [*SOURCES, {"id": "test-pack.hr", "kind": "structured"}]
    with serve(deployment_routes(sources=sources)) as (url, seen):
        code = cli.main(["--repo", str(repo), "run", "--url", url, "--out", str(tmp_path / "out")])

    assert code == 64
    assert "needs one structured source to compute the oracles from; it has test-pack.sales, test-pack.hr" in (
        capsys.readouterr().err
    )
    assert not submitted(seen)

    (repo / "data" / "packs" / "test-pack" / "eval" / "answers.yaml").write_text("source: sales\n" + ANSWERS)
    with serve(deployment_routes(sources=sources)) as (url, seen):
        assert cli.main(["--repo", str(repo), "run", "--url", url, "--out", str(tmp_path / "out2")]) == 0
    assert [path for _, path, _, _ in seen if path.endswith("/query")] == ["/api/v1/data_sources/test-pack.sales/query"]


def test_the_structured_source_named_by_an_answers_file_must_be_one_of_the_pack_s():
    listed = [{"id": "p.sales", "kind": "structured"}, {"id": "p.docs", "kind": "documents"}]
    deployment = Deployment("http://deployment", request=lambda *args, **options: listed)

    assert structured_source(deployment, "p") == "p.sales"
    assert structured_source(deployment, "p", "sales") == structured_source(deployment, "p", "p.sales") == "p.sales"
    with pytest.raises(OracleError, match="names source docs, which is not a structured source of p"):
        structured_source(deployment, "p", "docs")
