# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The market importer, on the made-up fixture bars: the rollup, sessions, exclusions and the whole us-equities build.

The fixture's values are chosen to be checked by hand (fixtures/make_minute_bars_fixture.py).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb
import pytest
import yaml
from conftest import FIXTURES
from conftest import MINUTE_BARS

from demo_data import external
from demo_data import market
from demo_data.cli import main
from demo_data.market import Listing
from demo_data.market import MarketError
from demo_data.pack import PackError
from demo_data.pack import load_pack

BARS = {
    "dataset": "minute-bars",
    "files": "market/stocks_1min/*_full_1min_adjsplit.parquet",
    "symbol_from_path": r"([^/]+)_full_1min_adjsplit\.parquet$",
    "columns": {"time": "ts", "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"},
    "frequency": "1min",
    "timezone": "America/New_York",
    "regular_session": ["09:30", "16:00"],
}


@pytest.fixture
def minute_bars(tmp_path) -> external.Dataset:
    """The fixture bars, verified in a scratch copy."""
    manifest = json.loads((MINUTE_BARS / "benchmark-bundle-manifest.json").read_text())
    dataset = external.Dataset(
        "minute-bars", tmp_path / "minute-bars", "benchmark-bundle-manifest.json", manifest["dataset_fingerprint"], 0
    )
    shutil.copytree(MINUTE_BARS, dataset.root)
    external.verify(dataset)
    return dataset


def test_the_rollup_keeps_the_regular_session_through_the_closing_auction(minute_bars, tmp_path):
    path, info = market.daily_rollup(minute_bars, minute_bars.files(), BARS, tmp_path / "cache")

    rows = duckdb.sql(f"SELECT * FROM '{path}' WHERE symbol = 'XAAA' ORDER BY trading_date").fetchall()
    symbol, day, open_, high, low, close, volume, dollar_volume, bars = rows[0]
    assert (symbol, str(day), bars, volume) == ("XAAA", "2025-12-31", 4, 5300)  # 09:30, 12:00, 15:59, 16:00
    assert (open_, high, low, close) == pytest.approx((10.0, 11.0, 9.0, 10.5))  # never the 999 or 0.01 bars
    assert dollar_volume == pytest.approx(10.1 * 100 + 10.2 * 100 + 10.3 * 100 + 10.5 * 5000, rel=1e-6)
    assert (info["symbols"], info["files"], info["cached"]) == (11, 11, False)
    assert market.daily_rollup(minute_bars, minute_bars.files(), BARS, tmp_path / "cache")[1]["cached"]


def test_sessions_drop_the_holiday(minute_bars, tmp_path):
    path, _ = market.daily_rollup(minute_bars, minute_bars.files(), BARS, tmp_path / "cache")
    with duckdb.connect() as db:
        db.execute(f"CREATE VIEW rollup AS FROM '{path}'")
        db.execute(market.SESSIONS)
        days = [str(day) for (day,) in db.execute("SELECT trading_date FROM sessions ORDER BY 1").fetchall()]

    assert days == ["2025-12-31", "2026-01-02", "2026-01-05"]  # not 2026-01-01, where only XCCC has bars


def test_exclusions_drop_other_securities_of_an_issuer_but_keep_share_classes():
    sec = yaml.safe_load((FIXTURES / "sec" / "company_tickers_exchange.json").read_text())
    listings, seen = {}, set()
    for cik, name, ticker, exchange in sec["data"]:
        listings[ticker.replace("-", ".")] = Listing(name, cik, exchange, primary=cik not in seen)
        seen.add(cik)
    symbols = sorted(path.name.split("_")[0] for path in (MINUTE_BARS / "market" / "stocks_1min").iterdir())
    sessions = dict.fromkeys(symbols, 3) | {"XDDD": 1}

    dropped, kept = market.exclude(symbols, sessions, listings, ["warrants", "units", "rights", "preferred"], 2)

    assert dropped == {
        "warrants": ["XAAAW"],  # same issuer as XAAA
        "units": ["XAAAU"],  # not listed: its suffix decides
        "rights": [],
        "preferred": ["XAAA.P", "XAAAL"],  # an unlisted dotted symbol; a same-issuer ticker that is not primary
        "not_listed": ["XCCC"],
        "min_sessions": ["XDDD"],
    }
    assert kept == ["XAAA", "XBBB", "XBBB.B", "XE", "XEU"]  # XBBB.B is a share class; XEU is not a unit of XE


def test_prepare_imports_the_fixture_into_the_us_equities_tables(equities, tmp_path, monkeypatch, capsys):
    data, sources = tmp_path / "data", tmp_path / "sources"
    monkeypatch.setenv("DATA_SOURCE_MINUTE_BARS", str(MINUTE_BARS))
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)  # the stub SEC snapshot is cached: no network
    run = ["--packs-dir", str(equities), "--data-dir", str(data), "--sources-dir", str(sources), "--pack"]

    assert main([*run, "us-equities", "fetch"]) == 0
    assert main([*run, "us-equities", "prepare", "--structured"]) == 0

    build = (data / "active").resolve()
    pack = json.loads((build / "pack.json").read_text())
    structured = pack["parts"]["structured"]
    assert structured["rows"] == {
        "assets": 5,
        "ticker_history": 5,
        "trading_sessions": 3,
        "daily_prices": 15,
        "asset_relationships": 6,
    }
    assert structured["import"]["dropped"] == {
        "warrants": 1,
        "units": 1,
        "rights": 0,
        "preferred": 2,
        "not_listed": 1,
        "min_sessions": 1,
    }
    assert pack["analytics"]["news_table"] is None
    assert pack["market"]["bars"]["root"] == str(sources / "minute-bars")  # for the minute-bar tools
    assert pack["prediction"]["population"] == {
        "view": "top_50_population",
        "ids": ["XAAA", "XBBB", "XBBB.B", "XE", "XEU"],
    }
    with duckdb.connect(str(build / "structured" / "us_equities.duckdb"), read_only=True) as db:
        db.execute("SET TimeZone = 'UTC'")
        assets = db.execute("SELECT asset_id, sector, liquidity_rank FROM assets ORDER BY liquidity_rank").fetchall()
        peers = db.execute("SELECT target_asset_id FROM asset_relationships WHERE source_asset_id = 'XAAA'").fetchall()
        close_at = db.execute("SELECT strftime(close_at, '%H:%M') FROM trading_sessions LIMIT 1").fetchone()
        returns = db.execute(
            "SELECT round(total_return_1d, 6) FROM daily_prices WHERE asset_id = 'XAAA' ORDER BY trading_date"
        ).fetchall()
        # The answer oracles run on the schema; the fixture's three sessions are too few for their answers.
        for oracle in sorted((equities / "us-equities" / "eval" / "oracles").glob("*.sql")):
            db.execute(oracle.read_text()).fetchall()
    assert assets[0] == ("XEU", "Nonclassifiable", 1)  # the highest dollar volume; SEC has no SIC code for it
    assert assets[-1] == ("XAAA", "Manufacturing", 5)
    assert peers == [("XBBB.B",), ("XBBB",)]  # same SIC code, most liquid first
    assert close_at == ("21:00",)  # 16:00 in New York in winter
    assert returns == [(0.0,), (round(11.5 / 10.5 - 1, 6),), (round(12.5 / 11.5 - 1, 6),)]

    # A change that is not in the data (here, a question) makes a new build on the same rollup.
    questions = equities / "us-equities" / "questions.yaml"
    questions.write_text(questions.read_text().replace("Market Leaders", "Leaders"))
    capsys.readouterr()
    assert main([*run, "us-equities", "prepare", "--structured"]) == 0
    assert "daily rollup" in (out := capsys.readouterr().out) and "reused" in out
    assert main([*run, "us-equities", "clean"]) == 0
    assert len(list((data / "cache" / "rollups").iterdir())) == 1  # still used by the active build


def test_the_opt_in_world_news_corpus_is_read_in_place(equities, tmp_path, monkeypatch):
    data, sources = tmp_path / "data", tmp_path / "sources"
    monkeypatch.setenv("DATA_SOURCE_MINUTE_BARS", str(MINUTE_BARS))
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)  # world_news needs no SEC access
    run = ["--packs-dir", str(equities), "--data-dir", str(data), "--sources-dir", str(sources), "--pack"]

    assert main([*run, "us-equities", "fetch"]) == 0
    assert main([*run, "us-equities", "prepare", "--corpora", "world_news"]) == 0

    pack = json.loads(((data / "active").resolve() / "pack.json").read_text())
    assert pack["parts"]["corpus"]["documents"] == {"world_news": 2}
    assert [source["id"] for source in pack["sources"]] == ["market_data", "world_news"]
    assert [question["id"] for question in pack["questions"] if "world_news" in question["sources"]] == [
        "world-news-rates",
        "world-news-cyber",
    ]
    assert [c["id"] for c in pack["conversations"] if "world_news" in c["sources"]] == ["headlines-and-prices"]


def test_prepare_stops_until_the_dataset_is_fetched(equities, tmp_path, capsys):
    run = ["--packs-dir", str(equities), "--data-dir", str(tmp_path / "data"), "--sources-dir", str(tmp_path)]

    assert main([*run, "--pack", "us-equities", "prepare", "--structured"]) == 1
    assert "benchmark-bundle-manifest.json not found; fetch it with `demo.sh data fetch`" in capsys.readouterr().err


def test_bars_must_be_listed_in_the_manifest(minute_bars, tmp_path):
    shutil.copy(
        minute_bars.root / "market/stocks_1min/XAAA_full_1min_adjsplit.parquet",
        minute_bars.root / "market/stocks_1min/XZZZ_full_1min_adjsplit.parquet",
    )

    with pytest.raises(MarketError, match="1 files match .* but are not in the manifest"):
        market.daily_rollup(minute_bars, minute_bars.files(), BARS, tmp_path / "cache")


def test_companies_and_news_can_come_from_the_dataset(minute_bars, tmp_path):
    """A dataset may carry its own companies and ticker-linked news (the synthetic pack's layout)."""
    with duckdb.connect() as db:
        db.execute(
            f"COPY (SELECT * FROM (VALUES ('XAAA', 'Xaaa Inc', 'Manufacturing', 'Semiconductors', '3674', 'Nasdaq',"
            f" 'Makes chips.', true)) t(symbol, company_name, sector, industry, sic_code, exchange, profile,"
            f" is_synthetic)) TO '{minute_bars.root / 'companies.parquet'}'"
        )
    files = [*minute_bars.files(), {"path": "companies.parquet"}]

    listings = market.company_listings("companies.parquet", minute_bars, files, tmp_path / "cache")

    expected = Listing("Xaaa Inc", None, "Nasdaq", sic_code="3674", sector="Manufacturing", industry="Semiconductors")
    assert listings == {"XAAA": Listing(**vars(expected) | {"profile": "Makes chips.", "is_synthetic": True})}
    with pytest.raises(MarketError, match="news.parquet is not in the dataset's manifest"):
        market.dataset_file(minute_bars, files, "news.parquet")


def test_validate_checks_the_market_section(pack_copy):
    pack = pack_copy("us-equities")
    text = (pack / "pack.yaml").read_text()
    (pack / "pack.yaml").write_text(text.replace("  news: null", "  news: news.parquet"))

    with pytest.raises(PackError, match="analytics.news_table must be set exactly when market.news is"):
        load_pack(Path(pack))
