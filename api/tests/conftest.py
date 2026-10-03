# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures: the fixture knowledge catalog, test settings, a fake Hermes and the running app.

Everything runs offline: outgoing HTTP goes to ``httpx.MockTransport`` handlers, and the app is
driven in-process through ``httpx.ASGITransport``.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
from fastapi import FastAPI
from support import CATALOG
from support import RECEIPT_KEY
from support import REPO
from support import FakeHermes

from demo_api.app import create_app
from demo_api.registry import ToolRegistry
from demo_api.settings import Settings


@pytest.fixture
def knowledge_dir(tmp_path: Path) -> Path:
    """The knowledge volume as ingest leaves it for the fixture catalog (contracts/fixtures/catalog).

    The retail pack (retail.policies documents, retail.sales tables) and the empty workspace. retail.sales's
    DuckDB is loaded from the fixture CSVs the way ingest loads files: tables without constraints, whose keys
    live in the source manifest.
    """
    root = tmp_path / "knowledge"
    for kind in ("packs", "sources"):
        shutil.copytree(CATALOG / kind, root / "catalog" / kind)
    database = root / "sources" / "retail.sales" / "tables.duckdb"
    database.parent.mkdir(parents=True)
    with duckdb.connect(str(database)) as connection:
        for table in ("customers", "orders"):
            csv = CATALOG / "tables" / f"{table}.csv"
            connection.execute(f"CREATE TABLE {table} AS SELECT * FROM read_csv('{csv}')")
    return root


@pytest.fixture
def features() -> str:
    """AGENT_FEATURES; a test parametrizes ``features`` to run with other tool groups."""
    return "retrieval,tables"


@pytest.fixture
def settings(tmp_path: Path, knowledge_dir: Path, features: str) -> Settings:
    return Settings(
        knowledge_dir=knowledge_dir,
        ingest_url="http://ingest.test",
        api_db_path=tmp_path / "api" / "jobs.db",
        agent_features=features,
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
    """Fake services the app calls, by host. Tests add ``phoenix.test``, ``ontology.test`` or ``ingest.test``."""
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
