# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import shutil
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fixture_pack import SESSIONS
from fixture_pack import at

from market_analytics.data import FEATURES
from market_analytics.data import ContractError
from market_analytics.data import MarketData
from market_analytics.data import Pack
from market_analytics.data import _peer_correlations
from market_analytics.data import footprint
from market_analytics.data import validate
from market_analytics.models import InvalidRequest


def utc(day: int, hour: int) -> datetime:
    """at() as the frames hold it: naive UTC (data.py)."""
    return at(day, hour).replace(tzinfo=None)


def test_fixture_pack_satisfies_the_contract(pack: Pack) -> None:
    validate(pack)


def test_a_pack_without_news_or_minute_bars_satisfies_the_contract(daily_only: MarketData) -> None:
    validate(daily_only.pack)

    assert (daily_only.pack.news_table, daily_only.pack.minute_bars) == (None, None)
    assert daily_only.news.empty
    assert list(daily_only.news.columns) == [
        "news_id",
        "asset_id",
        "published_at",
        "source_name",
        "sentiment_label",
        "session",
    ]


def test_contract_violations_are_all_listed(pack_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "active"
    shutil.copytree(pack_root, root)
    prices = pd.read_parquet(root / "tables" / "daily_prices.parquet").drop(columns="total_return_1d")
    prices.assign(volume=prices["volume"].astype(str)).to_parquet(root / "tables" / "daily_prices.parquet")
    news = pd.read_parquet(root / "tables" / "market_news.parquet")
    news.assign(sentiment_label="bullish").to_parquet(root / "tables" / "market_news.parquet")
    (root / "tables" / "assets.parquet").unlink()

    with pytest.raises(ContractError) as raised:
        validate(Pack.load(root))

    assert sorted(raised.value.problems) == [
        "daily_prices.total_return_1d is missing",
        "daily_prices.volume is VARCHAR, not integer",
        "market_news.sentiment_label has 8 rows outside ['positive', 'neutral', 'negative']",
        f"table assets is missing ({root / 'tables' / 'assets.parquet'})",
    ]


def test_universes_come_from_the_pack_predicates(data: MarketData) -> None:
    assert data.universe("reviewed_assets") == ("asset-alpha", "asset-beta", "asset-gamma")
    assert len(data.universe("all_assets")) == 4
    with pytest.raises(InvalidRequest, match="unknown universe"):
        data.universe("everything")


def test_assets_resolve_by_id_ticker_name_or_unique_name_prefix(data: MarketData) -> None:
    references = ["asset-alpha", "beta", "gama", "Gamma Grid", "OMEGA  holdings", "alph"]
    assert data.resolve_assets(references) == ["asset-alpha", "asset-beta", "asset-gamma", "asset-omega"]
    with pytest.raises(InvalidRequest, match="unknown asset 'ALPX'"):
        data.resolve_assets(["ALPX"])  # a retired ticker


def test_an_ambiguous_reference_names_only_a_few_of_its_matches(pack_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "active"
    shutil.copytree(pack_root, root)
    namesakes = pd.DataFrame(
        {
            "asset_id": [f"asset-alpha-{n}" for n in range(1, 8)],
            "company_name": [f"Alpha Unit {n}" for n in range(1, 8)],
            "is_reviewed": False,
        }
    )
    assets = pd.read_parquet(root / "tables" / "assets.parquet")
    pd.concat([assets, namesakes]).to_parquet(root / "tables" / "assets.parquet", index=False)
    data = MarketData.load(Pack.load(root))

    assert data.resolve_assets(["Alpha Robotics", "alpha unit 3"]) == ["asset-alpha", "asset-alpha-3"]
    with pytest.raises(InvalidRequest) as raised:
        data.resolve_assets(["alpha"])
    assert str(raised.value) == (
        "ambiguous asset 'alpha': matches 8 assets, "
        "e.g. ['asset-alpha', 'asset-alpha-1', 'asset-alpha-2', 'asset-alpha-3', 'asset-alpha-4']"
    )


def test_an_id_or_ticker_wins_over_another_companys_name(pack_root: Path, tmp_path: Path) -> None:
    """Real tickers are often another company's first word: ACI is Albertsons, and ACI Worldwide is ACIW."""
    root = tmp_path / "active"
    shutil.copytree(pack_root, root)
    assets = pd.read_parquet(root / "tables" / "assets.parquet")
    lookalike = pd.DataFrame({"asset_id": ["asset-beta-grid"], "company_name": ["Beta Grid Co"], "is_reviewed": False})
    pd.concat([assets, lookalike]).to_parquet(root / "tables" / "assets.parquet", index=False)
    data = MarketData.load(Pack.load(root))

    assert data.resolve_assets(["BETA", "asset-beta"]) == ["asset-beta"]  # the ticker, not "Beta Grid Co"
    assert data.resolve_assets(["beta grid"]) == ["asset-beta-grid"]


def test_news_aligns_to_the_first_session_at_or_after_publication(data: MarketData) -> None:
    sessions = data.news.set_index("news_id")["session"]
    assert sessions["n-01"] == 41  # published before the 21:00 close of SESSIONS[40]
    assert sessions["n-02"] == 47  # published after the close of SESSIONS[45]
    assert sessions["n-08"] == 0  # published after the last close
    assert data.prices.set_index(["asset_id", "session"]).loc[("asset-alpha", 47), "timestamp"] == utc(46, 21)


def test_features_match_a_per_asset_rolling_computation(data: MarketData) -> None:
    """The vectorized windows in data.py must equal the obvious per-asset groupby version."""
    expected = []
    for asset_id, bars in data.prices.groupby("asset_id"):
        log_volume = np.log(bars["volume"].astype(float))
        prior = log_volume.shift(1).rolling(20, min_periods=20)
        frame = pd.DataFrame(
            {
                "asset_id": asset_id,
                "timestamp": bars["timestamp"],
                "adjusted_return_1d": bars["total_return_1d"],
                "adjusted_return_5d": bars["adjusted_close"] / bars["adjusted_close"].shift(5) - 1,
                "realized_volatility_20d": bars["total_return_1d"].rolling(20, min_periods=2).std(),
                "log_volume_deviation_20d": (log_volume - prior.mean()) / prior.std(),
            }
        )
        expected.append(frame[prior.std().gt(0)].dropna())
    expected_frame = pd.concat(expected).astype(dict.fromkeys(FEATURES, "float32")).reset_index(drop=True)

    pd.testing.assert_frame_equal(data.features, expected_frame)
    assert data.features.groupby("asset_id")["timestamp"].min().eq(utc(20, 21)).all()


def test_relationship_graph_links_every_pair_or_only_declared_peers(pack: Pack, data: MarketData) -> None:
    assert data.graph.number_of_edges() == 12  # 4 assets, both directions
    alpha_beta = data.edges.set_index(["source", "target"]).loc[("asset-alpha", "asset-beta"), "correlation"]
    assert alpha_beta > 0.9

    sparse = MarketData.load(replace(pack, graph_mode="sparse_declared_peers"))
    assert sorted(sparse.graph.edges) == [
        ("asset-alpha", "asset-beta"),
        ("asset-beta", "asset-alpha"),
        ("asset-beta", "asset-gamma"),
        ("asset-gamma", "asset-beta"),
    ]
    # Computed from the declared pairs alone, the correlations equal the full matrix's.
    peers = sparse.edges.set_index(["source", "target"])["correlation"]
    full = data.edges.set_index(["source", "target"])["correlation"]
    pd.testing.assert_series_equal(peers, full.loc[peers.index], rtol=1e-12)


def test_peer_correlations_use_the_sessions_both_assets_traded(pack: Pack) -> None:
    """As DataFrame.corr does: pairwise complete sessions, and no edge for a flat series."""
    dates = pd.date_range("2026-06-01", periods=6)
    returns = {
        "asset-alpha": [0.01, np.nan, -0.02, 0.03, np.nan, 0.01],
        "asset-beta": [0.02, 0.01, -0.01, 0.02, -0.03, np.nan],
        "asset-gamma": [0.0] * 6,
    }
    window = pd.DataFrame(
        [(day, asset, value) for asset, values in returns.items() for day, value in zip(dates, values, strict=True)],
        columns=["trading_date", "asset_id", "total_return_1d"],
    )

    edges = _peer_correlations(pack, window).set_index(["source", "target"])["correlation"]

    expected = window.pivot(index="trading_date", columns="asset_id", values="total_return_1d").corr()
    assert edges.to_dict() == pytest.approx(
        {
            ("asset-alpha", "asset-beta"): expected.loc["asset-alpha", "asset-beta"],
            ("asset-beta", "asset-alpha"): expected.loc["asset-beta", "asset-alpha"],
        },
        rel=1e-12,
    )


def test_price_bars_are_stamped_at_the_session_close(data: MarketData) -> None:
    first = data.prices.iloc[0]
    assert first["timestamp"] == utc(0, 21)
    assert first["session"] == 1
    assert pd.isna(first["adjusted_return_1d"])
    assert len(data.prices) == 4 * len(SESSIONS)


def test_the_footprint_comes_from_the_parquet_footers(pack: Pack) -> None:
    assert footprint(pack, "gpu") == "280 price rows and 8 news rows: about 0.0 GB on the gpu"
    assert footprint(replace(pack, news_table=None), "cpu").startswith("280 price rows: ")


def test_every_timestamp_is_naive_utc_nanoseconds(data: MarketData) -> None:
    """One timestamp model for both engines: cudf.pandas falls back to pandas on tz-aware columns."""
    frames = {"prices": data.prices, "features": data.features, "news": data.news}
    dtypes = {f"{name}.{column}": str(kind) for name, frame in frames.items() for column, kind in frame.dtypes.items()}
    assert {column: kind for column, kind in dtypes.items() if "datetime" in kind} == {
        "prices.trading_date": "datetime64[ns]",
        "prices.timestamp": "datetime64[ns]",
        "features.timestamp": "datetime64[ns]",
        "news.published_at": "datetime64[ns]",
    }
