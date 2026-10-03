# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""A tiny pack that satisfies market-analytics/v1, laid out like /data/active.

Four assets over 70 sessions. Alpha and beta share a common return factor, so they are strongly correlated;
gamma has one planted anomalous session (a crash on heavy volume) at GAMMA_SPIKE; omega is not reviewed. Alpha,
beta and gamma also have minute bars on three sessions (fixture_bars.py). `daily_only=True` writes the pack with
neither news nor minute bars, like a real-data pack without ticker-linked news.
"""

import json
from datetime import UTC
from datetime import datetime
from datetime import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from fixture_bars import per_symbol

SESSIONS = pd.bdate_range("2026-05-01", periods=70)
GAMMA_SPIKE = 60  # session index of gamma's planted anomaly
ASSETS = pd.DataFrame(
    {
        "asset_id": ["asset-alpha", "asset-beta", "asset-gamma", "asset-omega"],
        "company_name": ["Alpha Robotics", "Beta Beverages", "Gamma Grid Systems", "Omega Holdings"],
        "is_reviewed": [True, True, True, False],
    }
)
TICKERS = {"asset-alpha": "ALPH", "asset-beta": "BETA", "asset-gamma": "GAMA", "asset-omega": "OMGA"}
PREDICTION_TABLES = {
    "asset_entities": {"primary_key": "asset_id"},
    "price_events": {"primary_key": "price_id", "time_column": "observed_at", "links_to_entity": "asset_id"},
    "return_outcomes": {"primary_key": "outcome_id", "time_column": "realized_at", "links_to_entity": "asset_id"},
}
TEMPLATES = [
    {
        "id": "positive_return",
        "name": "Positive return",
        "description": "Likelihood of a positive five-session return after the anchor.",
        "pql": "PREDICT COUNT(return_outcomes.* WHERE return_outcomes.positive_return = true, 0, 5, days) > 0 "
        "FOR EACH asset_entities.asset_id",
    },
    {
        "id": "any_price",
        "name": "Any price event",
        "description": "Likelihood of any price event in the week after the anchor.",
        "pql": "PREDICT COUNT(price_events.*, 0, 7, days) > 0 FOR EACH asset_entities.asset_id",
    },
]


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """A UTC timestamp on session `day` (an index into SESSIONS)."""
    return datetime.combine(SESSIONS[day].date(), time(hour, minute), tzinfo=UTC)


def daily_prices() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    common = rng.normal(0, 0.01, len(SESSIONS))
    returns = {
        "asset-alpha": 0.004 + common + rng.normal(0, 0.002, len(SESSIONS)),
        "asset-beta": 0.001 + common + rng.normal(0, 0.002, len(SESSIONS)),
        "asset-gamma": -0.002 + rng.normal(0, 0.01, len(SESSIONS)),
        "asset-omega": rng.normal(0, 0.01, len(SESSIONS)),
    }
    volumes = {asset: rng.integers(900_000, 1_100_000, len(SESSIONS)) for asset in returns}
    returns["asset-gamma"][GAMMA_SPIKE] = -0.25
    volumes["asset-gamma"][GAMMA_SPIKE] *= 20
    frames = []
    for asset, asset_returns in returns.items():
        asset_returns[0] = 0.0
        frames.append(
            pd.DataFrame(
                {
                    "asset_id": asset,
                    "trading_date": SESSIONS.date,
                    "adjusted_close": 100 * np.cumprod(1 + asset_returns),
                    "volume": volumes[asset],
                    "total_return_1d": asset_returns,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def market_news() -> pd.DataFrame:
    rows = [
        ("n-01", "asset-alpha", at(40, 10), "Wire A", "positive"),  # before the close: aligns to SESSIONS[40]
        ("n-02", "asset-alpha", at(45, 22, 30), "Wire A", "positive"),  # after the close: aligns to SESSIONS[46]
        ("n-03", "asset-beta", at(41, 12), "Wire B", "neutral"),
        ("n-04", "asset-beta", at(50, 9), "Wire A", "negative"),
        ("n-05", "asset-gamma", at(GAMMA_SPIKE - 1, 23), "Wire B", "negative"),
        ("n-06", "asset-gamma", at(55, 14), "Wire B", "positive"),
        ("n-07", "asset-omega", at(52, 15), "Wire A", "neutral"),
        ("n-08", "asset-alpha", at(69, 22), "Wire A", "negative"),  # after the last close: never aligns
    ]
    return pd.DataFrame(rows, columns=["news_id", "primary_asset_id", "published_at", "source_name", "sentiment_label"])


def write_prediction_database(path: Path, prices: pd.DataFrame) -> None:
    """The prediction views as plain tables in a `prediction` schema (the data image builds real views)."""
    with duckdb.connect(str(path)) as db:
        db.register("prices", prices)
        db.register("assets", ASSETS)
        db.execute("CREATE SCHEMA prediction")
        db.execute("CREATE TABLE prediction.asset_entities AS SELECT asset_id, company_name FROM assets")
        db.execute(
            """CREATE TABLE prediction.price_events AS
               SELECT asset_id || ':' || trading_date AS price_id, asset_id,
                      (trading_date::TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL 21 HOUR AS observed_at,
                      adjusted_close, volume
               FROM prices"""
        )
        db.execute(
            """CREATE TABLE prediction.return_outcomes AS
               SELECT asset_id || ':' || trading_date AS outcome_id, asset_id,
                      (trading_date::TIMESTAMP AT TIME ZONE 'UTC') + INTERVAL 7 DAY + INTERVAL 21 HOUR AS realized_at,
                      total_return_1d > 0 AS positive_return
               FROM prices"""
        )


def write_pack(root: Path, *, daily_only: bool = False) -> Path:
    """Write the pack under `root` and return it."""
    (root / "tables").mkdir()
    (root / "structured").mkdir()
    prices = daily_prices()
    tickers = pd.DataFrame({"asset_id": list(TICKERS), "ticker": list(TICKERS.values()), "effective_to": None})
    retired = pd.DataFrame({"asset_id": ["asset-alpha"], "ticker": ["ALPX"], "effective_to": [SESSIONS[10].date()]})
    tables = {
        "assets": ASSETS,
        "ticker_history": pd.concat([tickers, retired], ignore_index=True),
        "daily_prices": prices,
        "asset_relationships": pd.DataFrame(
            {"source_asset_id": ["asset-alpha", "asset-beta"], "target_asset_id": ["asset-beta", "asset-gamma"]}
        ),
    }
    if not daily_only:
        tables["market_news"] = market_news()
    for name, frame in tables.items():
        frame.to_parquet(root / "tables" / f"{name}.parquet", index=False)
    write_prediction_database(root / "structured" / "market_fixture.duckdb", prices)
    manifest = {
        "id": "fixture",
        "version": "1.0.0",
        "profile": "default",
        "structured": {"source": "market_fixture_structured", "database_name": "market_fixture"},
        "analytics": {
            "contract": "market-analytics/v1",
            "news_table": None if daily_only else "market_news",
            "session_close_utc": "21:00",
            "universes": {
                "reviewed_assets": {"description": "The three reviewed issuers.", "where": "is_reviewed"},
                "all_assets": {"description": "Every issuer.", "where": "true"},
            },
            "relationship_graph": {
                "window_start": "2026-06-01",
                "window_end": "2026-08-06",
                "mode": "full_correlation",
            },
        },
        "prediction": {
            "schema": "prediction",
            "anchor": at(55, 21).isoformat(),
            "horizon_sessions": 5,
            "entity": {"table": "asset_entities", "key": "asset_id"},
            "population": {"view": "reviewed_asset_population", "ids": ["asset-alpha", "asset-beta", "asset-gamma"]},
            "tables": PREDICTION_TABLES,
            "templates": TEMPLATES,
        },
    }
    if not daily_only:
        bars = per_symbol(root / "minute-bars", symbols=["asset-alpha", "asset-beta", "asset-gamma"])
        manifest["market"] = {"bars": bars}
    (root / "pack.json").write_text(json.dumps(manifest, indent=2))
    return root
