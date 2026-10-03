# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The market tools, called through the same dispatcher the worker uses."""

import json
import math
import shutil
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from fixture_bars import minute_bars
from fixture_pack import GAMMA_SPIKE
from fixture_pack import at

from market_analytics import tools
from market_analytics.data import MarketData
from market_analytics.data import Pack
from market_analytics.tools import market_scan

JUNE = {"start": datetime(2026, 6, 1, tzinfo=UTC), "end": datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC)}


def run(data: MarketData, tool: str, **arguments: Any) -> dict[str, Any]:
    return tools.run(data, tool, arguments)


def closes(data: MarketData, asset_id: str) -> pd.Series:
    """The asset's adjusted closes by naive UTC session timestamp, as the frames hold them."""
    prices = data.prices[data.prices["asset_id"] == asset_id]
    return pd.Series(prices["adjusted_close"].to_numpy(), index=prices["timestamp"].to_numpy())


def june_return(data: MarketData, asset_id: str) -> float:
    """From the last close before June to the last in June, so every June session's return counts."""
    start, end = (JUNE[key].replace(tzinfo=None) for key in ("start", "end"))
    close = closes(data, asset_id)
    return close[close.index <= end].iloc[-1] / close[close.index < start].iloc[-1] - 1


def test_market_scan_ranks_both_ends_of_a_universe(data: MarketData) -> None:
    returns = {asset: june_return(data, asset) for asset in data.universe("reviewed_assets")}
    leaders = run(data, "market_scan", universe_id="reviewed_assets", metrics=["return", "volume"], limit=2, **JUNE)
    laggard = run(data, "market_scan", universe_id="reviewed_assets", metrics=["return"], direction="lowest", **JUNE)

    assert leaders["status"] == "succeeded"
    assert leaders["engine"] == {
        "device": "cpu",
        "library": "pandas",
        "version": leaders["engine"]["version"],
        "engine_id": "pandas-cpu.v1",
    }
    assert leaders["rows_scanned"] == 3 * 22  # three assets, 22 June sessions
    assert leaders["asset_count"] == 3
    payload = leaders["payload"]
    assert payload["assets_ranked"] == 3
    assert [row["asset_id"] for row in payload["observations"]] == sorted(returns, key=returns.get, reverse=True)[:2]
    assert payload["observations"][0]["values"]["return"] == pytest.approx(max(returns.values()))
    assert set(payload["observations"][0]["values"]) == {"return", "volume"}
    assert laggard["payload"]["observations"][0]["asset_id"] == min(returns, key=returns.get)


def test_a_return_runs_from_the_close_before_the_window(data: MarketData) -> None:
    """The 5 sessions ending June 30 are 5 daily returns, as the volatility beside them is; an asset with no
    earlier session starts from its first close."""
    result = run(data, "market_scan", universe_id="reviewed_assets", metrics=["return"], sessions=5, end=JUNE["end"])
    for row in result["payload"]["observations"]:
        close = closes(data, row["asset_id"])
        close = close[close.index <= JUNE["end"].replace(tzinfo=None)]
        assert row["values"]["return"] == pytest.approx(close.iloc[-1] / close.iloc[-6] - 1)
        assert row["observation_count"] == 5

    first = {"start": datetime(2026, 5, 1, tzinfo=UTC), "end": datetime(2026, 5, 8, 23, 59, tzinfo=UTC)}
    result = run(data, "market_scan", universe_id="reviewed_assets", metrics=["return"], **first)
    for row in result["payload"]["observations"]:
        close = closes(data, row["asset_id"])
        assert row["values"]["return"] == pytest.approx(close.iloc[5] / close.iloc[0] - 1)  # May 1 to May 8

    summary = run(data, "price_context", asset_ids=["ALPH"], include_series=False, **JUNE)["payload"]["summaries"][0]
    alpha = closes(data, "asset-alpha")
    assert summary["start_price"] == pytest.approx(alpha[alpha.index < JUNE["start"].replace(tzinfo=None)].iloc[-1])
    assert summary["total_return"] == pytest.approx(june_return(data, "asset-alpha"))


def test_market_scan_zscore_centers_the_scores(data: MarketData) -> None:
    result = run(data, "market_scan", universe_id="all_assets", metrics=["volatility"], comparison="zscore", **JUNE)
    scores = [row["score"] for row in result["payload"]["observations"]]
    assert scores == sorted(scores, reverse=True)
    assert sum(scores) == pytest.approx(0, abs=1e-9)


def test_market_scan_counts_the_sessions_ending_at_end(data: MarketData) -> None:
    # "The 22 sessions ending June 30" are the 22 June sessions
    by_dates = run(data, "market_scan", universe_id="reviewed_assets", metrics=["return"], **JUNE)
    by_count = run(data, "market_scan", universe_id="reviewed_assets", metrics=["return"], sessions=22, end=JUNE["end"])
    assert by_count["payload"] == by_dates["payload"]
    assert {row["observation_count"] for row in by_count["payload"]["observations"]} == {22}
    shorter = run(data, "market_scan", universe_id="reviewed_assets", metrics=["return"], sessions=5, end=JUNE["end"])
    assert {row["observation_count"] for row in shorter["payload"]["observations"]} == {5}

    neither = run(data, "market_scan", universe_id="reviewed_assets", metrics=["return"], end=JUNE["end"])
    assert neither["error"]["code"] == "invalid_request"


def test_market_scan_with_start_and_sessions_says_how_to_fix_the_call(data: MarketData) -> None:
    """Still rejected, with the argument to drop and the call to make instead, from the call's own arguments: a
    model that read only "not both" retried the same call over and over."""
    arguments = {"universe_id": "reviewed_assets", "metrics": ["return", "volatility"], "sessions": 5, **JUNE}
    both = run(data, "market_scan", **arguments)

    assert both["status"] == "failed"
    assert both["payload"] is None
    assert both["error"]["code"] == "invalid_request"
    message = both["error"]["message"]
    assert message.startswith("start and sessions cannot both be given")
    assert "again without start" in message
    assert "drop sessions and keep start and end" in message
    retry = json.loads(message[message.index("{") : message.index("}") + 1])
    assert retry == {
        "universe_id": "reviewed_assets",
        "end": "2026-06-30T23:59:59Z",
        "sessions": 5,
        "metrics": ["return", "volatility"],
    }
    # The suggested call works, and fits the receipt's error summary (600 characters)
    del arguments["start"]
    assert run(data, "market_scan", **arguments)["status"] == "succeeded"
    assert len(message) <= 600


def test_market_scan_reports_an_empty_window_and_invalid_arguments(data: MarketData) -> None:
    empty = run(
        data,
        "market_scan",
        universe_id="all_assets",
        metrics=["return"],
        start=datetime(2020, 1, 1, tzinfo=UTC),
        end=datetime(2020, 2, 1, tzinfo=UTC),
    )
    assert empty["status"] == "empty"
    assert empty["payload"]["observations"] == []

    duplicate = run(data, "market_scan", universe_id="all_assets", metrics=["return", "return"], **JUNE)
    backwards = run(
        data, "market_scan", universe_id="all_assets", metrics=["return"], start=JUNE["end"], end=JUNE["start"]
    )
    for result in (duplicate, backwards):
        assert result["status"] == "failed"
        assert result["error"]["code"] == "invalid_request"
        assert result["payload"] is None


def test_unexpected_errors_become_execution_failures(data: MarketData, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: Any, **__: Any) -> None:
        raise ZeroDivisionError

    monkeypatch.setitem(tools.TOOLS, "market_scan", tools.Tool(broken, "tabular", market_scan.LIMITATIONS))
    result = run(data, "market_scan", universe_id="all_assets", metrics=["return"], **JUNE)
    assert result["status"] == "failed"
    assert result["error"] == {
        "code": "execution_failed",
        "message": "market_scan could not complete; see the server log.",
    }


def test_anomaly_scan_ranks_the_planted_crash_first(data: MarketData) -> None:
    result = run(
        data,
        "market_anomaly_scan",
        universe_id="reviewed_assets",
        training_start=at(20, 0),
        training_end=at(49, 23),
        scoring_start=at(50, 0),
        scoring_end=at(69, 23),
        limit=3,
    )

    assert result["status"] == "succeeded"
    assert result["engine"]["library"] == "scikit-learn"
    assert result["engine"]["engine_id"] == "sklearn-pca-anomaly-cpu.v1"
    assert result["asset_count"] == 3  # the reviewed assets, in both windows
    payload = result["payload"]
    assert payload["policy_id"] == "pca-reconstruction-market-v1"
    assert (payload["training_observations"], payload["scoring_observations"]) == (90, 60)
    top = payload["observations"]
    assert [row["rank"] for row in top] == [1, 2, 3]
    assert top[0]["cohort_percentile"] == 100.0
    # The crash and the high-volatility sessions after it are the most unusual.
    assert {row["asset_id"] for row in top} == {"asset-gamma"}
    crash = next(row for row in top if datetime.fromisoformat(row["timestamp"]) == at(GAMMA_SPIKE, 21))
    assert crash["is_anomaly"] and crash["decision_score"] < 0
    assert crash["observed_deviations"]["adjusted_return_1d"] < -10  # a far larger fall than in training
    assert crash["observed_deviations"]["log_volume_deviation_20d"] > 10


@pytest.mark.filterwarnings("ignore:invalid value encountered:RuntimeWarning")
def test_anomaly_scan_fails_on_non_finite_features(data: MarketData) -> None:
    """PCA skips its own finiteness check (see anomaly.py), so the tool must still refuse NaN or infinity."""
    features = data.features.copy()
    features.loc[features["asset_id"] == "asset-gamma", "log_volume_deviation_20d"] = np.inf
    result = run(
        replace(data, features=features),
        "market_anomaly_scan",
        universe_id="reviewed_assets",
        training_start=at(20, 0),
        training_end=at(49, 23),
        scoring_start=at(50, 0),
        scoring_end=at(69, 23),
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "execution_failed"


def test_anomaly_scan_leaves_out_a_session_with_no_volume(pack_root: Path, tmp_path: Path) -> None:
    """log(0) is -inf: the session has no volume deviation, so it and the 20 sessions it is a baseline for drop out."""
    root = tmp_path / "active"
    shutil.copytree(pack_root, root)
    path = root / "tables" / "daily_prices.parquet"
    prices = pd.read_parquet(path)
    prices.loc[prices.index[prices["asset_id"] == "asset-alpha"][55], "volume"] = 0
    prices.to_parquet(path)
    data = MarketData.load(Pack.load(root))

    result = run(
        data,
        "market_anomaly_scan",
        universe_id="reviewed_assets",
        training_start=at(20, 0),
        training_end=at(49, 23),
        scoring_start=at(50, 0),
        scoring_end=at(69, 23),
    )

    assert result["status"] == "succeeded"
    assert result["payload"]["scoring_observations"] == 60 - 15  # alpha's sessions 55 to 69
    alpha = data.features[data.features["asset_id"] == "asset-alpha"]
    assert at(55, 21).replace(tzinfo=None) not in set(alpha["timestamp"])


def test_anomaly_scan_filters_by_percentile_and_rejects_overlapping_windows(data: MarketData) -> None:
    windows = {"training_start": at(20, 0), "training_end": at(49, 23), "scoring_end": at(69, 23)}
    top_decile = run(
        data,
        "market_anomaly_scan",
        universe_id="reviewed_assets",
        scoring_start=at(50, 0),
        minimum_percentile=90,
        **windows,
    )
    assert all(row["cohort_percentile"] >= 90 for row in top_decile["payload"]["observations"])
    assert len(top_decile["payload"]["observations"]) == 7  # ranks 1-7 of 60 are at or above the 90th percentile

    overlapping = run(data, "market_anomaly_scan", universe_id="reviewed_assets", scoring_start=at(40, 0), **windows)
    assert overlapping["error"]["code"] == "invalid_request"


def test_price_context_summarizes_daily_bars_next_to_a_weekly_series(data: MarketData) -> None:
    result = run(data, "price_context", asset_ids=["ALPH", "Beta Beverages"], frequency="weekly", point_limit=3, **JUNE)

    payload = result["payload"]
    assert [summary["asset_id"] for summary in payload["summaries"]] == ["asset-alpha", "asset-beta"]
    alpha = payload["summaries"][0]
    # The series has one point per week; its first is the first week's close (Friday 2026-06-05).
    assert len(payload["series"]) == 3 and payload["series_truncated"]
    assert result["warnings"] == ["The series is cut to the first 3 points."]
    assert datetime.fromisoformat(payload["series"][0]["timestamp"]) == datetime(2026, 6, 5, 21, tzinfo=UTC)
    # The summary is the daily one, whatever the frequency.
    daily = run(data, "price_context", asset_ids=["ALPH", "Beta Beverages"], include_series=False, **JUNE)["payload"]
    assert payload["summaries"] == daily["summaries"]
    assert alpha["observation_count"] == 22  # June's trading sessions, not its five weeks
    assert datetime.fromisoformat(alpha["start_timestamp"]) == datetime(2026, 6, 1, 21, tzinfo=UTC)
    assert datetime.fromisoformat(alpha["end_timestamp"]) == datetime(2026, 6, 30, 21, tzinfo=UTC)
    assert alpha["total_return"] == pytest.approx(alpha["end_price"] / alpha["start_price"] - 1)
    monthly = run(data, "price_context", asset_ids=["ALPH"], frequency="monthly", **JUNE)["payload"]
    assert monthly["summaries"][0] == alpha
    assert len(monthly["series"]) == 1


def test_price_context_rejects_unknown_assets(data: MarketData) -> None:
    result = run(data, "price_context", asset_ids=["ALPH", "NOPE"], **JUNE)
    assert result["error"] == {"code": "invalid_request", "message": "unknown asset 'NOPE'"}


def test_sentiment_timeline_counts_labels_per_period(data: MarketData) -> None:
    window = {"start": at(0, 0), "end": at(69, 23, 59)}
    result = run(data, "sentiment_timeline", frequency="monthly", **window)

    payload = result["payload"]
    assert payload["articles_considered"] == 8
    assert sum(point["article_count"] for point in payload["points"]) == 8
    for point in payload["points"]:
        assert point["positive_count"] + point["neutral_count"] + point["negative_count"] == point["article_count"]
    assert [point["period_start"][:10] for point in payload["points"]] == ["2026-06-01", "2026-07-01", "2026-08-01"]

    recent = run(data, "sentiment_timeline", frequency="monthly", point_limit=1, asset_ids=["asset-alpha"], **window)
    assert [point["period_start"][:10] for point in recent["payload"]["points"]] == ["2026-08-01"]
    assert recent["payload"]["points_truncated"]


def test_news_price_relationship_measures_forward_returns(data: MarketData) -> None:
    result = run(
        data,
        "analyze_news_price_relationship",
        published_from=at(0, 0),
        published_to=at(69, 23, 59),
        return_horizon_sessions=2,
        source_names=["Wire A"],
    )

    payload = result["payload"]
    # n-08 comes after the last close, and n-07's two-session horizon is inside the data, so 4 of 5 align.
    assert (payload["eligible_event_count"], payload["aligned_event_count"]) == (5, 4)
    assert payload["coverage_ratio"] == 0.8
    first = payload["events"][0]
    assert first["news_id"] == "n-01"
    closes = data.prices[data.prices["asset_id"] == "asset-alpha"].set_index("session")["adjusted_close"]
    assert first["forward_return"] == pytest.approx(closes[43] / closes[41] - 1)
    assert datetime.fromisoformat(first["aligned_session"]) == at(40, 21)
    assert datetime.fromisoformat(first["outcome_session"]) == at(42, 21)
    labels = {summary["sentiment_label"]: summary["event_count"] for summary in payload["summaries"]}
    assert labels == {"negative": 1, "neutral": 1, "positive": 2}
    assert -1 <= payload["sentiment_return_correlation"] <= 1


def test_news_tools_keep_only_a_universes_news(data: MarketData) -> None:
    window = {"published_from": at(0, 0), "published_to": at(69, 23, 59)}
    reviewed = run(data, "analyze_news_price_relationship", universe_id="reviewed_assets", **window)
    timeline = run(data, "sentiment_timeline", universe_id="reviewed_assets", start=at(0, 0), end=at(69, 23, 59))

    # n-07 is asset-omega's, which is not reviewed; the other seven articles are.
    assert reviewed["payload"]["eligible_event_count"] == 7
    assert {event["asset_id"] for event in reviewed["payload"]["events"]} == set(data.universe("reviewed_assets"))
    assert timeline["payload"]["articles_considered"] == 7


def test_news_price_relationship_without_aligned_events_is_empty(data: MarketData) -> None:
    result = run(data, "analyze_news_price_relationship", published_from=at(69, 22), published_to=at(69, 23))
    assert result["status"] == "empty"
    assert result["payload"]["eligible_event_count"] == 1
    assert result["warnings"] == ["No article had a complete forward-return window."]


def test_market_relationships_rank_pagerank_centrality(data: MarketData) -> None:
    result = run(data, "analyze_market_relationships", top_k=2)

    assert result["engine"]["library"] == "networkx"
    assert result["engine"]["engine_id"] == "networkx-pagerank-cpu.v1"
    payload = result["payload"]
    assert (payload["node_count"], payload["edge_count"]) == (4, 12)
    assert result["asset_count"] == 4
    assert payload["window_start"] == "2026-06-01"
    assert [asset["rank"] for asset in payload["central_assets"]] == [1, 2]
    assert payload["strongest_edges"][0]["source_asset_id"] == "asset-alpha"
    assert payload["strongest_edges"][0]["target_asset_id"] == "asset-beta"

    everyone = run(data, "analyze_market_relationships", top_k=50)["payload"]["central_assets"]
    assert math.isclose(sum(asset["centrality"] for asset in everyone), 1.0)


# The three days of fixture minute bars, as whole UTC days: in New York they hold the same three sessions.
THREE_DAYS = {"start": datetime(2026, 6, 29, tzinfo=UTC), "end": datetime(2026, 7, 1, 23, 59, 59, tzinfo=UTC)}


def regular_session(asset_id: str, day: str) -> Any:
    bars = minute_bars(["asset-alpha", "asset-beta", "asset-gamma"])
    clock = bars["ts"].dt.strftime("%H:%M")
    return bars[
        (bars["symbol"] == asset_id) & (bars["ts"].dt.strftime("%Y-%m-%d") == day) & clock.between("09:30", "16:00")
    ]


def test_intraday_scan_ranks_asset_sessions_from_the_minute_bars(data: MarketData) -> None:
    result = run(data, "intraday_scan", universe_id="reviewed_assets", **THREE_DAYS)

    assert result["status"] == "succeeded"
    assert result["rows_scanned"] == 9 * 391  # three assets, three sessions, 09:30 to 16:00
    payload = result["payload"]
    assert (payload["assets_scanned"], payload["sessions_scanned"], payload["files_read"]) == (3, 9, 3)
    ranges = [row["intraday_range"] for row in payload["observations"]]
    assert len(ranges) == 9 and ranges == sorted(ranges, reverse=True)

    top = payload["observations"][0]
    bars = regular_session(top["asset_id"], top["session"])
    close, volume, clock = bars["close"].astype("float64"), bars["volume"], bars["ts"].dt.strftime("%H:%M")
    assert top["bar_count"] == 391
    assert (top["open"], top["close"]) == pytest.approx((bars["open"].iloc[0], close.iloc[-1]))
    assert top["intraday_range"] == pytest.approx(bars["high"].max() / bars["low"].min() - 1)
    assert top["vwap"] == pytest.approx((close * volume).sum() / volume.sum())
    assert top["realized_volatility"] == pytest.approx(np.sqrt((close.pct_change() ** 2).sum()))
    assert top["max_drawdown"] == pytest.approx((close / close.cummax() - 1).min())
    assert top["opening_volume_share"] == pytest.approx(volume[clock < "10:00"].sum() / volume.sum())
    assert top["closing_volume_share"] == pytest.approx(volume[clock > "15:30"].sum() / volume.sum())


def test_intraday_scan_takes_named_assets_and_any_metric(data: MarketData) -> None:
    result = run(data, "intraday_scan", asset_ids=["ALPH"], rank_by="volume", direction="lowest", limit=2, **THREE_DAYS)

    rows = result["payload"]["observations"]
    assert [row["asset_id"] for row in rows] == ["asset-alpha"] * 2
    assert rows[0]["volume"] <= rows[1]["volume"]
    assert rows[0]["volume"] == pytest.approx(regular_session("asset-alpha", rows[0]["session"])["volume"].sum())


def test_intraday_scan_reports_an_empty_window_and_invalid_arguments(data: MarketData) -> None:
    empty = run(data, "intraday_scan", universe_id="all_assets", start=at(60, 0), end=at(69, 23))
    neither = run(data, "intraday_scan", **THREE_DAYS)

    assert empty["status"] == "empty"
    assert empty["warnings"] == ["No minute bars matched the assets and window."]
    assert neither["error"] == {
        "code": "invalid_request",
        "message": "name the assets (asset_ids) or a universe (universe_id)",
    }


def test_tools_without_their_data_in_the_pack_report_it(daily_only: MarketData) -> None:
    window = {"start": at(0, 0), "end": at(69, 23)}
    news = run(daily_only, "sentiment_timeline", **window)
    reaction = run(daily_only, "analyze_news_price_relationship", published_from=at(0, 0), published_to=at(69, 23))
    intraday = run(daily_only, "intraday_scan", universe_id="all_assets", **THREE_DAYS)

    assert news["status"] == reaction["status"] == intraday["status"] == "failed"
    assert news["error"]["code"] == reaction["error"]["code"] == "news_unavailable"
    assert news["error"]["message"].startswith("The active data pack has no ticker-linked news table")
    assert intraday["error"]["code"] == "minute_bars_unavailable"
    assert run(daily_only, "market_scan", universe_id="all_assets", metrics=["return"], **JUNE)["status"] == "succeeded"


def test_a_result_records_its_timing_to_the_microsecond(data: MarketData) -> None:
    """The receipt card shows the setup and engine times as recorded, with sub-millisecond precision: the setup
    and the calculation are measured spans of the engine's whole handling of the call."""
    timing = run(data, "price_context", asset_ids=["ALPH", "BETA"], **JUNE)["timing"]

    assert set(timing) == {"compute_ms", "setup_ms", "engine_ms", "total_ms"}
    assert 0 < timing["setup_ms"] < timing["engine_ms"]
    assert 0 < timing["compute_ms"] < timing["engine_ms"]
    assert timing["setup_ms"] + timing["compute_ms"] == pytest.approx(timing["engine_ms"])
    assert not all(float(value).is_integer() for value in timing.values())


@pytest.mark.parametrize(
    ("tool", "arguments", "assets"),
    [
        ("price_context", {"asset_ids": ["ALPH", "BETA"], **JUNE}, 2),
        ("sentiment_timeline", {"universe_id": "reviewed_assets", **JUNE}, 2),  # June's articles cover two of the three
        ("intraday_scan", {"universe_id": "reviewed_assets", **THREE_DAYS}, 3),
    ],
)
def test_a_result_counts_the_assets_it_ran_over(data: MarketData, tool: str, arguments: dict, assets: int) -> None:
    result = run(data, tool, **arguments)
    assert result["status"] == "succeeded"
    assert result["asset_count"] == assets
    assert result["engine"]["engine_id"] == "pandas-cpu.v1"
