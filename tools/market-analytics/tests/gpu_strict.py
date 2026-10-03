# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Runs in a spawned process for test_gpu_parity.py. It imports nothing at module level, so the process can
install the GPU accelerators before pandas is imported (see bootstrap.py)."""

import os
from pathlib import Path
from typing import Any

# Minute-bar batch budgets: the default, which reads all of a fixture's files in one call, and one file per call.
BUDGETS = (None, 1)


def run_without_fallbacks(root: Path, calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Load the pack on the GPU, then run each call with CUDF_PANDAS_FAIL_ON_FALLBACK=1.

    Loading the pack falls back to pandas twice (merge_asof and rename_axis), which is allowed. cudf.pandas reads
    the variable at each fallback, so from then on a fallback raises and the call fails.
    """
    from market_analytics import bootstrap

    os.environ["MARKET_ANALYTICS_ENGINE"] = "gpu"
    bootstrap.install_gpu_accelerators()

    from market_analytics import tools
    from market_analytics.data import MarketData
    from market_analytics.data import Pack

    data = MarketData.load(Pack.load(root))
    os.environ["CUDF_PANDAS_FAIL_ON_FALLBACK"] = "1"
    return [tools.run(data, tool, arguments) for tool, arguments in calls]


def scan_bars_without_fallbacks(specs: list[dict[str, Any]], window: tuple[Any, Any]) -> list[str]:
    """Scan each minute-bar layout on the GPU with CUDF_PANDAS_FAIL_ON_FALLBACK=1, at each of BUDGETS; the results
    as sorted JSON records."""
    from market_analytics import bootstrap

    os.environ["MARKET_ANALYTICS_ENGINE"] = "gpu"
    bootstrap.install_gpu_accelerators()

    os.environ["CUDF_PANDAS_FAIL_ON_FALLBACK"] = "1"
    results = [scan_bars(spec, window, budget) for spec in specs for budget in BUDGETS]
    del os.environ["CUDF_PANDAS_FAIL_ON_FALLBACK"]  # serializing the results may fall back
    return [records(result) for result in results]


def scan_bars(spec: dict[str, Any], window: tuple[Any, Any], budget: int | None) -> Any:
    from fixture_bars import session_bars

    from market_analytics.bars import MinuteBars

    bars = MinuteBars.from_pack({"market": {"bars": spec}})
    return bars.scan(["AAA", "CCC"], *window, session_bars, budget=budget).result


def records(result: Any) -> str:
    """Sorted, since batches come back in file order."""
    return result.sort_values(["symbol", "session"], ignore_index=True).to_json(orient="records", date_format="iso")
