# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Write the tiny external dataset the tests use: made-up minute bars and headlines, one file per symbol. No real data.

    uv run python tests/fixtures/make_minute_bars_fixture.py

external/minute-bars/
  benchmark-bundle-manifest.json                        the manifest: files, bytes, sha256
  market/stocks_1min/<SYMBOL>_full_1min_adjsplit.parquet
  gdelt/headlines.parquet                               three made-up headlines in GDELT's columns
sec/
  company_tickers_exchange.json, sic.json               a stub SEC snapshot for the made-up issuers

Every symbol trades 2025-12-31, 2026-01-02 and 2026-01-05, 8 bars a day, so each daily bar can be checked by hand:
open is the 09:30 open, high and low come from the 12:00 bar, close is the 16:00 closing auction, and the 04:00,
09:29, 16:01 and 19:59 bars (outside the session) carry absurd prices that must never reach a daily bar. On the
holiday, 2026-01-01, only XCCC has stray bars. XDDD trades on one day only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
DATES = [date(2025, 12, 31), date(2026, 1, 2), date(2026, 1, 5)]
HOLIDAY = date(2026, 1, 1)
# symbol: (price level, SEC CIK or None when SEC does not list it)
SYMBOLS = {
    "XAAA": (10, 1),  # the base stock
    "XAAAW": (1, 1),  # its warrant: same issuer, W suffix
    "XAAAU": (11, None),  # its unit: not listed, U suffix
    "XAAA.P": (25, None),  # its preferred: a dot, not listed
    "XAAAL": (24, 1),  # its note: same issuer, not its primary ticker
    "XBBB": (40, 2),  # a peer of XAAA (same SIC code)
    "XBBB.B": (41, 2),  # its class B shares: SEC lists XBBB-B
    "XCCC": (50, None),  # a fund SEC does not list
    "XDDD": (60, 3),  # one session only
    "XE": (70, 4),  # E and EU are different issuers, so XEU is no unit of XE
    "XEU": (80, 5),
}
SIC = {
    1: ("3674", "SEMICONDUCTORS & RELATED DEVICES"),
    2: ("3674", "SEMICONDUCTORS & RELATED DEVICES"),
    3: ("7372", "SERVICES-PREPACKAGED SOFTWARE"),
    4: ("6022", "STATE COMMERCIAL BANKS"),
    5: ("", ""),
}
# (time, open, high, low, close, volume) relative to the day's price level p; None marks the absurd bars
BARS = [
    ("04:00", None, 100.0),
    ("09:29", None, 100.0),
    ("09:30", (0.0, 0.3, -0.2, 0.1), 100.0),
    ("12:00", (0.1, 1.0, -1.0, 0.2), 100.0),
    ("15:59", (0.2, 0.4, 0.0, 0.3), 100.0),
    ("16:00", (0.3, 0.5, 0.3, 0.5), 5000.0),
    ("16:01", None, 100.0),
    ("19:59", None, 100.0),
]


def bars(level: float, days: list[date]) -> pa.Table:
    rows = []
    for index, day in enumerate(days):
        p = level + index
        for clock, offsets, volume in BARS:
            ts = datetime.combine(day, datetime.strptime(clock, "%H:%M").time())
            o, h, low, c = (p + offset for offset in offsets) if offsets else (999.0, 999.0, 0.01, 0.01)
            rows.append(
                {
                    "timestamp": (ts - datetime(1970, 1, 1)) // timedelta(microseconds=1),
                    "ts": ts,
                    "open": o,
                    "high": h,
                    "low": low,
                    "close": c,
                    "volume": volume,
                    "symbol_id": 0,
                    "asset_class_id": 0,
                }
            )
    schema = pa.schema(
        [
            ("timestamp", pa.int64()),
            ("ts", pa.timestamp("us")),
            ("open", pa.float32()),
            ("high", pa.float32()),
            ("low", pa.float32()),
            ("close", pa.float32()),
            ("volume", pa.float64()),
            ("symbol_id", pa.int32()),
            ("asset_class_id", pa.int8()),
        ]
    )
    return pa.Table.from_pylist(rows, schema=schema)


def headlines() -> pa.Table:
    """GDELT's article columns: an https link, a plain http link, and a blank headline that must be skipped."""
    return pa.table(
        {
            "id": ["20260102140000-1", "20260102140000-2", "20260105090000-3"],
            "date": pa.array([20260102140000, 20260102140000, 20260105090000], pa.int64()),
            "source": ["example.com", "example.org", "example.net"],
            "string": ["Central bank holds rates steady", "Port strike enters second week", "  "],
            "embedding": pa.array([[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]], pa.list_(pa.float32())),
            "url": ["https://example.com/rates", "http://example.org/strike", "https://example.net/blank"],
            "tone": [-1.5, -3.25, 0.0],
            "cluster_id": pa.array([1, 2, 3], pa.int32()),
            "cluster_label": ["1_bank_rates", "2_port_strike", "3_blank"],
        }
    )


def main() -> None:
    root = HERE / "external" / "minute-bars"
    stocks = root / "market" / "stocks_1min"
    stocks.mkdir(parents=True, exist_ok=True)
    for symbol, (level, _) in SYMBOLS.items():
        days = {"XDDD": DATES[:1], "XCCC": sorted([*DATES, HOLIDAY])}.get(symbol, DATES)
        pq.write_table(bars(level, days), stocks / f"{symbol}_full_1min_adjsplit.parquet", compression="zstd")
    (root / "gdelt").mkdir(exist_ok=True)
    pq.write_table(headlines(), root / "gdelt" / "headlines.parquet", compression="zstd")
    files = [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(root.glob("*/**/*.parquet"))
    ]
    fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    manifest = {
        "format": "portable-benchmark-bundle",
        "version": 1,
        "dataset_fingerprint": fingerprint,
        "file_count": len(files),
        "total_bytes": sum(f["bytes"] for f in files),
        "files": files,
    }
    (root / "benchmark-bundle-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    sec = HERE / "sec"
    sec.mkdir(exist_ok=True)
    listed = [(cik, symbol.replace(".", "-")) for symbol, (_, cik) in SYMBOLS.items() if cik]
    names = {
        1: "Xaaa Devices Inc",
        2: "Xbbb Semiconductor Corp",
        3: "Xddd Software Inc",
        4: "Xe Bancorp",
        5: "Xeu Holdings Inc",
    }
    data = [[cik, names[cik], ticker, "Nasdaq"] for cik, ticker in listed]
    (sec / "company_tickers_exchange.json").write_text(
        json.dumps({"fields": ["cik", "name", "ticker", "exchange"], "data": data}, indent=1) + "\n"
    )
    (sec / "sic.json").write_text(json.dumps({str(cik): list(code) for cik, code in SIC.items()}, indent=1) + "\n")
    print(f"fingerprint {fingerprint}, {manifest['total_bytes']:,} bytes")


if __name__ == "__main__":
    main()
