# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from mcp.client import Client

from demo_prediction.server import create_server
from demo_prediction.settings import Settings

pytestmark = pytest.mark.anyio


async def test_tool_schema(knowledge_dir: Path):
    async with Client(create_server(Settings(knowledge_dir=knowledge_dir))) as client:
        (tool,) = (await client.list_tools()).tools

    assert tool.name == "predict"
    assert tool.annotations.read_only_hint
    assert tool.input_schema["properties"].keys() == {"question", "pql", "source_ids", "anchor_time"}
    assert set(tool.input_schema["required"]) == {"question", "pql", "source_ids"}
    assert {
        "available",
        "reason",
        "source_id",
        "template_id",
        "pql",
        "task_type",
        "anchor_time",
        "horizon",
        "entity_table",
        "rows",
        "model",
        "elapsed_ms",
        "warnings",
    } == tool.output_schema["properties"].keys()
    assert "template:<id>" in tool.description and "FOR EACH" in tool.description


@pytest.mark.parametrize(("url", "configured"), [("http://kumo-relational:8000", True), (None, False)])
async def test_health(knowledge_dir: Path, url: str | None, configured: bool):
    server = create_server(Settings(knowledge_dir=knowledge_dir, kumo_url=url))
    transport = httpx.ASGITransport(app=server.streamable_http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://prediction") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "kumo_configured": configured}


def test_settings_defaults():
    settings = Settings.from_env({"KUMO_RELATIONAL_URL": "", "KUMO_API_KEY": ""})

    assert (settings.knowledge_dir, settings.port) == (Path("/knowledge"), 8322)
    assert (settings.kumo_url, settings.kumo_api_key) == (None, None)
    assert "secret" not in repr(Settings(kumo_api_key="secret"))


def test_settings_from_the_environment():
    settings = Settings.from_env(
        {
            "KNOWLEDGE_DIR": "/srv/kb",
            "PREDICTION_PORT": "9002",
            "KUMO_RELATIONAL_URL": "https://kumo.example.com",
            "KUMO_API_KEY": "key",
        }
    )

    assert (settings.knowledge_dir, settings.port) == (Path("/srv/kb"), 9002)
    assert (settings.kumo_url, settings.kumo_api_key) == ("https://kumo.example.com", "key")
