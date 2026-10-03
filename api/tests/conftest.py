# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures: a small active data pack, test settings, a fake Hermes and the running app.

Everything runs offline: outgoing HTTP goes to ``httpx.MockTransport`` handlers, and the app is
driven in-process through ``httpx.ASGITransport``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
from fastapi import FastAPI
from support import PACK
from support import RECEIPT_KEY
from support import REPO
from support import FakeHermes

from demo_api.app import create_app
from demo_api.registry import ToolRegistry
from demo_api.settings import Settings


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A built pack: pack.json, a DuckDB file with tables, keys and a view, and a retrieval index."""
    active = tmp_path / "active"
    (active / "structured").mkdir(parents=True)
    (active / "pack.json").write_text(json.dumps(PACK))
    manifest = {
        "collection": "aiq_market_intelligence_current",
        "physical_collection": "x__1",
        "source_ids": ["market_news"],
    }
    (active / "collection-manifest.json").write_text(json.dumps(manifest))
    with duckdb.connect(str(active / "structured" / "market_analysis.duckdb")) as connection:
        connection.execute("CREATE TABLE assets (asset_id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL)")
        connection.execute(
            "CREATE TABLE daily_prices (price_id VARCHAR PRIMARY KEY, "
            "asset_id VARCHAR NOT NULL REFERENCES assets(asset_id), close DOUBLE, traded_at TIMESTAMPTZ)"
        )
        connection.execute("INSERT INTO assets VALUES ('A1', 'Alpha'), ('B2', 'Beta')")
        connection.execute(
            "INSERT INTO daily_prices SELECT 'P' || i, CASE WHEN i % 2 = 0 THEN 'A1' ELSE 'B2' END, 100 + i, "
            "TIMESTAMPTZ '2026-08-01 21:00:00+00' + INTERVAL (i) DAY FROM range(150) t(i)"
        )
        connection.execute("CREATE SCHEMA prediction")
        connection.execute("CREATE VIEW prediction.asset_entities AS SELECT asset_id FROM main.assets")
    return active


@pytest.fixture
def settings(tmp_path: Path, data_dir: Path) -> Settings:
    return Settings(
        data_active_dir=data_dir,
        api_db_path=tmp_path / "api" / "jobs.db",
        agent_features="retrieval,tables",
        hermes_url="http://hermes.test",
        hermes_api_server_key="test-hermes-key",
        hermes_receipt_api_key=RECEIPT_KEY,
        hermes_run_poll_interval_seconds=0.01,
        hermes_receipt_settle_seconds=0.3,
        aiq_phoenix_internal_url="http://phoenix.test",
    )


@pytest.fixture
def tool_registry() -> ToolRegistry:
    return ToolRegistry.load(REPO / "contracts" / "tool-registry.json")


@pytest.fixture
def fake_hermes() -> FakeHermes:
    return FakeHermes()


@pytest.fixture
def upstreams(fake_hermes: FakeHermes) -> dict[str, Callable[[httpx.Request], Any]]:
    """Fake services the app calls, by host. Tests add ``phoenix.test`` or ``ontology.test`` handlers."""
    return {"hermes.test": fake_hermes.handle}


@pytest.fixture
async def app(settings: Settings, upstreams: dict[str, Callable[[httpx.Request], Any]]) -> AsyncIterator[FastAPI]:
    """The app with its lifespan running; its outgoing HTTP goes to ``upstreams``."""

    async def route(request: httpx.Request) -> httpx.Response:
        response = upstreams[request.url.host](request)
        return await response if asyncio.iscoroutine(response) else response

    app = create_app(settings, transport=httpx.MockTransport(route))
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def api(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test") as client:
        yield client


@pytest.fixture
def post_receipt(api: httpx.AsyncClient) -> Callable[..., Any]:
    async def post(receipt: dict[str, Any], *, key: str = RECEIPT_KEY) -> httpx.Response:
        return await api.post(
            f"/internal/hermes/jobs/{receipt['jobId']}/tool-receipts", json=receipt, headers={"X-Receipt-Key": key}
        )

    return post
