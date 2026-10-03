# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Partition-scoped, batched minute-bar scans in both layouts."""

from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest
from fixture_bars import minute_bars
from fixture_bars import month_partitions
from fixture_bars import per_symbol
from fixture_bars import session_bars

from market_analytics.bars import CPU_BATCH_BYTES
from market_analytics.bars import GPU_BATCH_BYTES
from market_analytics.bars import MinuteBars
from market_analytics.bars import batch_bytes

# June 30, 2026 in UTC, the worker's timestamp model: 04:00 June 30 to 03:59 July 1 in New York (EDT, UTC-4).
JUNE_30 = (datetime(2026, 6, 30, 4, 0), datetime(2026, 7, 1, 3, 59))
EVERYTHING = (datetime(2026, 6, 1), datetime(2026, 7, 31))


@pytest.fixture(scope="module", params=["per_symbol", "month_partitions"])
def bars(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> MinuteBars:
    layout = {"per_symbol": per_symbol, "month_partitions": month_partitions}[request.param]
    return MinuteBars.from_pack({"market": {"bars": layout(tmp_path_factory.mktemp(request.param))}})


def expected_sessions(symbols: list[str], day: str) -> pd.DataFrame:
    """The regular session's bars (09:30 to 16:00, both included) reduced by hand."""
    frame = minute_bars()
    frame = frame[
        frame["symbol"].isin(symbols)
        & (frame["ts"].dt.strftime("%Y-%m-%d") == day)
        & frame["ts"].dt.strftime("%H:%M").between("09:30", "16:00")
    ]
    return frame.groupby("symbol", as_index=False).agg(
        open=("open", "first"), close=("close", "last"), volume=("volume", "sum"), bar_count=("close", "count")
    )


def test_a_scan_reduces_the_regular_session_of_the_named_symbols(bars: MinuteBars) -> None:
    scan = bars.scan(["CCC", "AAA"], *JUNE_30, session_bars)

    assert scan.rows == 2 * 391
    result = scan.result
    assert result["session"].tolist() == [pd.Timestamp("2026-06-30")] * 2
    expected = expected_sessions(["AAA", "CCC"], "2026-06-30")
    pd.testing.assert_frame_equal(result[expected.columns.tolist()], expected)
    assert (result["high"] >= result[["open", "close"]].max(axis=1)).all()
    assert (result["vwap"].between(result["low"], result["high"])).all()


def test_one_symbol_files_are_picked_by_name_and_read_whole(tmp_path: Path) -> None:
    bars = MinuteBars.from_pack({"market": {"bars": per_symbol(tmp_path)}})

    parts = bars.plan(["CCC", "AAA", "ZZZ"], *(bars.local(moment) for moment in JUNE_30))

    assert [(part.path.name, part.symbol, part.rows) for part in parts] == [
        ("CCC_full_1min_adjsplit.parquet", "CCC", 3 * 960),
        ("AAA_full_1min_adjsplit.parquet", "AAA", 3 * 960),
    ]
    # With no symbol column, a file that is needed at all is read whole; one with no bar in the window is skipped.
    metadata = pq.read_metadata(parts[0].path)
    assert parts[0].bytes == sum(metadata.row_group(i).total_byte_size for i in range(metadata.num_row_groups))
    assert bars.plan(["AAA"], datetime(2026, 7, 2), datetime(2026, 7, 3)) == []


def test_month_partitions_are_pruned_by_window_and_row_groups_by_statistics(tmp_path: Path) -> None:
    bars = MinuteBars.from_pack({"market": {"bars": month_partitions(tmp_path)}})

    (july,) = bars.plan(["CCC"], datetime(2026, 7, 1), datetime(2026, 7, 1, 23, 59))
    (june,) = bars.plan(["AAA"], datetime(2026, 6, 30), datetime(2026, 6, 30, 23, 59))

    assert july.path.relative_to(tmp_path).as_posix() == "bars/month=2026-07/part-001.parquet"
    # June's first part has one row group per symbol and day (AAA, AAA, BBB, BBB): only AAA on June 30 is read.
    assert june.path.relative_to(tmp_path).as_posix() == "bars/month=2026-06/part-000.parquet"
    assert june.bytes == pq.read_metadata(june.path).row_group(1).total_byte_size
    assert len(bars.plan(["AAA", "CCC"], *EVERYTHING)) == 4  # both months, both parts


def test_the_batch_budget_bounds_each_read_but_not_the_result(bars: MinuteBars) -> None:
    whole = bars.scan(["AAA", "BBB", "CCC"], *EVERYTHING, session_bars)
    batched = bars.scan(["AAA", "BBB", "CCC"], *EVERYTHING, session_bars, budget=1)

    assert whole.batches == 1
    assert batched.batches == batched.files > 1  # a file larger than the budget is a batch of its own
    pd.testing.assert_frame_equal(
        batched.result.sort_values(["symbol", "session"], ignore_index=True), whole.result, check_exact=True
    )
    assert len(whole.result) == 3 * 3  # every symbol and day


@pytest.mark.parametrize(("engine", "expected"), [("cpu", CPU_BATCH_BYTES), ("gpu", GPU_BATCH_BYTES)])
def test_the_batch_budget_defaults_by_engine_and_follows_the_setting(
    monkeypatch: pytest.MonkeyPatch, engine: str, expected: int
) -> None:
    monkeypatch.setenv("MARKET_ANALYTICS_ENGINE", engine)
    monkeypatch.setenv("MARKET_ANALYTICS_BATCH_BYTES", "")  # Compose passes an unset variable as empty
    assert batch_bytes() == expected
    monkeypatch.setenv("MARKET_ANALYTICS_BATCH_BYTES", "1000")
    assert batch_bytes() == 1000


def test_the_window_is_converted_to_the_datasets_wall_clock(bars: MinuteBars) -> None:
    """13:30 UTC on June 30 is 09:30 in New York."""
    scan = bars.scan(["BBB"], datetime(2026, 6, 30, 13, 30), datetime(2026, 6, 30, 13, 34), lambda frame: frame)

    assert scan.result["time"].dt.strftime("%H:%M").tolist() == ["09:30", "09:31", "09:32", "09:33", "09:34"]
    assert set(scan.result["symbol"]) == {"BBB"}


@pytest.mark.parametrize("layout", [per_symbol, month_partitions])
def test_tz_aware_times_scan_like_wall_clock_times(tmp_path: Path, layout) -> None:
    """A TIMESTAMP WITH TIME ZONE time column (UTC here) reads as the same New York wall-clock bars."""
    aware = MinuteBars.from_pack({"market": {"bars": layout(tmp_path / "aware", utc=True)}})
    naive = MinuteBars.from_pack({"market": {"bars": layout(tmp_path / "naive")}})

    for window in (JUNE_30, EVERYTHING, (datetime(2026, 6, 30, 13, 30), datetime(2026, 6, 30, 13, 34))):
        expected = naive.scan(["CCC", "AAA"], *window, session_bars)
        scan = aware.scan(["CCC", "AAA"], *window, session_bars)
        assert (scan.files, scan.rows) == (expected.files, expected.rows)
        pd.testing.assert_frame_equal(scan.result, expected.result, check_exact=True)
    # Row groups are still pruned by their statistics, in wall-clock time: bars run 04:00 to 19:59, so nothing
    # lies between 20:00 and 03:59 (a statistic read in the wrong zone would overlap it).
    for bars in (aware, naive):
        assert bars.plan(["AAA"], datetime(2026, 6, 30, 20, 0), datetime(2026, 7, 1, 3, 59)) == []
        assert len(bars.plan(["AAA"], datetime(2026, 6, 30, 19, 59), datetime(2026, 7, 1, 3, 59))) == 1


def test_a_scan_that_matches_nothing_is_empty(bars: MinuteBars) -> None:
    scan = bars.scan(["AAA"], datetime(2026, 6, 30, 1, 0), datetime(2026, 6, 30, 2, 0), session_bars)  # overnight

    assert (scan.rows, len(scan.result)) == (0, 0)
    assert bars.scan(["ZZZ"], *EVERYTHING, session_bars).files == 0


def test_a_dataset_it_cannot_list_fails_instead_of_scanning_nothing(tmp_path: Path) -> None:
    bars = MinuteBars.from_pack({"market": {"bars": per_symbol(tmp_path / "dataset")}})
    hidden = tmp_path / "hidden"
    (tmp_path / "dataset").rename(hidden)  # as if missing, or unreadable to the service's user

    with pytest.raises(FileNotFoundError, match="missing or unreadable"):
        bars.plan(["AAA"], *EVERYTHING)
    hidden.rename(tmp_path / "dataset")
    assert [part.symbol for part in bars.plan(["AAA"], *EVERYTHING)] == ["AAA"]  # found once it is back


def test_packs_without_minute_bars_have_none(tmp_path: Path) -> None:
    daily = {**per_symbol(tmp_path), "frequency": "1d"}

    assert MinuteBars.from_pack({}) is None
    assert MinuteBars.from_pack({"market": {"bars": daily}}) is None
