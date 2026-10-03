# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""demo-eval run | report: the on-demand answer-quality eval that `scripts/demo.sh eval` runs.

Exit codes: 0 every run passed, 1 a run failed, 2 usage, 64 the request does not fit the deployment or the
configuration, 69 the deployment is unreachable.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import report
from .client import Deployment
from .client import HttpError
from .grader import Grader
from .grader import GraderConfigError
from .oracles import OracleError
from .runner import EvalConfigError
from .runner import EvalOptions
from .runner import run_eval
from .runner import score_dir
from .spec import SpecError

EXIT_FAILED, EXIT_CONFIG, EXIT_UNAVAILABLE = 1, 64, 69
REPO = Path(__file__).resolve().parents[3]
DEFAULT_URL = "http://127.0.0.1:3300"  # the UI, which proxies /api/v1 to the job API


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _questions(text: str) -> tuple[str, ...]:
    return tuple(q.strip() for q in text.replace(" ", ",").split(",") if q.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demo-eval", description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO, help="the repository checkout (data/packs, contracts)")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="ask a pack's questions on a deployment, then score and report")
    run.add_argument("--url", default=DEFAULT_URL, help=f"the deployment's UI (default {DEFAULT_URL})")
    run.add_argument("--pack", help="the pack to ask (default: the first industry the deployment lists)")
    run.add_argument("--runs", type=int, default=1, help="runs per question (default 1)")
    run.add_argument("--questions", type=_questions, default=(), help="question ids, comma-separated")
    run.add_argument("--out", type=Path, default=REPO / "eval" / "runs", help="where run directories go")
    run.add_argument("--max-wait", type=float, default=1260.0, help="seconds before a job is cancelled")

    again = commands.add_parser("report", help="score a saved run directory again (and grade what is ungraded)")
    again.add_argument("directory", type=Path)

    args = parser.parse_args(argv)
    try:
        grader = Grader.from_env()
        if args.command == "report":
            rows = score_dir(args.directory, args.repo, grader, _log)
            return _finish(rows, args.directory)
        if args.runs < 1:
            parser.error("--runs must be at least 1")
        if grader:
            _log(f"grader: {grader.model}, {grader.samples} samples per run")
        options = EvalOptions(
            url=args.url,
            repo=args.repo,
            out=args.out,
            runs=args.runs,
            pack=args.pack,
            questions=args.questions,
            max_wait=args.max_wait,
        )
        out, rows = run_eval(options, Deployment(args.url), grader, _log)
        return _finish(rows, out)
    except (EvalConfigError, GraderConfigError, OracleError, SpecError) as error:
        _log(f"error: {error}")
        return EXIT_CONFIG
    except HttpError as error:
        _log(f"error: the deployment did not answer: {error}")
        return EXIT_UNAVAILABLE


def _finish(rows: list[dict], out: Path) -> int:
    print(report.summary(rows))
    total = sum(report.passed(row) for row in rows)
    print(f"\n{total} of {len(rows)} runs pass. Report: {out / 'report.md'}")
    return 0 if rows and total == len(rows) else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
