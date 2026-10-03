# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Checking tables against a tool contract."""

from __future__ import annotations

import duckdb
import pytest
from conftest import PACKS

from demo_data.pack import contract_errors
from demo_data.pack import load_pack


@pytest.fixture
def connection(synthetic_pack):
    """The synthetic-market pack's declared tables, empty."""
    with duckdb.connect() as connection:
        connection.execute(synthetic_pack.path("schema.sql").read_text())
        yield connection


def relations(pack) -> dict[str, str]:
    return {table: f"main.{table}" for table in pack.tables}


def test_declared_tables_satisfy_the_contract(synthetic_pack, contract, connection):
    assert contract_errors(contract, synthetic_pack, connection, relations(synthetic_pack)) == []


def test_type_and_column_problems(synthetic_pack, contract, connection):
    connection.execute("ALTER TABLE company_news ALTER published_at TYPE VARCHAR")
    connection.execute("ALTER TABLE company_news DROP COLUMN source_name")

    assert contract_errors(contract, synthetic_pack, connection, relations(synthetic_pack)) == [
        "market-analytics/v1: company_news.published_at is VARCHAR, not timestamp",
        "market-analytics/v1: company_news.source_name is missing",
    ]


def test_row_rules_apply_once_tables_have_rows(synthetic_pack, contract):
    with duckdb.connect() as connection:
        connection.execute(
            """CREATE TABLE company_news AS SELECT * FROM (VALUES
               ('n1', 'A', TIMESTAMPTZ '2026-08-18 21:30:00+00', 'Wire', 'bullish'),
               ('n1', 'A', TIMESTAMPTZ '2026-08-19 21:30:00+00', 'Wire', 'neutral'))
               t(news_id, primary_asset_id, published_at, source_name, sentiment_label)"""
        )
        news_only = {"company_news": "main.company_news"}

        assert contract_errors(contract, synthetic_pack, connection, news_only) == [
            "market-analytics/v1: company_news.sentiment_label has 1 rows outside ['positive', 'neutral', 'negative']",
            "market-analytics/v1: company_news has 1 duplicate ('news_id',) keys",
        ]


def test_a_missing_table_and_the_contracts_own_type_names(synthetic_pack, connection):
    contract = {
        "id": "prices/v1",
        "logical_types": {"number": ["DOUBLE"]},
        "tables": {"daily_prices": {"columns": {"adjusted_close": "number"}}, "fx_rates": {"columns": {}}},
    }

    assert contract_errors(contract, synthetic_pack, connection, relations(synthetic_pack)) == [
        "prices/v1: table fx_rates is missing"
    ]


def test_a_table_without_rows_yet_is_only_checked_for_presence(synthetic_pack, contract, connection):
    without_news = {table: rel for table, rel in relations(synthetic_pack).items() if table != "company_news"}

    assert contract_errors(contract, synthetic_pack, connection, without_news) == []


def test_a_pack_without_a_news_table_skips_it(contract):
    pack = load_pack(PACKS / "us-equities")
    with duckdb.connect() as connection:
        connection.execute(pack.path("schema.sql").read_text())

        assert pack.manifest["analytics"]["news_table"] is None
        assert contract_errors(contract, pack, connection, relations(pack)) == []
