# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""`demo.sh test gpu --perf`: the GPU performance guard, on a GPU host running the analytics-gpu profile.

Market tools: each case of the active pack's `eval/perf.yaml` goes to market-analytics' `POST /benchmark`, the route
behind the UI's Benchmark tab. It runs the call once untimed on each engine, then in alternating CPU/GPU pairs, and
compares the payloads as the GPU parity tests do. A case fails when the results differ, when the GPU engine is not
RAPIDS (cudf.pandas, cuml.accel or nx-cugraph), or when the speedup (median CPU time over median GPU time, the
tools' own compute timers) stays below the case's floor in two measurements.

Milvus: each workload profile of the comparison the guard measures just before (`demo-retrieval benchmark --guard`, the
CPU HNSW index against its GPU_IVF_FLAT copy, into `retrieval-benchmark-guard.json`; the Benchmark tab keeps serving the
`retrieval-benchmark.json` measured at `up`) fails when its recall and agreement gates fail or its CPU/GPU search-time
ratio is below the floor. A comparison measured before the guard started (`--measured-since`) fails too: measuring it
again did not succeed, and the file still holds an earlier measurement.

A floor is half the lowest speedup recorded on the A100 for that case, rounded down to 0.05: wide enough for
run-to-run noise on a shared host, narrow enough to catch the regressions seen so far (cudf.pandas falling back to
pandas made the GPU 3 to 10 times slower than the CPU; a CAGRA index found none of the right neighbors).
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from typing import Any

from .client import HttpError
from .client import request_json
from .report import table
from .spec import MarketCase
from .spec import PerfSpec
from .spec import RetrievalCase

RAPIDS = frozenset({"cudf.pandas", "cuml.accel", "nx-cugraph"})


class GuardUnavailable(RuntimeError):
    """Nothing to measure on: market analytics runs on the CPU only, or is not running."""


@dataclass(frozen=True)
class Outcome:
    case: str
    tool: str
    result: str  # PASS, FAIL, INFO (no floor) or SKIP
    speedup: float | None = None
    floor: float | None = None
    recorded: tuple[float, ...] = ()
    cpu_ms: float | None = None
    gpu_ms: float | None = None
    detail: str = ""


def _median(engine: dict[str, Any] | None) -> float | None:
    trials = (engine or {}).get("trials_ms") or []
    return statistics.median(trials) if trials else None


def judge_market(case: MarketCase, answer: dict[str, Any]) -> Outcome:
    """One /benchmark answer against the case's floor."""
    base = {"case": case.id, "tool": case.tool, "floor": case.min_speedup, "recorded": case.recorded}
    if not answer.get("available", True):
        raise GuardUnavailable(str(answer.get("reason") or "market analytics cannot compare engines here"))
    if answer.get("status") != "completed" or answer.get("parity") is not True:
        reason = answer.get("reason") or f"the comparison ended {answer.get('status')}"
        return Outcome(**base, result="FAIL", detail=str(reason)[:200])
    gpu = answer.get("gpu") or {}
    if gpu.get("device") != "gpu" or gpu.get("library") not in RAPIDS:
        detail = f"the GPU engine ran on {gpu.get('device')} with {gpu.get('library')}, not RAPIDS"
        return Outcome(**base, result="FAIL", detail=detail)
    cpu_ms, gpu_ms = _median(answer.get("cpu")), _median(gpu)
    if not cpu_ms or not gpu_ms:
        return Outcome(**base, result="FAIL", detail="no timed pairs")
    speedup = cpu_ms / gpu_ms
    measured = {"speedup": speedup, "cpu_ms": cpu_ms, "gpu_ms": gpu_ms}
    if case.min_speedup is None:
        return Outcome(**base, **measured, result="INFO", detail="reported only")
    if speedup < case.min_speedup:
        return Outcome(**base, **measured, result="FAIL", detail=f"below the floor of {case.min_speedup:.2f}x")
    return Outcome(**base, **measured, result="PASS")


def measure(
    url: str, case: MarketCase, *, pairs: int, budget_seconds: float, post: Callable[..., Any] = request_json
) -> dict[str, Any]:
    body = {"tool": case.tool, "arguments": case.arguments, "pairs": pairs, "budget_seconds": budget_seconds}
    try:
        return post("POST", f"{url.rstrip('/')}/benchmark", body, timeout=budget_seconds + 600, attempts=2)
    except HttpError as error:
        if error.status is None:
            raise GuardUnavailable(f"market analytics is not reachable at {url}: {error.detail}") from None
        return {"status": "failed", "reason": f"POST /benchmark answered {error.status}: {error.detail[:150]}"}


def market_case(
    url: str, case: MarketCase, *, pairs: int, budget_seconds: float, post: Callable[..., Any] = request_json
) -> Outcome:
    """Measure one case; a speedup below the floor is measured once more, and fails only if it stays below."""
    outcome = judge_market(case, measure(url, case, pairs=pairs, budget_seconds=budget_seconds, post=post))
    if outcome.result == "FAIL" and outcome.speedup is not None:
        again = judge_market(case, measure(url, case, pairs=pairs, budget_seconds=budget_seconds, post=post))
        if again.result != "FAIL" or again.speedup is None:
            return again
        best = max(outcome, again, key=lambda o: o.speedup or 0)
        return replace(best, detail=f"{best.detail}, in both of two measurements")
    return outcome


def _measured_at(benchmark: dict[str, Any]) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(benchmark.get("measuredAt")))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)  # the benchmark writes UTC


def _quality(quality: dict[str, Any]) -> str:
    """The profile's gates as measured, e.g. "recall CPU 1.000 GPU 0.993, overlap 0.988"."""
    values = [quality.get(key) for key in ("cpuRecallAtK", "gpuRecallAtK", "cpuGpuOverlapAtK")]
    if any(not isinstance(value, int | float) for value in values):
        return ""
    return "; recall CPU {:.3f} GPU {:.3f}, overlap {:.3f}".format(*values)


def judge_retrieval(
    case: RetrievalCase, benchmark: dict[str, Any] | None, measured_since: datetime | None = None
) -> Outcome:
    """One workload profile of the Milvus comparison; ``measured_since``: when the guard measured it again."""
    base = {"case": f"milvus {case.profile_id}", "tool": "retrieve_evidence", "floor": case.min_speedup}
    base |= {"recorded": case.recorded}
    logs = "./scripts/demo.sh logs retrieval-benchmark milvus-gpu"
    if benchmark is None:
        return Outcome(**base, result="FAIL", detail=f"no Milvus comparison for this build ({logs})")
    measured_at = _measured_at(benchmark)
    if measured_since is not None and (measured_at is None or measured_at < measured_since):
        detail = f"measured at {benchmark.get('measuredAt')}, before this check: measuring it again failed ({logs})"
        return Outcome(**base, result="FAIL", detail=detail)
    profile = next((p for p in benchmark.get("profiles", []) if p.get("profileId") == case.profile_id), None)
    if profile is None:
        return Outcome(**base, result="FAIL", detail=f"the Milvus comparison has no {case.profile_id} profile")
    cpu, gpu, quality = profile.get("cpu") or {}, profile.get("gpu") or {}, profile.get("quality") or {}
    indexes, gates = f"{cpu.get('indexType')} vs {gpu.get('indexType')}", _quality(quality)
    if cpu.get("status") != "completed" or gpu.get("status") != "completed":
        return Outcome(**base, result="FAIL", detail=f"{indexes}: a search failed")
    if not quality.get("passed"):
        reasons = "; ".join(quality.get("failureReasons") or []) or "recall or agreement below the gates"
        return Outcome(**base, result="FAIL", detail=f"{indexes}: {reasons}{gates}"[:200])
    cpu_ms, gpu_ms = cpu.get("vectorSearchMs"), gpu.get("vectorSearchMs")
    if not cpu_ms or not gpu_ms:
        return Outcome(**base, result="FAIL", detail=f"{indexes}: no search time")
    speedup = cpu_ms / gpu_ms
    measured = {"speedup": speedup, "cpu_ms": cpu_ms, "gpu_ms": gpu_ms}
    if case.min_speedup is None:
        return Outcome(**base, **measured, result="INFO", detail=f"{indexes}{gates}")
    if speedup < case.min_speedup:
        return Outcome(**base, **measured, result="FAIL", detail=f"{indexes}: below the floor{gates}")
    return Outcome(**base, **measured, result="PASS", detail=f"{indexes}{gates}")


def guard(
    spec: PerfSpec,
    build: dict[str, Any],
    *,
    analytics_url: str,
    retrieval: dict[str, Any] | None,
    retrieval_expected: bool,
    retrieval_since: datetime | None = None,
    pairs: int = 5,
    budget_seconds: float = 60.0,
    post: Callable[..., Any] = request_json,
    log: Callable[[str], None] = print,
) -> list[Outcome]:
    """Every case of the pack: the market cases, then the Milvus profiles."""
    if spec.profile and build.get("profile") != spec.profile:
        detail = f"the cases are for the {spec.profile} profile; this build is {build.get('profile')}"
        return [Outcome(case.id, case.tool, "SKIP", detail=detail) for case in spec.market]
    outcomes = []
    for case in spec.market:
        log(f"measuring {case.id} ({case.tool})")
        outcomes.append(market_case(analytics_url, case, pairs=pairs, budget_seconds=budget_seconds, post=post))
    for case in spec.retrieval:
        if not retrieval_expected:
            detail = "the stack runs without the retrieval profile"
            outcomes.append(Outcome(f"milvus {case.profile_id}", "retrieve_evidence", "SKIP", detail=detail))
        else:
            outcomes.append(judge_retrieval(case, retrieval, retrieval_since))
    return outcomes


def render(outcomes: list[Outcome]) -> str:
    def number(value: float | None, unit: str = "", digits: int = 1) -> str:
        return "-" if value is None else f"{value:,.{digits}f}{unit}"

    rows = [
        [
            outcome.case,
            outcome.tool,
            number(outcome.cpu_ms),
            number(outcome.gpu_ms),
            number(outcome.speedup, "x", 2),
            number(outcome.floor, "x", 2),
            ", ".join(f"{x:g}x" for x in outcome.recorded) or "-",
            outcome.result,
            outcome.detail or "-",
        ]
        for outcome in outcomes
    ]
    headers = ["Case", "Tool", "CPU ms", "GPU ms", "Speedup", "Floor", "Recorded (A100)", "Result", "Detail"]
    return table(headers, rows)
