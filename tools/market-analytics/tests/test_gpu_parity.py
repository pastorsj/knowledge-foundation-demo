# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The GPU worker (cudf.pandas, cuml.accel, nx-cugraph) gives the same answers as pandas on the CPU.

Needs an NVIDIA GPU and `uv sync --extra gpu-cu12`; skipped otherwise. Loading the pack falls back to pandas twice
(merge_asof and rename_axis, which cudf.pandas does not implement), which is expected, so
CUDF_PANDAS_FAIL_ON_FALLBACK=1 makes every test here error at startup. The tool calls themselves stay on the GPU,
which the last test checks.
"""

import importlib.util
import json
import math
import multiprocessing
import shutil
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import gpu_strict
import pandas as pd
import pytest
from fixture_bars import month_partitions
from fixture_bars import per_symbol
from fixture_pack import at

from market_analytics import tools
from market_analytics.data import MarketData
from market_analytics.data import Pack
from market_analytics.worker import Worker

pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(
        importlib.util.find_spec("cudf") is None or shutil.which("nvidia-smi") is None,
        reason="needs an NVIDIA GPU and the gpu-cu12 extra",
    ),
]

JUNE = {"start": at(21, 0), "end": at(41, 23)}
WIRE_A = {"source_names": ["Wire A"]}
CALLS = [
    ("market_scan", {"universe_id": "all_assets", "metrics": ["return", "volatility"], **JUNE}),
    ("market_scan", {"universe_id": "all_assets", "metrics": ["volume"], "comparison": "zscore", **JUNE}),
    ("market_scan", {"universe_id": "all_assets", "metrics": ["return"], "sessions": 5, "end": JUNE["end"]}),
    (
        "market_anomaly_scan",
        {
            "universe_id": "all_assets",
            "training_start": at(20, 0),
            "training_end": at(49, 23),
            "scoring_start": at(50, 0),
            "scoring_end": at(69, 23),
        },
    ),
    ("price_context", {"asset_ids": ["ALPH", "GAMA"], "frequency": "weekly", **JUNE}),
    ("sentiment_timeline", {"start": at(0, 0), "end": at(69, 23), "frequency": "weekly"}),
    ("analyze_news_price_relationship", {"published_from": at(0, 0), "published_to": at(69, 23)}),
    ("analyze_market_relationships", {"top_k": 4}),
    # The asset and source filters of the two news tools.
    ("sentiment_timeline", {"start": at(0, 0), "end": at(69, 23), "asset_ids": ["ALPH", "BETA"], **WIRE_A}),
    (
        "analyze_news_price_relationship",
        {"published_from": at(0, 0), "published_to": at(69, 23), "asset_ids": ["ALPH", "BETA"], **WIRE_A},
    ),
    (
        "analyze_news_price_relationship",
        {"published_from": at(0, 0), "published_to": at(69, 23), "universe_id": "reviewed_assets"},
    ),
    # The three sessions with minute bars (June 29 to July 1).
    ("intraday_scan", {"universe_id": "all_assets", "start": at(41, 0), "end": at(43, 23)}),
    ("intraday_scan", {"asset_ids": ["GAMA"], "rank_by": "max_drawdown", "direction": "lowest", **JUNE}),
]


@pytest.fixture(scope="module")
def gpu_worker(pack_root: Path) -> Iterator[Worker]:
    worker = Worker(pack_root, timeout=300)
    with pytest.MonkeyPatch.context() as env:
        env.setenv("MARKET_ANALYTICS_ENGINE", "gpu")  # the worker process inherits it at start
        worker.start()
    yield worker
    worker.close()


@pytest.mark.parametrize(("tool", "arguments"), CALLS)
def test_gpu_matches_cpu(data: MarketData, gpu_worker: Worker, tool: str, arguments: dict[str, Any]) -> None:
    cpu = tools.run(data, tool, arguments)
    gpu = gpu_worker.call(tool, arguments)

    assert gpu["engine"]["device"] == "gpu"
    assert gpu["engine"]["library"] in {"cudf.pandas", "cuml.accel", "nx-cugraph"}
    assert gpu["status"] == cpu["status"] == "succeeded"
    assert_close(gpu["payload"], cpu["payload"])


def test_the_declared_peer_graph_loads_and_matches_on_the_gpu(pack_root: Path, tmp_path: Path) -> None:
    """sparse_declared_peers, the mode of the large packs, which the fixture's full_correlation graph skips. Real
    packs declare most pairs both ways, so the two directions repeat rows that must be dropped on the GPU too."""
    root = tmp_path / "sparse"
    shutil.copytree(pack_root, root)
    declared = {"source_asset_id": ["asset-alpha", "asset-beta", "asset-gamma"]}
    declared["target_asset_id"] = ["asset-beta", "asset-gamma", "asset-beta"]
    pd.DataFrame(declared).to_parquet(root / "tables" / "asset_relationships.parquet", index=False)
    manifest = json.loads((root / "pack.json").read_text())
    manifest["analytics"]["relationship_graph"]["mode"] = "sparse_declared_peers"
    (root / "pack.json").write_text(json.dumps(manifest))
    arguments = {"top_k": 4}
    cpu = tools.run(MarketData.load(Pack.load(root)), "analyze_market_relationships", arguments)
    worker = Worker(root, timeout=300)
    with pytest.MonkeyPatch.context() as env:
        env.setenv("MARKET_ANALYTICS_ENGINE", "gpu")
        worker.start()
    try:
        gpu = worker.call("analyze_market_relationships", arguments)
    finally:
        worker.close()

    assert gpu["engine"]["device"] == "gpu"
    assert cpu["payload"]["edge_count"] == 4  # the two declared pairs, both directions
    assert_close(gpu["payload"], cpu["payload"])


def test_gpu_tool_calls_do_not_fall_back_to_pandas(pack_root: Path) -> None:
    """A cudf.pandas fallback copies the frames to pandas and back on every call: the GPU engine was slower than
    the CPU while the tools fell back. With CUDF_PANDAS_FAIL_ON_FALLBACK=1 once the pack is loaded, a fallback fails
    its call."""
    with multiprocessing.get_context("spawn").Pool(1) as pool:
        results = pool.apply(gpu_strict.run_without_fallbacks, (pack_root, CALLS))

    assert [(tool, result["status"]) for (tool, _), result in zip(CALLS, results, strict=True)] == [
        (tool, "succeeded") for tool, _ in CALLS
    ]


def test_minute_bar_scans_match_the_cpu_without_falling_back(tmp_path: Path) -> None:
    """Partition-scoped reads, multi-file reads, row-group filters and batch reductions all stay on the GPU, in
    both layouts."""
    specs = [per_symbol(tmp_path / "per_symbol"), month_partitions(tmp_path / "month_partitions")]
    window = (datetime(2026, 6, 29), datetime(2026, 7, 2))
    cpu = [
        gpu_strict.records(gpu_strict.scan_bars(spec, window, budget))
        for spec in specs
        for budget in gpu_strict.BUDGETS
    ]
    with multiprocessing.get_context("spawn").Pool(1) as pool:
        gpu = pool.apply(gpu_strict.scan_bars_without_fallbacks, (specs, window))

    for gpu_json, cpu_json in zip(gpu, cpu, strict=True):
        assert len(json.loads(cpu_json)) == 2 * 3  # two symbols, three sessions
        assert_close(json.loads(gpu_json), json.loads(cpu_json))


def assert_close(gpu: Any, cpu: Any, path: str = "payload") -> None:
    """Equal structure, ids, ranks and timestamps; floats within GPU rounding."""
    if isinstance(cpu, dict):
        assert gpu.keys() == cpu.keys(), path
        for key in cpu:
            assert_close(gpu[key], cpu[key], f"{path}.{key}")
    elif isinstance(cpu, list):
        assert len(gpu) == len(cpu), path
        for index, (gpu_item, cpu_item) in enumerate(zip(gpu, cpu, strict=True)):
            assert_close(gpu_item, cpu_item, f"{path}[{index}]")
    elif isinstance(cpu, float):
        assert math.isclose(gpu, cpu, rel_tol=1e-4, abs_tol=1e-6), f"{path}: {gpu} != {cpu}"
    else:
        assert gpu == cpu, path
