# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""retrieval-benchmark: the GPU copy of the build, the timed profiles, the quality gates and the claim, the build's
query vectors, and the GPU guard's own measurement.

Milvus Lite stands in for both servers here, with a FLAT index for the GPU copy; the box runs GPU_IVF_FLAT.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from pymilvus import MilvusClient

from demo_retrieval import __main__ as cli
from demo_retrieval import benchmark
from demo_retrieval import store
from demo_retrieval.datapack import BenchmarkQuery
from demo_retrieval.datapack import CollectionManifest
from demo_retrieval.settings import Settings

FLAT = {"index_type": "FLAT", "metric_type": "IP", "params": {}}
FLAT_SEARCH = {"metric_type": "IP", "params": {}}
QUERIES = [
    {"query": "cybersecurity incident disclosure", "sources": ["market_news"]},
    {"query": "merger agreement", "sources": ["market_news"]},
    {"query": "share buyback", "sources": ["market_regulations"]},
    {"query": "dividend increase", "sources": ["market_news", "market_regulations"]},
]


@pytest.fixture
def gpu_settings(settings: Settings, tmp_path: Path) -> Settings:
    return replace(settings, milvus_gpu_uri=str(tmp_path / "milvus-gpu.db"))


@pytest.fixture
def with_queries(data_dir: Path, manifest: CollectionManifest) -> Path:
    pack = json.loads((data_dir / "pack.json").read_text())
    pack["documents"]["benchmark_queries"] = QUERIES
    (data_dir / "pack.json").write_text(json.dumps(pack))
    return data_dir


def test_the_comparison_mirrors_the_build_and_measures_every_profile(gpu_settings, with_queries, manifest):
    result = benchmark.run(gpu_settings, with_queries, gpu_index=FLAT, gpu_search_params=FLAT_SEARCH)

    assert result == json.loads((with_queries / "retrieval-benchmark.json").read_text())
    assert (result["collection"], result["collectionVersion"]) == (manifest.collection, manifest.physical_collection)
    assert (result["queryCount"], result["sourceIds"]) == (4, ["market_news", "market_regulations"])
    assert [p["profileId"] for p in result["profiles"]] == ["vector-single", "vector-batch", "vector-concurrency"]
    single, batch, concurrent = result["profiles"]
    assert (single["cpu"]["indexType"], single["gpu"]["indexType"]) == ("HNSW", "FLAT")
    # 4 queries x 3 repetitions; a batch holds one scope's queries, so 3 requests per repetition
    assert (single["cpu"]["requestCount"], single["cpu"]["vectorCount"]) == (12, 12)
    assert (batch["gpu"]["requestCount"], batch["gpu"]["vectorCount"]) == (9, 12)
    assert (concurrent["batchSize"], concurrent["concurrency"]) == (1, 5)
    for profile in result["profiles"]:
        assert profile["cpu"]["status"] == profile["gpu"]["status"] == "completed"
        assert profile["quality"] == {
            "passed": True,
            "cpuRecallAtK": 1.0,
            "gpuRecallAtK": 1.0,
            "cpuGpuOverlapAtK": 1.0,
            "failureReasons": [],
        }
        assert profile["claim"]["observedCpuOverGpuRatio"] > 0

    # The GPU Milvus holds one copy: this build's normalized vectors
    gpu = MilvusClient(uri=gpu_settings.milvus_gpu_uri)
    assert gpu.list_collections() == [manifest.physical_collection]
    assert gpu.get_collection_stats(manifest.physical_collection)["row_count"] == manifest.chunk_count
    gpu.close()

    # The build's query vectors stay beside the result; the GPU guard's file is not written
    assert (with_queries / "retrieval-benchmark-queries.npz").is_file()
    assert not (with_queries / "retrieval-benchmark-guard.json").exists()


def test_an_unchanged_build_is_not_measured_again(gpu_settings, with_queries, nvidia_api):
    first = benchmark.run(gpu_settings, with_queries, gpu_index=FLAT, gpu_search_params=FLAT_SEARCH)
    nvidia_api.calls.clear()

    assert benchmark.run(gpu_settings, with_queries, gpu_index=FLAT, gpu_search_params=FLAT_SEARCH) == first
    assert nvidia_api.calls == []


def test_the_guard_measures_into_its_own_file_and_never_changes_what_the_api_serves(
    monkeypatch, gpu_settings, with_queries, nvidia_api
):
    """`demo-retrieval benchmark --guard` (`demo.sh test gpu --perf`): new timings on the same copy and query vectors,
    in retrieval-benchmark-guard.json. A failing gate there leaves the Benchmark tab's comparison as `up` wrote it."""
    first = benchmark.run(gpu_settings, with_queries, gpu_index=FLAT, gpu_search_params=FLAT_SEARCH)
    served = (with_queries / "retrieval-benchmark.json").read_bytes()
    saved_queries = (with_queries / "retrieval-benchmark-queries.npz").read_bytes()
    nvidia_api.calls.clear()
    monkeypatch.setattr(benchmark, "MIN_RECALL", 1.01)  # no index can pass: the guard's measurement fails its gate

    guarded = benchmark.run(gpu_settings, with_queries, gpu_index=FLAT, gpu_search_params=FLAT_SEARCH, guard=True)

    assert nvidia_api.calls == []  # the build's query vectors, not new embeddings
    assert guarded == json.loads((with_queries / "retrieval-benchmark-guard.json").read_text())
    assert guarded["measuredAt"] >= first["measuredAt"]
    assert [p["profileId"] for p in guarded["profiles"]] == [p["profileId"] for p in first["profiles"]]
    assert {p["claim"]["decision"] for p in guarded["profiles"]} == {"no_claim"}
    assert all("cpu_recall_below_1.01" in p["quality"]["failureReasons"] for p in guarded["profiles"])
    assert (with_queries / "retrieval-benchmark.json").read_bytes() == served
    assert (with_queries / "retrieval-benchmark-queries.npz").read_bytes() == saved_queries
    assert all(p["quality"]["passed"] for p in json.loads(served)["profiles"])


def test_every_measurement_of_a_build_searches_the_same_query_vectors(monkeypatch, gpu_settings, with_queries):
    """The hosted model's vector for one text differs in its last bits from call to call: the build's queries are
    embedded once, so the guard measures exactly what `up` measured."""
    embedded: list[int] = []
    rng = np.random.default_rng(7)

    def jittered(embedder, queries):
        embedded.append(len(queries))
        return [v + rng.normal(0, 1e-7, v.shape).astype(np.float32) for v in benchmark_embed(embedder, queries)]

    benchmark_embed = benchmark.embed_queries
    monkeypatch.setattr(benchmark, "embed_queries", jittered)
    first = benchmark.run(gpu_settings, with_queries, gpu_index=FLAT, gpu_search_params=FLAT_SEARCH)
    guarded = [
        benchmark.run(gpu_settings, with_queries, gpu_index=FLAT, gpu_search_params=FLAT_SEARCH, guard=True)
        for _ in range(2)
    ]

    assert embedded == [len(QUERIES)]
    assert [p["quality"] for p in first["profiles"]] == [p["quality"] for p in guarded[0]["profiles"]]
    assert [p["quality"] for p in guarded[0]["profiles"]] == [p["quality"] for p in guarded[1]["profiles"]]


def test_a_comparison_measured_without_the_builds_query_vectors_is_measured_again(
    gpu_settings, with_queries, nvidia_api
):
    """A result from before the query vectors were kept (an earlier version: a new embedding each time, rounded
    scores) is measured again by the next `up`. The guard never writes the query vectors, nor a served result."""
    first = benchmark.run(gpu_settings, with_queries, gpu_index=FLAT, gpu_search_params=FLAT_SEARCH)
    saved = with_queries / "retrieval-benchmark-queries.npz"
    saved.unlink()
    benchmark.run(gpu_settings, with_queries, gpu_index=FLAT, gpu_search_params=FLAT_SEARCH, guard=True)
    assert not saved.exists()
    assert json.loads((with_queries / "retrieval-benchmark.json").read_text()) == first
    nvidia_api.calls.clear()

    again = benchmark.run(gpu_settings, with_queries, gpu_index=FLAT, gpu_search_params=FLAT_SEARCH)

    assert nvidia_api.calls  # measured again: the queries were embedded
    assert again["measuredAt"] > first["measuredAt"]
    assert saved.is_file()

    # Other queries are other vectors
    pack = json.loads((with_queries / "pack.json").read_text())
    pack["documents"]["benchmark_queries"] = QUERIES[:3]
    (with_queries / "pack.json").write_text(json.dumps(pack))
    nvidia_api.calls.clear()
    assert benchmark.run(gpu_settings, with_queries, gpu_index=FLAT, gpu_search_params=FLAT_SEARCH)["queryCount"] == 3
    assert nvidia_api.calls


def test_a_copy_under_another_index_is_rebuilt_and_measured_again(gpu_settings, with_queries, manifest, nvidia_api):
    """After a change of the GPU index, an unchanged build's copy and its result are stale."""
    benchmark.run(gpu_settings, with_queries, gpu_index=FLAT, gpu_search_params=FLAT_SEARCH)
    nvidia_api.calls.clear()
    other = {"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 4}}

    result = benchmark.run(gpu_settings, with_queries, gpu_index=other, gpu_search_params=FLAT_SEARCH)

    assert nvidia_api.calls == []  # measured again, from the build's query vectors
    assert {profile["gpu"]["indexType"] for profile in result["profiles"]} == {"IVF_FLAT"}
    gpu = MilvusClient(uri=gpu_settings.milvus_gpu_uri)
    [index] = gpu.list_indexes(manifest.physical_collection, field_name=store.VECTOR_FIELD)
    assert gpu.describe_index(manifest.physical_collection, index_name=index)["index_type"] == "IVF_FLAT"
    gpu.close()


def test_nothing_is_compared_without_a_gpu_milvus_or_queries(settings, gpu_settings, data_dir, manifest):
    assert benchmark.run(settings, data_dir) is None  # a CPU-only stack
    assert benchmark.run(gpu_settings, data_dir) is None  # a pack without benchmark queries
    assert not (data_dir / "retrieval-benchmark.json").exists()


def test_exact_neighbors_are_scored_within_the_scope_and_ties_are_one_neighbor():
    vectors = benchmark.Vectors(
        ids=np.asarray(["a", "b", "c", "d"]),
        sources=np.asarray(["news", "news", "rules", "news"]),
        matrix=benchmark._normalized(np.asarray([[1, 0], [1, 1], [1, 0], [0, 1]], dtype=np.float32)),
    )
    query = np.asarray([1.0, 0.0], dtype=np.float32)

    assert vectors.truth(query, ["news"], k=2).best == pytest.approx([1.0, 0.707107])
    # "a" and "c" hold the same vector: either one is a correct nearest neighbor
    truth = vectors.truth(query, ["news", "rules"], k=2)
    assert benchmark._recall([truth.score("c"), truth.score("a")], truth.best) == 1.0
    assert benchmark._recall([truth.score("a"), truth.score("b")], truth.best) == 0.5


def test_a_true_neighbor_scores_the_same_number_as_a_result_and_as_the_truth():
    """2,048-dimension vectors as the embed model writes them. Scored apart (a matrix product for the truth, a dot
    product per result) and rounded to six places, about 0.5% of the chunks scored differently, so an index that
    returned exactly the true neighbors still lost recall, by a different amount for each new query embedding."""
    rng = np.random.default_rng(11)
    rows = 4000
    vectors = benchmark.Vectors(
        ids=np.asarray([f"chunk-{i}" for i in range(rows)]),
        sources=np.asarray(["news", "rules"])[rng.integers(0, 2, rows)],
        matrix=benchmark._normalized(rng.normal(size=(rows, 2048)).astype(np.float32)),
    )
    queries = benchmark._normalized(rng.normal(size=(60, 2048)).astype(np.float32))

    for query, scope in zip(queries, [("news",), ("rules",), ("news", "rules")] * 20, strict=True):
        truth = vectors.truth(query, scope)
        in_scope = np.flatnonzero(np.isin(vectors.sources, scope))
        nearest = in_scope[np.argsort(-(vectors.matrix[in_scope] @ query))[: benchmark.TOP_K]]
        assert benchmark._recall([truth.score(str(vectors.ids[row])) for row in nearest], truth.best) == 1.0


def test_scores_within_the_tolerance_pair_up_once():
    near = 0.5 + benchmark.TIE_TOLERANCE / 2
    assert benchmark._matched([0.5, near], [near, 0.5]) == 2
    assert benchmark._matched([0.5, 0.5, 0.5], [0.5]) == 1  # one result per true neighbor
    assert benchmark._matched([0.9, 0.5], [0.9, 0.5 + 2 * benchmark.TIE_TOLERANCE]) == 1
    assert benchmark._recall([0.9, 0.7, 0.7], [0.9, 0.8, 0.7]) == pytest.approx(2 / 3)
    assert benchmark._jaccard([0.9, 0.8], [0.9, 0.7]) == pytest.approx(1 / 3)
    assert benchmark._jaccard([], []) == 1.0


def test_batches_group_one_scope_and_concurrency_runs_waves():
    queries = [BenchmarkQuery(f"q{i}", ("news",) if i % 3 else ("rules",)) for i in range(7)]

    batches = benchmark.waves(queries, benchmark.Profile("b", "batch", batch_size=2))
    concurrent = benchmark.waves(queries, benchmark.Profile("c", "concurrency", concurrency=5))

    assert [[r.queries for r in wave] for wave in batches] == [[(0, 3)], [(6,)], [(1, 2)], [(4, 5)]]
    assert all(len({queries[q].source_ids for q in r.queries}) == 1 for wave in batches for r in wave)
    assert [len(wave) for wave in concurrent] == [5, 2]


def measured(*, ms: float, ids: list[list[float]], error: str | None = None) -> benchmark.Measurement:
    """One repetition, its neighbors as the exact scores of the chunks found."""
    found = dict(enumerate(ids))
    return benchmark.Measurement(total_ms=ms, latencies_ms=[ms], requests=1, vectors=1, results=[found], error=error)


def test_repetitions_that_return_another_copy_of_a_tied_neighbor_are_stable():
    exact = [[0.9, 0.5]]
    tied = benchmark.Measurement(
        total_ms=10, latencies_ms=[10], requests=1, vectors=1, results=[{0: [0.9, 0.5]}, {0: [0.9, 0.5 + 1e-8]}]
    )
    moved = benchmark.Measurement(
        total_ms=10, latencies_ms=[10], requests=1, vectors=1, results=[{0: [0.9, 0.5]}, {0: [0.9, 0.4]}]
    )

    assert benchmark.quality({"cpu": tied, "gpu": tied}, exact)["passed"]
    assert (
        "cpu_results_not_stable_across_repetitions"
        in benchmark.quality({"cpu": moved, "gpu": tied}, exact)["failureReasons"]
    )


def test_quality_gates_need_recall_and_agreement():
    exact = [[0.9, 0.8], [0.7, 0.6]]
    good = benchmark.quality({"cpu": measured(ms=10, ids=exact), "gpu": measured(ms=5, ids=exact)}, exact)
    poor = benchmark.quality(
        {"cpu": measured(ms=10, ids=exact), "gpu": measured(ms=5, ids=[[0.9, 0.1], [0.7, 0.6]])}, exact
    )
    failed = benchmark.quality({"cpu": measured(ms=10, ids=exact), "gpu": measured(ms=0, ids=[], error="boom")}, exact)

    assert good["passed"] and good["cpuGpuOverlapAtK"] == 1.0
    assert not poor["passed"]
    assert poor["gpuRecallAtK"] == 0.75
    assert "gpu_recall_below_0.95" in poor["failureReasons"]
    assert "worst_query_recall_below_0.8" in poor["failureReasons"]
    assert "cpu_gpu_overlap_below_0.95" in poor["failureReasons"]
    assert failed == {
        "passed": False,
        "cpuRecallAtK": None,
        "gpuRecallAtK": None,
        "cpuGpuOverlapAtK": None,
        "failureReasons": ["gpu_search_failed"],
    }


@pytest.mark.parametrize(
    ("passed", "cpu_ms", "gpu_ms", "expected"),
    [
        (True, 22.0, 10.0, {"decision": "gpu_speedup", "observedCpuOverGpuRatio": 2.2, "gpuSpeedupFactor": 2.2}),
        (
            True,
            10.5,
            10.0,
            {"decision": "cpu_faster_or_equal", "observedCpuOverGpuRatio": 1.05, "gpuSpeedupFactor": None},
        ),
        (False, 22.0, 10.0, {"decision": "no_claim", "observedCpuOverGpuRatio": 2.2, "gpuSpeedupFactor": None}),
    ],
)
def test_a_speedup_is_claimed_only_past_the_threshold_with_passing_quality(passed, cpu_ms, gpu_ms, expected):
    index = benchmark.Index("cpu", None, "x", "HNSW", store.SEARCH_PARAMS)
    cpu = benchmark.backend(index, measured(ms=cpu_ms, ids=[]))
    gpu = benchmark.backend(replace(index, role="gpu"), measured(ms=gpu_ms, ids=[]))

    assert benchmark.claim(passed, cpu, gpu) == expected


@pytest.mark.parametrize("outcome", ["raises", "nothing measured"])
def test_the_one_shot_never_fails_but_the_guard_does(monkeypatch, settings, tmp_path, outcome):
    """`up`'s one-shot exits 0 whatever happens; `benchmark --guard` (the GPU guard) exits 1 when nothing was
    measured, so the guard never reads an earlier measurement as a new one."""

    def run(*_args, **_kwargs):
        if outcome == "raises":
            raise TimeoutError("the index did not finish building")  # else nothing to compare: None

    monkeypatch.setattr(benchmark, "run", run)
    cli._benchmark(settings, tmp_path, guard=False)
    with pytest.raises(SystemExit) as exited:
        cli._benchmark(settings, tmp_path, guard=True)
    assert exited.value.code == 1


def test_the_guard_succeeds_when_it_measured(monkeypatch, settings, tmp_path):
    runs = []
    monkeypatch.setattr(benchmark, "run", lambda *_args, **kwargs: runs.append(kwargs) or {"profiles": []})
    cli._benchmark(settings, tmp_path, guard=True)
    assert runs == [{"guard": True}]


def test_the_command_line_runs_the_guard_with_guard(monkeypatch, tmp_path):
    runs = []
    monkeypatch.setattr(cli, "_benchmark", lambda _settings, data_dir, *, guard: runs.append((data_dir, guard)))
    monkeypatch.setenv("RETRIEVER_API_KEY", "nvapi-test-dummy")
    cli.main(["benchmark", "--data-dir", str(tmp_path)])
    cli.main(["benchmark", "--guard", "--data-dir", str(tmp_path)])
    assert runs == [(tmp_path, False), (tmp_path, True)]
