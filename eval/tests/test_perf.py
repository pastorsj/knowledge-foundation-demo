# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The GPU performance guard: each case against its floor, a second measurement before failing, Milvus gates."""

import json
from datetime import UTC
from datetime import datetime
from itertools import count

import pytest
from support import REPO
from support import serve

from demo_eval import cli
from demo_eval import perf
from demo_eval.spec import MarketCase
from demo_eval.spec import PerfSpec
from demo_eval.spec import RetrievalCase
from demo_eval.spec import load_perf

CASE = MarketCase("scan", "market_scan", {"universe_id": "all_assets"}, (3.0, 3.7), 1.5)


def answer(cpu_ms: float, gpu_ms: float, *, library: str = "cudf.pandas", parity: bool = True, **extra) -> dict:
    return {
        "available": True,
        "status": "completed" if parity else "mismatch",
        "parity": parity,
        "reason": None if parity else "The CPU and GPU results differ at payload.rows[0].asset_id.",
        "cpu": {"device": "cpu", "library": "pandas", "trials_ms": [cpu_ms, cpu_ms + 2, cpu_ms - 2]},
        "gpu": {"device": "gpu", "library": library, "trials_ms": [gpu_ms, gpu_ms + 1, gpu_ms - 1]},
    } | extra


def test_a_case_passes_at_or_above_its_floor_and_fails_below():
    passed = perf.judge_market(CASE, answer(230, 77))
    assert (passed.result, round(passed.speedup, 2)) == ("PASS", 2.99)
    failed = perf.judge_market(CASE, answer(100, 90))
    assert failed.result == "FAIL" and "below the floor of 1.50x" in failed.detail


@pytest.mark.parametrize(
    ("reply", "detail"),
    [
        (answer(230, 77, parity=False), "differ at payload.rows[0].asset_id"),
        (answer(230, 77, library="pandas"), "not RAPIDS"),
        ({"available": True, "status": "failed", "reason": "bad window", "cpu": None, "gpu": None}, "bad window"),
    ],
)
def test_a_mismatch_a_cpu_fallback_or_a_failed_call_fails_whatever_the_speed(reply, detail):
    outcome = perf.judge_market(CASE, reply)
    assert outcome.result == "FAIL" and detail in outcome.detail


def test_a_case_without_a_floor_is_reported_only():
    case = MarketCase("news", "analyze_news_price_relationship", {}, (8.1,), None)
    outcome = perf.judge_market(case, answer(50, 90))
    assert outcome.result == "INFO" and outcome.speedup == pytest.approx(50 / 90)


def test_a_cpu_only_service_stops_the_guard():
    with pytest.raises(perf.GuardUnavailable, match="CPU only"):
        perf.judge_market(CASE, {"available": False, "reason": "This analytics service runs on the CPU only."})


def test_a_slow_case_is_measured_again_and_fails_only_if_it_stays_slow():
    replies = iter([answer(100, 90), answer(230, 77)])
    outcome = perf.market_case("http://x", CASE, pairs=5, budget_seconds=30, post=lambda *a, **k: next(replies))
    assert outcome.result == "PASS"

    replies = iter([answer(100, 90), answer(110, 90)])
    outcome = perf.market_case("http://x", CASE, pairs=5, budget_seconds=30, post=lambda *a, **k: next(replies))
    assert outcome.result == "FAIL" and outcome.detail.endswith("in both of two measurements")
    assert outcome.speedup == pytest.approx(110 / 90)  # the better of the two


def milvus(single=1.2, *, passed=True) -> dict:
    def profile(profile_id: str, ratio: float) -> dict:
        backend = {"status": "completed", "indexType": "HNSW", "vectorSearchMs": 100.0 * ratio}
        gpu = {"status": "completed", "indexType": "GPU_IVF_FLAT", "vectorSearchMs": 100.0}
        quality = {"passed": passed, "failureReasons": [] if passed else ["gpu recall@10 0.00 below 0.95"]}
        return {"profileId": profile_id, "cpu": backend, "gpu": gpu, "quality": quality}

    return {"profiles": [profile("vector-single", single), profile("vector-batch", 1.1)]}


def test_milvus_profiles_need_their_quality_gates_and_floor():
    single = RetrievalCase("vector-single", (1.13,), 0.55)
    assert perf.judge_retrieval(single, milvus()).result == "PASS"
    assert perf.judge_retrieval(single, milvus(0.5)).result == "FAIL"
    gated = perf.judge_retrieval(single, milvus(passed=False))
    assert gated.result == "FAIL" and "recall@10 0.00" in gated.detail
    assert "no Milvus comparison" in perf.judge_retrieval(single, None).detail
    assert (
        "no vector-concurrency"
        in perf.judge_retrieval(RetrievalCase("vector-concurrency", (1.7,), 0.85), milvus()).detail
    )


def test_a_milvus_row_reports_the_recall_and_overlap_it_measured():
    single = RetrievalCase("vector-single", (1.13,), 0.55)
    measured = milvus()
    measured["profiles"][0]["quality"] |= {"cpuRecallAtK": 1.0, "gpuRecallAtK": 0.9933, "cpuGpuOverlapAtK": 0.9879}

    outcome = perf.judge_retrieval(single, measured)

    assert outcome.detail == "HNSW vs GPU_IVF_FLAT; recall CPU 1.000 GPU 0.993, overlap 0.988"
    assert perf.judge_retrieval(single, milvus()).detail == "HNSW vs GPU_IVF_FLAT"  # nothing measured: no numbers


def test_a_milvus_comparison_measured_before_the_guard_fails():
    """`benchmark --guard` failed and left the earlier measurement: it must not pass as a new one."""
    single = RetrievalCase("vector-single", (1.13,), 0.55)
    since = datetime(2026, 10, 2, 3, 0, tzinfo=UTC)
    fresh = milvus() | {"measuredAt": "2026-10-02T03:00:05.123456Z"}
    assert perf.judge_retrieval(single, fresh, since).result == "PASS"
    stale = perf.judge_retrieval(single, milvus() | {"measuredAt": "2026-10-01T18:33:12.5Z"}, since)
    assert stale.result == "FAIL" and "before this check: measuring it again failed" in stale.detail
    assert perf.judge_retrieval(single, milvus(), since).result == "FAIL"  # no measuredAt at all
    assert perf.judge_retrieval(single, milvus()).result == "PASS"  # no start time given: not checked


def test_another_profile_or_no_retrieval_skips_instead_of_failing():
    spec = PerfSpec("p", "standard", (CASE,), (RetrievalCase("vector-single", (1.1,), 0.55),))
    skipped = perf.guard(spec, {"profile": "ci"}, analytics_url="http://x", retrieval=None, retrieval_expected=True)
    assert [o.result for o in skipped] == ["SKIP"] and "for the standard profile" in skipped[0].detail

    outcomes = perf.guard(
        spec,
        {"profile": "standard"},
        analytics_url="http://x",
        retrieval=None,
        retrieval_expected=False,
        post=lambda *a, **k: answer(230, 77),
        log=lambda _m: None,
    )
    assert [o.result for o in outcomes] == ["PASS", "SKIP"]


def test_the_command_measures_the_active_packs_cases_and_prints_a_table(tmp_path, capsys):
    spec = load_perf(REPO / "data" / "packs" / "synthetic-market")
    build = tmp_path / "pack.json"
    build.write_text(json.dumps({"id": "synthetic-market", "profile": "standard", "build": "synthetic-market@x"}))
    retrieval = tmp_path / "retrieval-benchmark.json"
    profiles = milvus()["profiles"] + [dict(milvus()["profiles"][0], profileId="vector-concurrency")]
    retrieval.write_text(json.dumps({"profiles": profiles}))
    calls = count()

    def benchmark(body, _headers):
        next(calls)
        assert body["pairs"] == 3 and body["budget_seconds"] == 20
        return 200, answer(300, 60, library="cuml.accel" if "anomaly" in body["tool"] else "cudf.pandas")

    with serve({("POST", "/benchmark"): benchmark}) as (url, _):
        code = cli.main(
            ["perf", "--analytics-url", url, "--build", str(build), "--retrieval-benchmark", str(retrieval)]
            + ["--pairs", "3", "--budget", "20"]
        )

    printed = capsys.readouterr().out
    assert code == 0, printed
    assert next(calls) == len(spec.market)
    assert "| scan-all-issuers | market_scan |" in printed and "| milvus vector-concurrency |" in printed
    assert "0 failed" in printed

    # The same file, against a start time after its measurement: every Milvus profile fails
    retrieval.write_text(json.dumps({"profiles": profiles, "measuredAt": "2026-10-01T18:33:12Z"}))
    with serve({("POST", "/benchmark"): benchmark}) as (url, _):
        code = cli.main(
            ["perf", "--analytics-url", url, "--build", str(build), "--retrieval-benchmark", str(retrieval)]
            + ["--measured-since", "2026-10-02T03:00:00Z", "--pairs", "3", "--budget", "20"]
        )
    printed = capsys.readouterr().out
    assert code == 1 and f"{len(spec.retrieval)} failed" in printed


def test_the_command_exits_69_on_a_cpu_only_stack_and_0_for_a_pack_without_cases(tmp_path, capsys):
    build = tmp_path / "pack.json"
    build.write_text(json.dumps({"id": "synthetic-market", "profile": "standard"}))
    cpu_only = {"available": False, "reason": "This analytics service runs on the CPU only."}
    with serve({("POST", "/benchmark"): lambda b, h: (200, cpu_only)}) as (url, _):
        assert cli.main(["perf", "--analytics-url", url, "--build", str(build), "--no-retrieval"]) == 69
    assert "CPU only" in capsys.readouterr().err

    build.write_text(json.dumps({"id": "no-such-pack", "profile": "x"}))
    assert cli.main(["perf", "--build", str(build)]) == 0
    assert "no GPU guard cases" in capsys.readouterr().err
