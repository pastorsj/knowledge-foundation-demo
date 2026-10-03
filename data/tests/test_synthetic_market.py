# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The synthetic-market pack at its ci profile: the committed Nemotron text plus seeds, no key and no network.

The generator's raw dataset is checked on its own (deterministic, and its minute bars roll up exactly to the
daily bars); the slow tests build the pack end to end through the market importer.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import duckdb
import pytest
import yaml

from demo_data.cli import main
from demo_data.pack import DATA_ROOT
from demo_data.pack import load_pack

PACK = DATA_ROOT / "packs" / "synthetic-market"
CI = {"issuers": 12, "start": "2026-06-01", "end": "2026-08-31", "frequency": "1min"}
ORACLE_ROWS = {
    "large_universe_scan.sql": 12,
    "market_leaders.sql": 12,
    "negative_news.sql": 12,
    "intraday_ranges.sql": 120,  # 12 issuers, 10 sessions
    "news_sentiment_reaction.sql": 3,
    "peer_pair_correlations.sql": 0,  # the 12 story issuers are all in different industries
    "sector_breakdown.sql": 6,
    "story_event_context.sql": 23,
}


def generator():
    spec = importlib.util.spec_from_file_location("synthetic_market_build", PACK / "generator" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def raw(tmp_path_factory) -> dict[str, Path]:
    """The ci dataset twice, and its daily-bar twin."""
    build = generator()
    model = yaml.safe_load((PACK / "generator" / "model.yaml").read_text())
    text = build.read_text(PACK / "text", model, CI["issuers"])
    out = {name: tmp_path_factory.mktemp(name) for name in ("minutes", "again", "daily")}
    build.generate(model, CI, text, out["minutes"])
    build.generate(model, CI, text, out["again"])
    build.generate(model, CI | {"frequency": "1d"}, text, out["daily"])
    return out


def test_the_pack_validates():
    pack = load_pack(PACK)
    assert pack.manifest["analytics"]["news_table"] == "company_news"
    assert {source["id"] for source in pack.manifest["sources"]} == {"market_data", "sec_filings", "market_regulations"}
    assert sum(question.get("featured", False) for question in pack.questions) == 6
    assert main(["--pack", "synthetic-market", "validate"]) == 0


def test_the_committed_text_passed_its_checks():
    checks = json.loads((PACK / "text" / "checks.json").read_text())
    companies = [json.loads(line) for line in (PACK / "text" / "companies.jsonl").read_text().splitlines()]
    assert checks["issuers"] == len(companies) >= 2000
    assert [company["slot"] for company in companies] == list(range(len(companies)))
    assert len({company["ticker"] for company in companies}) == len(companies)
    assert all(company["company_name"].split()[0] == company["name_root"] for company in companies)


def test_the_dataset_is_deterministic(raw):
    first = json.loads((raw["minutes"] / "manifest.json").read_text())
    again = json.loads((raw["again"] / "manifest.json").read_text())
    assert first["dataset_fingerprint"] == again["dataset_fingerprint"]
    assert [entry["path"] for entry in first["files"]][:2] == ["bars/month=2026-06/part-000.parquet",
                                                               "bars/month=2026-07/part-000.parquet"]  # fmt: skip


def test_minute_bars_roll_up_exactly_to_the_daily_bars(raw):
    rollup = """
        SELECT symbol, CAST(time AS DATE) AS day, arg_min(open, time) AS o, max(high) AS h, min(low) AS l,
          arg_max(close, time) AS c, sum(volume) AS v, count(*) AS bars
        FROM read_parquet('{}/bars/*/*.parquet') GROUP BY ALL
    """
    minutes = duckdb.sql(rollup.format(raw["minutes"])).fetchall()
    daily = duckdb.sql(rollup.format(raw["daily"])).fetchall()
    assert len(minutes) == 12 * 64
    assert {row[:7] for row in minutes} == {row[:7] for row in daily}
    assert {row[7] for row in minutes} == {391} and {row[7] for row in daily} == {1}
    bad = duckdb.sql(
        f"SELECT count(*) FROM read_parquet('{raw['minutes']}/bars/*/*.parquet') "
        "WHERE high < greatest(open, close) OR low > least(open, close) OR volume < 0"
    ).fetchone()
    assert bad == (0,)


def test_the_news_is_labelled_synthetic_and_linked_to_tickers(raw):
    news = duckdb.sql(f"SELECT * FROM read_parquet('{raw['minutes']}/news.parquet')").to_arrow_table().to_pylist()
    companies = duckdb.sql(f"SELECT symbol, is_synthetic FROM read_parquet('{raw['minutes']}/companies.parquet')")
    symbols = dict(companies.fetchall())
    assert all(symbols.values()) and len(symbols) == 12
    assert {item["source_name"] for item in news} == {"Synthetic Newswire"}
    assert {item["symbol"] for item in news} <= set(symbols)
    stories = [item for item in news if item["is_story"]]
    assert len(stories) == 12 and all(item["summary"] for item in stories)


@pytest.fixture(scope="module")
def build(tmp_path_factory) -> Path:
    data = tmp_path_factory.mktemp("data")
    command = ["--pack", "synthetic-market", "--data-dir", str(data), "prepare", "--structured", "--profile", "ci"]
    assert main(command) == 0
    return (data / "active").resolve()


@pytest.fixture(scope="module")
def database(build):
    with duckdb.connect(str(build / "structured" / "synthetic_market.duckdb"), read_only=True) as connection:
        yield connection


@pytest.mark.slow
class TestTheCiBuild:
    def test_rows_and_verification(self, build):
        pack = json.loads((build / "pack.json").read_text())
        rows = pack["parts"]["structured"]["rows"]
        assert (rows["assets"], rows["daily_prices"], rows["trading_sessions"]) == (12, 768, 64)
        assert rows["company_news"] > 12
        assert len(pack["prediction"]["population"]["ids"]) == 12
        assert main(["--data-dir", str(build.parents[1]), "verify"]) == 0

    def test_the_story_issuers_are_the_12_most_liquid(self, database):
        story = database.execute("SELECT DISTINCT primary_asset_id FROM company_news WHERE is_story").fetchall()
        top = database.execute("SELECT asset_id FROM assets WHERE liquidity_rank <= 12").fetchall()
        assert sorted(story) == sorted(top)

    def test_each_story_moves_its_publication_session(self, database):
        oracle = database.execute((PACK / "eval" / "oracles" / "story_event_context.sql").read_text())
        columns = [column[0] for column in oracle.description]
        stories = [item for row in oracle.fetchall() if (item := dict(zip(columns, row, strict=True)))["is_story"]]
        assert len(stories) == 12
        for story in stories:
            assert abs(story["publication_session_return"]) > 0.03
            assert (story["publication_session_return"] > 0) == (story["sentiment_label"] == "positive")

    def test_prediction_views_see_nothing_after_the_anchor(self, database):
        for view, column in {"price_events": "observed_at", "news_events": "published_at"}.items():
            query = f"SELECT count(*) FROM prediction.{view} WHERE {column} > TIMESTAMPTZ '2026-08-24 21:00:00+00'"
            assert database.execute(query).fetchone() == (0,), view

    def test_sql_oracles_return_their_rows(self, database):
        counts = {
            path.name: database.execute(f"SELECT count(*) FROM ({path.read_text().strip().rstrip(';')})").fetchone()[0]
            for path in sorted((PACK / "eval" / "oracles").glob("*.sql"))
        }
        assert counts == ORACLE_ROWS
