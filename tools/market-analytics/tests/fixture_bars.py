# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Made-up minute bars in the two layouts bars.py reads, for three symbols over three days.

Bars run from 04:00 to 19:59 New York time, one row group per symbol and day. `per_symbol` writes one file per
symbol (no symbol column); `month_partitions` writes the canonical layout (month=YYYY-MM/part-NNN, each part
holding whole symbols). The days cross a month boundary. With `utc=True` the same bars carry tz-aware UTC times,
as a TIMESTAMP WITH TIME ZONE column, instead of naive New York times.
"""

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

SYMBOLS = ["AAA", "BBB", "CCC"]
DAYS = ["2026-06-29", "2026-06-30", "2026-07-01"]
BARS_PER_DAY = 16 * 60
TIMEZONE = "America/New_York"
COLUMNS = {"time": "ts", "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"}


def minute_bars(symbols: list[str] = SYMBOLS, *, utc: bool = False) -> pd.DataFrame:
    """symbol, ts (naive New York time, or tz-aware UTC), float32 prices and float64 volume, sorted by symbol and
    time."""
    rng = np.random.default_rng(11)
    times = pd.DatetimeIndex(
        [t for day in DAYS for t in pd.date_range(f"{day} 04:00", periods=BARS_PER_DAY, freq="min")]
    )
    frames = []
    for symbol in symbols:
        close = 100 + np.cumsum(rng.normal(0, 0.05, len(times)))
        spread = rng.uniform(0.01, 0.1, len(times))
        frames.append(
            pd.DataFrame(
                {
                    "symbol": symbol,
                    "ts": times.astype("datetime64[us]"),
                    "open": (close + rng.normal(0, 0.02, len(times))).astype("float32"),
                    "high": (close + spread).astype("float32"),
                    "low": (close - spread).astype("float32"),
                    "close": close.astype("float32"),
                    "volume": rng.integers(100, 5_000, len(times)).astype("float64"),
                }
            )
        )
    bars = pd.concat(frames, ignore_index=True)
    if utc:
        bars["ts"] = bars["ts"].dt.tz_localize(TIMEZONE).dt.tz_convert("UTC")
    return bars


def per_symbol(root: Path, symbols: list[str] = SYMBOLS, *, utc: bool = False) -> dict[str, Any]:
    """One file per symbol; returns pack.json's `market.bars` for it."""
    (root / "stocks_1min").mkdir(parents=True)
    for symbol, bars in minute_bars(symbols, utc=utc).groupby("symbol"):
        path = root / "stocks_1min" / f"{symbol}_full_1min_adjsplit.parquet"
        bars.drop(columns="symbol").to_parquet(path, index=False, row_group_size=BARS_PER_DAY)
    return _spec(
        root, files="stocks_1min/*_full_1min_adjsplit.parquet", symbol_from_path=r"([^/]+)_full_1min_adjsplit\.parquet$"
    )


def month_partitions(root: Path, *, utc: bool = False) -> dict[str, Any]:
    """The canonical layout, partitioned by New York month; returns pack.json's `market.bars` for it."""
    bars = minute_bars(utc=utc)
    for month, rows in bars.groupby(minute_bars()["ts"].dt.strftime("%Y-%m")):
        directory = root / "bars" / f"month={month}"
        directory.mkdir(parents=True)
        for number, symbols in enumerate([SYMBOLS[:2], SYMBOLS[2:]]):
            part = rows[rows["symbol"].isin(symbols)]
            part.to_parquet(directory / f"part-{number:03d}.parquet", index=False, row_group_size=BARS_PER_DAY)
    return _spec(root, files="bars/month=*/part-*.parquet", symbol_column="symbol")


def _spec(root: Path, **layout: str) -> dict[str, Any]:
    return {
        "root": str(root),
        "columns": COLUMNS,
        "frequency": "1min",
        "timezone": TIMEZONE,
        "regular_session": ["09:30", "16:00"],
        **layout,
    }


def session_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """A reduction for the scan tests: one bar per symbol and session (open, high, low, close, volume, VWAP from
    closes, bar count).

    Open and close are each group's first and last bar, so the files must be ordered by time within a symbol, as
    both layouts are.
    """
    frame = frame.assign(session=frame["time"].dt.floor("D"), value=frame["close"] * frame["volume"])
    bars = frame.groupby(["symbol", "session"]).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        value=("value", "sum"),
        bar_count=("close", "count"),
    )
    bars["vwap"] = bars["value"] / bars["volume"]
    return bars.drop(columns="value").reset_index()
