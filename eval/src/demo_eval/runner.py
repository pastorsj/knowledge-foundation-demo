# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""`demo.sh eval`: ask a pack's questions on a running deployment, then score and report every run.

Each question is one fresh job with the question's own sources (the pack's catalog ids, `<pack>.<source>`) and the
pack id, as the UI's cards send it, one at a time. Every run is saved before it is scored, so a run directory can be
scored again (`demo-eval report DIR`) after a change to the checks, without asking anything again:

    <out>/<pack>-<UTC time>/
      meta.json              the deployment, the questions, the grader model (never its key or URL)
      oracles.json           the oracle rows, computed on the deployment's own data
      runs/<qid>.<n>.json    the job's status, wall time and export (report, events, receipts)
      grades/<qid>.<n>.json  the grader's samples, when the grader is on
      scores.json, report.md

A run directory holds the deployment's answers and evidence: keep it off the repository (`eval/runs/` is ignored)
and delete it when done.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from . import facts
from . import oracles as oracle_queries
from . import report
from .client import Deployment
from .client import HttpError
from .client import wait
from .grader import Grader
from .score import Registry
from .score import score
from .spec import AnswerSpec
from .spec import catalog_id
from .spec import load_answers


class EvalConfigError(ValueError):
    """The request does not fit the deployment (an unknown pack or question)."""


@dataclass(frozen=True)
class EvalOptions:
    url: str
    repo: Path
    out: Path
    runs: int = 1
    pack: str | None = None  # default: the first industry the deployment lists
    questions: tuple[str, ...] = ()
    max_wait: float = 1260.0  # the API's 1,200 s job deadline, and a minute for it to report


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _answered(error: HttpError) -> bool:
    """The deployment answered with a client error (a request that does not fit it), rather than being unreachable."""
    return error.status is not None and error.status < 500


def resolve_pack(deployment: Deployment, requested: str | None) -> str:
    """The pack to evaluate: the requested one, else the first industry the deployment lists."""
    if requested:
        return requested
    try:
        packs = deployment.packs()
    except HttpError as error:
        if not _answered(error):
            raise
        raise EvalConfigError(f"the deployment does not list its packs ({error}): give --pack") from None
    industries = [str(pack["id"]) for pack in packs if pack.get("kind") == "industry"]
    if not industries:
        raise EvalConfigError("the deployment offers no industry pack: give --pack")
    return industries[0]


def load_pack(deployment: Deployment, pack_id: str) -> dict[str, Any]:
    """The pack's view on the deployment; an unknown pack is a configuration error that names the ones it has."""
    try:
        return deployment.pack(pack_id)
    except HttpError as error:
        if not _answered(error):
            raise
        try:
            offered = ", ".join(str(pack["id"]) for pack in deployment.packs()) or "none"
        except HttpError:
            offered = "unknown"
        raise EvalConfigError(f"the deployment has no pack {pack_id}; it offers {offered}") from None


def select_questions(pack: dict[str, Any], spec: AnswerSpec, requested: tuple[str, ...]) -> list[dict[str, Any]]:
    """The requested questions, in that order; by default every one with answer checks, else the featured ones."""
    available = {question["id"]: question for question in pack.get("questions", [])}
    if requested:
        if unknown := [qid for qid in requested if qid not in available]:
            raise EvalConfigError(
                f"pack {pack.get('id')} on this deployment has no question {', '.join(unknown)}; "
                f"it offers {', '.join(available) or 'none'}"
            )
        return [available[qid] for qid in requested]
    checked = [question for question in available.values() if spec.question(question["id"])]
    return checked or [question for question in available.values() if question.get("featured")]


def question_sources(pack_id: str, question: dict[str, Any]) -> list[str]:
    """The question's sources as catalog ids (`<pack>.<id>`); `questions.yaml` lists the pack's own ids."""
    return [catalog_id(pack_id, str(source)) for source in question.get("sources") or []]


def ask(
    deployment: Deployment,
    pack_id: str,
    question: dict[str, Any],
    *,
    max_wait: float,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """One question as a fresh job: its status, wall time and export."""
    started = clock()
    job_id = deployment.submit(question["question"], question_sources(pack_id, question), pack_id)
    try:
        status = wait(deployment, job_id, max_seconds=max_wait)
    except KeyboardInterrupt:
        deployment.cancel(job_id)
        raise
    wall = round(clock() - started, 1)
    try:
        turn = deployment.export(job_id)
    except HttpError as error:
        turn, status = {}, {**status, "export_error": str(error)[:300]}
    return {
        "job_id": job_id,
        "submitted_at": datetime.fromtimestamp(started, UTC).isoformat(),
        "wall_seconds": wall,
        "status": status,
        "turn": turn,
    }


def run_eval(
    options: EvalOptions,
    deployment: Deployment,
    grader: Grader | None,
    log: Callable[[str], None] = print,
) -> tuple[Path, list[dict[str, Any]]]:
    pack_id = resolve_pack(deployment, options.pack)
    pack = load_pack(deployment, pack_id)
    pack_dir = options.repo / "data" / "packs" / pack_id
    if not pack_dir.is_dir():
        log(f"this checkout has no data/packs/{pack_id}: only the generic checks apply")
    spec = load_answers(pack_dir)
    questions = select_questions(pack, spec, options.questions)
    if not questions:
        raise EvalConfigError(f"pack {pack_id} offers no questions on this deployment")

    out = options.out / f"{pack_id}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    (out / "runs").mkdir(parents=True)
    host = urllib.parse.urlsplit(deployment.url).netloc
    meta = {
        "pack": pack_id,
        "pack_version": pack.get("version"),
        "deployment": host,
        "started_at": _now(),
        "runs": options.runs,
        "questions": [question["id"] for question in questions],
        "grader": f"{grader.model}, {grader.samples} samples" if grader else None,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    log(f"eval of {pack_id} on {host}: {len(questions)} question(s) x {options.runs}; results in {out}")

    specs = [spec.question(question["id"]) for question in questions]
    used = [question_spec for question_spec in specs if question_spec and question_spec.oracles]
    oracles = {}
    if used:
        source = oracle_queries.structured_source(deployment, pack_id, spec.source)
        oracles = oracle_queries.compute(deployment, source, pack_dir, used)
    (out / "oracles.json").write_text(json.dumps(oracles, indent=1, default=str))

    for n in range(1, options.runs + 1):
        for question in questions:
            run = {"pack": pack_id, "qid": question["id"], "run": n, "question": question["question"]}
            run |= {"sources": question_sources(pack_id, question), "declared_tools": question.get("tools") or []}
            try:
                run |= ask(deployment, pack_id, question, max_wait=options.max_wait)
            except HttpError as error:  # this question could not be asked; the others still are
                run |= {"job_id": None, "wall_seconds": None, "turn": {}}
                run |= {"status": {"status": "error", "error": str(error)[:300]}}
            (out / "runs" / f"{question['id']}.{n}.json").write_text(json.dumps(run))
            took = f" in {run['wall_seconds']:.0f} s" if run["wall_seconds"] is not None else ""
            log(f"{_now()} {question['id']}.{n}: {run['status'].get('status')}{took}")
    return out, score_dir(out, options.repo, grader, log)


def score_dir(out: Path, repo: Path, grader: Grader | None, log: Callable[[str], None] = print) -> list[dict[str, Any]]:
    """Score (and, with the grader on, grade) every saved run of a run directory; writes scores.json and report.md."""
    meta = json.loads((out / "meta.json").read_text())
    spec = load_answers(repo / "data" / "packs" / meta["pack"])
    registry = Registry.load(repo)
    oracles = json.loads((out / "oracles.json").read_text()) if (out / "oracles.json").is_file() else {}
    rows = []
    order = {qid: index for index, qid in enumerate(meta.get("questions") or [])}

    def position(path: Path) -> tuple[int, int, str]:
        qid, run, _ = path.name.rsplit(".", 2)
        return int(run), order.get(qid, len(order)), qid

    for path in sorted((out / "runs").glob("*.json"), key=position):
        run = json.loads(path.read_text())
        question = spec.question(run["qid"])
        row = score(run, question, declared=run.get("declared_tools") or [], oracles=oracles, registry=registry)
        graded = out / "grades" / path.name
        if not graded.is_file() and grader is not None:
            reference = facts.render(spec, question, oracles) if question else ""
            verdict = grader.grade(dataset=spec.dataset, question=run["question"], facts=reference, run=run)
            if verdict is not None:
                graded.parent.mkdir(exist_ok=True)
                graded.write_text(json.dumps(verdict, indent=1))
            else:
                log(f"{run['qid']}.{run['run']}: the grader did not answer ({'; '.join(grader.errors[-1:])})")
        row["graded"] = grader is not None or graded.is_file()
        row["grade"] = json.loads(graded.read_text()) if graded.is_file() else None
        rows.append(row)
    if grader is not None and not meta.get("grader"):
        meta["grader"] = f"{grader.model}, {grader.samples} samples"
    (out / "scores.json").write_text(json.dumps(rows, indent=1, default=str))
    (out / "report.md").write_text(report.markdown(rows, meta))
    return rows
