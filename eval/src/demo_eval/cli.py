# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""demo-eval run | report | perf: the on-demand checks `scripts/demo.sh eval` and `test gpu --perf` run.

Exit codes: 0 every run or case passed, 1 one failed, 2 usage, 64 the request does not fit the deployment or the
configuration, 69 the deployment or service is unreachable.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import perf
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
from .spec import load_perf

EXIT_FAILED, EXIT_CONFIG, EXIT_UNAVAILABLE = 1, 64, 69
REPO = Path(__file__).resolve().parents[3]


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _utc_time(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 time: {text}") from None
    if moment.tzinfo is None:
        raise argparse.ArgumentTypeError(f"give the time zone, e.g. {text}Z")
    return moment


def _questions(text: str) -> tuple[str, ...]:
    return tuple(q.strip() for q in text.replace(" ", ",").split(",") if q.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demo-eval", description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO, help="the repository checkout (data/packs, contracts)")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="ask the pack's questions on a deployment, then score and report")
    run.add_argument("--url", required=True, help="the deployment's UI, e.g. http://127.0.0.1:3100")
    run.add_argument("--pack", help="fail unless the deployment runs this pack")
    run.add_argument("--runs", type=int, default=1, help="runs per question (default 1)")
    run.add_argument("--questions", type=_questions, default=(), help="question ids, comma-separated")
    run.add_argument("--out", type=Path, default=REPO / "eval" / "runs", help="where run directories go")
    run.add_argument("--max-wait", type=float, default=1260.0, help="seconds before a job is cancelled")

    again = commands.add_parser("report", help="score a saved run directory again (and grade what is ungraded)")
    again.add_argument("directory", type=Path)

    guard = commands.add_parser("perf", help="the GPU performance guard (demo.sh test gpu --perf)")
    guard.add_argument("--analytics-url", default="http://127.0.0.1:3010", help="market analytics (GPU service)")
    guard.add_argument("--build", type=Path, required=True, help="the active build's pack.json")
    guard.add_argument(
        "--retrieval-benchmark",
        type=Path,
        help="the Milvus comparison measured for the guard (retrieval-benchmark-guard.json of the active build)",
    )
    guard.add_argument(
        "--measured-since",
        type=_utc_time,
        help="when the Milvus comparison was measured again (ISO 8601 UTC); an earlier measurement fails",
    )
    guard.add_argument("--no-retrieval", action="store_true", help="the stack runs without the retrieval profile")
    guard.add_argument("--pairs", type=int, default=5, help="timed CPU/GPU pairs per case (default 5)")
    guard.add_argument("--budget", type=float, default=60.0, help="seconds of timed pairs per case (default 60)")

    args = parser.parse_args(argv)
    try:
        if args.command == "perf":
            return _perf(args)
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
    except perf.GuardUnavailable as error:
        _log(f"error: {error}")
        return EXIT_UNAVAILABLE
    except HttpError as error:
        _log(f"error: the deployment did not answer: {error}")
        return EXIT_UNAVAILABLE


def _finish(rows: list[dict], out: Path) -> int:
    print(report.summary(rows))
    total = sum(report.passed(row) for row in rows)
    print(f"\n{total} of {len(rows)} runs pass. Report: {out / 'report.md'}")
    return 0 if rows and total == len(rows) else EXIT_FAILED


def _perf(args: argparse.Namespace) -> int:
    build = json.loads(args.build.read_text())
    spec = load_perf(args.repo / "data" / "packs" / str(build.get("id")))
    if spec is None:
        _log(f"pack {build.get('id')} has no GPU guard cases (eval/perf.yaml): nothing to check")
        return 0
    retrieval = None
    if args.retrieval_benchmark and args.retrieval_benchmark.is_file() and args.retrieval_benchmark.stat().st_size:
        retrieval = json.loads(args.retrieval_benchmark.read_text())
    outcomes = perf.guard(
        spec,
        build,
        analytics_url=args.analytics_url,
        retrieval=retrieval,
        retrieval_expected=not args.no_retrieval,
        retrieval_since=args.measured_since,
        pairs=args.pairs,
        budget_seconds=args.budget,
        log=_log,
    )
    print(perf.render(outcomes))
    failed = [outcome for outcome in outcomes if outcome.result == "FAIL"]
    skipped = [outcome for outcome in outcomes if outcome.result == "SKIP"]
    print(
        f"\nGPU guard on {build.get('build') or build.get('id')}: {len(failed)} failed, "
        f"{sum(o.result == 'PASS' for o in outcomes)} passed, {len(skipped)} skipped"
    )
    return EXIT_FAILED if failed else 0


if __name__ == "__main__":
    sys.exit(main())
