# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The host helpers that finish market_scan and market_anomaly_scan compute what the pandas calls they replace
computed, to the last bit, so a CPU result does not change (tools/common.py)."""

import numpy as np
import pandas as pd
import pytest

from market_analytics.tools.anomaly import _highest
from market_analytics.tools.common import mean
from market_analytics.tools.common import population_std
from market_analytics.tools.common import rank

RNG = np.random.default_rng(20261002)


def _floats(size: int) -> np.ndarray:
    return RNG.standard_normal(size) * 10.0 ** RNG.integers(-3, 4, size)


def _with(values: np.ndarray, special: float, count: int) -> np.ndarray:
    values = values.copy()
    values[RNG.choice(len(values), count, replace=False)] = special
    return values


ARRAYS = {
    "one": _floats(1),
    "two": _floats(2),
    "fifty": _floats(50),
    "a market": _floats(1601),
    "ties": np.round(_floats(400), 1),
    "missing": _with(_floats(300), np.nan, 17),
    "all missing": np.full(5, np.nan),
    "infinite": _with(_floats(60), np.inf, 1),
    "both infinities": _with(_with(_floats(60), np.inf, 1), -np.inf, 1),
    "equal": np.full(12, 0.25),
    "volumes": RNG.integers(0, 4_000_000_000, 1601),
    "one volume": np.array([123_456_789]),
}


def _identical(ours: float, theirs: float) -> bool:
    return bool(np.isnan(ours) and np.isnan(theirs)) or np.float64(ours).tobytes() == np.float64(theirs).tobytes()


@pytest.mark.parametrize("values", ARRAYS.values(), ids=ARRAYS.keys())
def test_mean_and_population_std_are_pandas_to_the_last_bit(values: np.ndarray) -> None:
    series = pd.Series(values)
    with np.errstate(invalid="ignore"):
        assert _identical(mean(values), series.mean())
        assert _identical(population_std(values), series.std(ddof=0))


@pytest.mark.parametrize("ascending", [True, False])
@pytest.mark.parametrize("values", ARRAYS.values(), ids=ARRAYS.keys())
def test_rank_orders_as_sort_values_by_score_then_id(values: np.ndarray, ascending: bool) -> None:
    ids = np.array([f"A{i:05d}" for i in RNG.permutation(len(values))], dtype=object)
    frame = pd.DataFrame({"score": values, "asset_id": ids})
    expected = frame.sort_values(["score", "asset_id"], ascending=[ascending, True]).index.to_numpy()
    assert rank(values, ids, ascending=ascending).tolist() == expected.tolist()


@pytest.mark.parametrize("limit", [1, 3, 10, 25, 299, 300])
def test_the_highest_scores_come_in_the_order_of_a_full_sort(limit: int) -> None:
    """Ties at the cutoff included: the asset id, then the time, break them as the full sort did."""
    rows = 300
    scoring = pd.DataFrame(
        {
            "asset_id": RNG.choice(["AAA", "BBB", "CCC", "DDD"], rows).astype(object),
            "timestamp": pd.Timestamp("2026-01-02 21:00") + pd.to_timedelta(RNG.permutation(rows), unit="D"),
        },
        index=pd.RangeIndex(1000, 1000 + rows),  # positions, not labels, pick the rows
    )
    scores = np.round(RNG.random(rows), 1)  # about 30 rows share each score
    expected = (
        scoring.assign(score=scores)
        .reset_index(drop=True)
        .sort_values(["score", "asset_id", "timestamp"], ascending=[False, True, True])
        .head(limit)
    )

    top, asset_ids, timestamps = _highest(scoring, scores, limit)

    assert top.tolist() == expected.index.tolist()
    assert asset_ids.tolist() == expected["asset_id"].tolist()
    assert timestamps.tolist() == expected["timestamp"].to_numpy().tolist()
