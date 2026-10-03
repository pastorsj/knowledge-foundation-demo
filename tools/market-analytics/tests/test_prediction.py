# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""predict_asset_outcomes around a stubbed Kumo client, plus one live call when an endpoint is configured."""

import os
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fixture_pack import TEMPLATES
from mcp import Client
from mcp.server.mcpserver import MCPServer

from market_analytics import prediction
from market_analytics.data import Pack
from market_analytics.prediction import Horizon
from market_analytics.prediction import Predictor
from market_analytics.prediction import horizon
from market_analytics.prediction import register

pytestmark = pytest.mark.anyio


class StubClient:
    """Records what predict_asset_outcomes sends and answers like RelationalClient for a binary query."""

    calls: list[dict[str, Any]] = []
    error: Exception | None = None

    def __init__(self, url: str, api_key: str | None = None, **options: Any) -> None:
        self.call = {"url": url, "api_key": api_key, **options}

    def __enter__(self) -> "StubClient":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def relational(self, graph: Any) -> "StubClient":
        self.call["graph"] = graph
        return self

    def predict(self, query: str, indices: list[str], **options: Any) -> pd.DataFrame:
        self.calls.append({**self.call, "query": query, "indices": indices, **options})
        if self.error is not None:
            raise self.error
        probabilities = [0.25 + 0.25 * position for position in range(len(indices))]
        return pd.DataFrame(
            {"ENTITY": indices, "PREDICTION": [p > 0.5 for p in probabilities], "TRUE_PROB": probabilities}
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> type[StubClient]:
    monkeypatch.setattr(StubClient, "calls", [])
    monkeypatch.setattr(StubClient, "error", None)
    monkeypatch.setattr(prediction, "RelationalClient", StubClient)
    return StubClient


def server_with_prediction(pack: Pack) -> MCPServer:
    server = MCPServer("market_analytics")
    register(server, pack, "http://kumo-relational:8000", api_key="dummy-key")
    return server


def test_horizon_is_read_from_the_pql_window() -> None:
    assert horizon(TEMPLATES[0]["pql"]) == Horizon(value=5, unit="days")
    assert horizon("PREDICT SUM(orders.price, -2, 30, Days) FOR users.user_id=1") == Horizon(value=32, unit="days")
    with pytest.raises(ValueError, match="no aggregation window"):
        horizon("PREDICT users.age FOR users.user_id=1")


async def test_the_tool_offers_only_the_pack_templates(pack: Pack) -> None:
    async with Client(server_with_prediction(pack)) as client:
        tool = next(tool for tool in (await client.list_tools()).tools if tool.name == "predict_asset_outcomes")

    assert tool.annotations.read_only_hint
    assert tool.input_schema["properties"]["template_id"]["enum"] == ["positive_return", "any_price"]
    assert set(tool.input_schema["properties"]) == {"template_id", "asset_ids", "source_ids"}
    assert tool.input_schema["required"] == ["template_id"]


async def test_prediction_maps_kumo_rows_to_asset_probabilities(pack: Pack, stub: type[StubClient]) -> None:
    async with Client(server_with_prediction(pack)) as client:
        result = await client.call_tool("predict_asset_outcomes", {"template_id": "positive_return"})

    assert result.structured_content == {
        "available": True,
        "reason": None,
        "template_id": "positive_return",
        "pql": TEMPLATES[0]["pql"],
        "anchor": pack.prediction["anchor"],
        "horizon": {"value": 5, "unit": "days"},
        "rows": [
            {"asset_id": "asset-alpha", "probability": 0.25},
            {"asset_id": "asset-beta", "probability": 0.5},
            {"asset_id": "asset-gamma", "probability": 0.75},
        ],
        "model": "kumo-relational",
    }
    (call,) = stub.calls
    assert (call["url"], call["api_key"]) == ("http://kumo-relational:8000", "dummy-key")
    assert (call["max_retries"], call["num_retries"]) == (0, 0)  # one attempt, so the call ends within its timeout
    assert call["query"] == TEMPLATES[0]["pql"]
    assert call["anchor_time"] == pd.Timestamp(pack.prediction["anchor"])
    assert call["run_mode"] == "fast"


def test_the_graph_has_the_pack_keys_time_columns_and_links(pack: Pack) -> None:
    graph = Predictor(pack, "http://kumo-relational:8000").graph()

    assert graph["price_events"].primary_key.name == "price_id"
    assert graph["return_outcomes"].time_column.name == "realized_at"
    assert sorted((edge.src_table, edge.fkey, edge.dst_table) for edge in graph.edges) == [
        ("price_events", "asset_id", "asset_entities"),
        ("return_outcomes", "asset_id", "asset_entities"),
    ]
    graph.validate()


async def test_an_unreachable_endpoint_makes_the_prediction_unavailable(pack: Pack, stub: type[StubClient]) -> None:
    stub.error = ConnectionError("connection refused")
    async with Client(server_with_prediction(pack)) as client:
        result = await client.call_tool(
            "predict_asset_outcomes", {"template_id": "any_price", "asset_ids": ["asset-beta"]}
        )

    content = result.structured_content
    assert (content["available"], content["reason"], content["rows"]) == (
        False,
        "ConnectionError: connection refused",
        [],
    )
    assert content["horizon"] == {"value": 7, "unit": "days"}
    assert stub.calls[0]["indices"] == ["asset-beta"]


async def test_assets_outside_the_population_and_unselected_sources_are_refused(
    pack: Pack, stub: type[StubClient]
) -> None:
    async with Client(server_with_prediction(pack)) as client:
        outside = await client.call_tool(
            "predict_asset_outcomes", {"template_id": "any_price", "asset_ids": ["asset-omega"]}
        )
        unselected = await client.call_tool(
            "predict_asset_outcomes", {"template_id": "any_price", "source_ids": ["market_news"]}
        )

    assert outside.is_error
    assert "no predictions for ['asset-omega']" in outside.content[0].text
    assert unselected.structured_content["available"] is False
    assert stub.calls == []


@pytest.mark.live
def test_live_prediction() -> None:
    """KUMO_RELATIONAL_URL (and KUMO_API_KEY for a hosted gateway) plus DATA_ACTIVE_DIR with a real pack."""
    if not (os.environ.get("KUMO_RELATIONAL_URL") and os.environ.get("DATA_ACTIVE_DIR")):
        pytest.skip("set KUMO_RELATIONAL_URL and DATA_ACTIVE_DIR")
    pack = Pack.load(Path(os.environ["DATA_ACTIVE_DIR"]))
    predictor = Predictor(pack, os.environ["KUMO_RELATIONAL_URL"], os.environ.get("KUMO_API_KEY"))
    template_id = next(iter(predictor.templates))

    result = predictor.predict(template_id, predictor.population[:2])

    assert result.available, result.reason
    assert [row.asset_id for row in result.rows] == predictor.population[:2]
    assert all(0 <= row.probability <= 1 for row in result.rows)
