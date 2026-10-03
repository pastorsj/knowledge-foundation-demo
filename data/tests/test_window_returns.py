# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The sector oracles measure window returns as the packs' ontology text says Auto Ontology should (daily_prices).

Made-up bars with the cases the real data has: a stock without a bar on a boundary session, one listed inside the
window and one with no bar in it. Real stocks show all three, and a join on the boundary dates drops them.
"""

from __future__ import annotations

from datetime import date
from datetime import timedelta
from math import prod
from statistics import median

import duckdb
import pytest
import yaml
from conftest import PACKS

FINANCE = "Finance, Insurance and Real Estate"


def database(pack: str) -> duckdb.DuckDBPyConnection:
    db = duckdb.connect()
    db.execute((PACKS / pack / "schema.sql").read_text())
    return db


def add_stock(db: duckdb.DuckDBPyConnection, asset_id: str, sector: str, closes: dict[date, float]) -> None:
    """A stock and its bars; total_return_1d over its previous bar, as the market importer computes it."""
    days = sorted(closes)
    rank = db.execute("SELECT count(*) + 1 FROM main.assets").fetchone()[0]
    db.execute(
        "INSERT INTO main.assets VALUES (?, ?, NULL, '6022', ?, 'Banks', 'NYSE', NULL, ?, ?, ?, 1.0, ?, false)",
        [asset_id, f"{asset_id} Inc", sector, days[0], days[-1], len(days), rank],
    )
    previous = None
    for day in days:
        close = closes[day]
        daily = close / previous - 1 if previous else 0.0
        db.execute(
            "INSERT INTO main.daily_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?, 100, ?, 1, ?)",
            [f"{asset_id}:{day}", asset_id, day, close, close, close, close, close, close * 100, daily],
        )
        previous = close


def test_us_equities_returns_run_from_each_stocks_own_bars():
    db = database("us-equities")
    before, last_before, first, next_to_last, last = (
        date(2025, 12, 30),
        date(2025, 12, 31),  # the session before the window
        date(2026, 1, 2),
        date(2026, 3, 11),
        date(2026, 3, 12),  # the window's last session
    )
    add_stock(db, "FULL", FINANCE, {before: 9.0, last_before: 10.0, first: 11.0, next_to_last: 12.0, last: 12.5})
    add_stock(db, "GAP", FINANCE, {before: 20.0, first: 21.0, last: 24.0})  # no bar on the session before
    add_stock(db, "EARLY", FINANCE, {last_before: 50.0, first: 50.0, next_to_last: 55.0})  # none on the last
    add_stock(db, "NEW", FINANCE, {next_to_last: 8.0, last: 10.0})  # listed inside the window
    add_stock(db, "GONE", FINANCE, {before: 30.0})  # no bar in the window: counted, but has no return
    add_stock(db, "MAKER", "Manufacturing", {last_before: 100.0, last: 90.0})

    sectors = db.execute((PACKS / "us-equities" / "eval" / "oracles" / "sector_breakdown.sql").read_text()).fetchall()
    # The last close in the window over the last close before it; a new listing from its first close.
    returns = {"FULL": 12.5 / 10 - 1, "GAP": 24 / 20 - 1, "EARLY": 55 / 50 - 1, "NEW": 10 / 8 - 1}
    compounded = dict(
        db.execute(
            "SELECT asset_id, exp(sum(ln(1 + total_return_1d))) - 1 FROM main.daily_prices"
            " WHERE trading_date BETWEEN DATE '2026-01-02' AND DATE '2026-03-12' GROUP BY asset_id"
        ).fetchall()
    )

    assert sectors == [
        (FINANCE, 5, pytest.approx(median(returns.values()))),  # GONE counts, without a return
        ("Manufacturing", 1, pytest.approx(-0.1)),
    ]
    assert compounded == pytest.approx(returns | {"MAKER": -0.1})  # the window's daily returns compounded


def test_synthetic_market_returns_over_n_sessions_compound_n_daily_returns():
    db = database("synthetic-market")
    sessions = [date(2026, 8, 31) - timedelta(days=day) for day in range(40)]
    sessions = sorted(day for day in sessions if day.weekday() < 5)[-22:]
    add_stock(db, "UP", FINANCE, {day: 100 * 1.01**i for i, day in enumerate(sessions)})
    add_stock(db, "DOWN", "Manufacturing", {day: 100 * 0.99**i for i, day in enumerate(sessions)})

    sectors = db.execute((PACKS / "synthetic-market" / "eval" / "oracles" / "sector_breakdown.sql").read_text())

    # From the close on the session before the 20, so 20 daily returns, not 19.
    assert sectors.fetchall() == [
        (FINANCE, 1, pytest.approx(prod([1.01] * 20) - 1)),
        ("Manufacturing", 1, pytest.approx(prod([0.99] * 20) - 1)),
    ]


@pytest.mark.parametrize(("pack", "member"), [("us-equities", "stock"), ("synthetic-market", "issuer")])
def test_the_ontology_states_the_convention(pack, member):
    """Auto Ontology reads only this text: the base, the per-member return and the group count must stay in it."""
    description = yaml.safe_load((PACKS / pack / "ontology.yaml").read_text())["tables"]["daily_prices"]["description"]

    assert "the session before the window's first session" in description
    assert "not an average or median of total_return_1d" in description
    assert f"every {member} of the group in assets" in description
