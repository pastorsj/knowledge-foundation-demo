# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Every market tool's result stays short enough for the agent to read whole (budget.py).

Hermes hides an MCP result longer than 50,000 characters from the model behind a 1,500-character preview, so the
worst case of each tool is measured here as the model would read it.
"""

import json
from datetime import UTC
from datetime import date
from datetime import datetime
from typing import Any

import pytest
from mcp import Client
from pydantic import BaseModel

from market_analytics import budget
from market_analytics.data import Pack
from market_analytics.models import AnomalyObservation
from market_analytics.models import AssetNewsSummary
from market_analytics.models import AssetPriceSummary
from market_analytics.models import CentralAsset
from market_analytics.models import Engine
from market_analytics.models import IntradayScanPayload
from market_analytics.models import IntradaySession
from market_analytics.models import MarketAnomalyPayload
from market_analytics.models import MarketRelationshipsPayload
from market_analytics.models import MarketResult
from market_analytics.models import MarketScanPayload
from market_analytics.models import NewsPriceEvent
from market_analytics.models import NewsPriceRelationshipPayload
from market_analytics.models import PriceContextPayload
from market_analytics.models import PricePoint
from market_analytics.models import RankedAsset
from market_analytics.models import RelationshipEdge
from market_analytics.models import SentimentPoint
from market_analytics.models import SentimentReturnSummary
from market_analytics.models import SentimentTimelinePayload
from market_analytics.models import Timing
from market_analytics.server import create_server

pytestmark = pytest.mark.anyio

HERMES_MCP_LIMIT = 50_000  # Hermes v2026.9.24, tool_budget.mcp_result_size_chars
ROWS = 500  # more than any tool lists
VALUE = -0.12345678901234567  # as long as a float gets
VOLUME = 123_456_789_012.0
AT = datetime(2026, 8, 31, 21, tzinfo=UTC)
ASSET = "WIDE.TICKER.ID"


def asset(index: int) -> str:
    return f"{ASSET}{index:04d}"


def worst_payload(tool: str) -> BaseModel:
    """The tool's payload with far more rows than it may list, every value as long as it can be."""
    match tool:
        case "market_scan":
            metrics = {"return": VALUE, "volume": VOLUME, "volatility": VALUE, "peer_relative_return": VALUE}
            rows = [
                RankedAsset(
                    rank=i + 1,
                    asset_id=asset(i),
                    score=VALUE,
                    values=metrics,
                    zscores=dict.fromkeys(metrics, VALUE),
                    observation_count=260,
                    coverage_ratio=VALUE,
                )
                for i in range(ROWS)
            ]
            return MarketScanPayload(
                universe_id="all_assets",
                primary_metric="return",
                comparison="zscore",
                direction="highest",
                assets_ranked=2000,
                observations=rows,
            )
        case "market_anomaly_scan":
            features = [f"feature_{i}_with_a_long_name" for i in range(8)]
            rows = [
                AnomalyObservation(
                    rank=i + 1,
                    asset_id=asset(i),
                    timestamp=AT,
                    anomaly_score=VALUE,
                    decision_score=VALUE,
                    cohort_percentile=VALUE,
                    is_anomaly=True,
                    observed_deviations=dict.fromkeys(features, VALUE),
                )
                for i in range(ROWS)
            ]
            return MarketAnomalyPayload(
                universe_id="all_assets",
                feature_names=features,
                training_observations=1,
                scoring_observations=1,
                flagged_observations=ROWS,
                observations=rows,
            )
        case "price_context":
            summaries = [
                AssetPriceSummary(
                    asset_id=asset(i),
                    start_timestamp=AT,
                    end_timestamp=AT,
                    start_price=VALUE,
                    end_price=VALUE,
                    total_return=VALUE,
                    minimum_price=VALUE,
                    maximum_price=VALUE,
                    average_volume=VOLUME,
                    observation_count=260,
                )
                for i in range(50)  # the most assets one call names
            ]
            points = [
                PricePoint(asset_id=asset(i), timestamp=AT, adjusted_close=VALUE, volume=VOLUME) for i in range(2000)
            ]
            return PriceContextPayload(frequency="daily", summaries=summaries, series=points, series_truncated=False)
        case "sentiment_timeline":
            points = [
                SentimentPoint(
                    period_start=AT,
                    article_count=12345,
                    positive_count=1234,
                    neutral_count=1234,
                    negative_count=1234,
                    mean_sentiment=VALUE,
                )
                for _ in range(ROWS)
            ]
            return SentimentTimelinePayload(
                frequency="daily", articles_considered=1, points=points, points_truncated=False
            )
        case "analyze_news_price_relationship":
            events = [
                NewsPriceEvent(
                    news_id=f"{asset(i)}-20260828-12",
                    asset_id=asset(i),
                    published_at=AT,
                    sentiment_label="negative",
                    aligned_session=AT,
                    session_return=VALUE,
                    outcome_session=AT,
                    forward_return=VALUE,
                )
                for i in range(ROWS)
            ]
            assets = [
                AssetNewsSummary(
                    asset_id=asset(i),
                    article_count=12345,
                    positive_count=1234,
                    neutral_count=1234,
                    negative_count=1234,
                    aligned_event_count=1234,
                    mean_forward_return=VALUE,
                )
                for i in range(2000)
            ]
            summaries = [
                SentimentReturnSummary(
                    sentiment_label=label, event_count=1, mean_forward_return=VALUE, median_forward_return=VALUE
                )
                for label in ("positive", "neutral", "negative")
            ]
            return NewsPriceRelationshipPayload(
                return_horizon_sessions=20,
                eligible_event_count=ROWS,
                aligned_event_count=ROWS,
                coverage_ratio=1.0,
                sentiment_return_correlation=VALUE,
                summaries=summaries,
                asset_summaries=assets,
                events=events,
                events_truncated=False,
            )
        case "analyze_market_relationships":
            return MarketRelationshipsPayload(
                window_start=date(2025, 1, 2),
                window_end=date(2026, 3, 12),
                node_count=2000,
                edge_count=100_000,
                central_assets=[CentralAsset(rank=i + 1, asset_id=asset(i), centrality=VALUE) for i in range(50)],
                strongest_edges=[
                    RelationshipEdge(source_asset_id=asset(i), target_asset_id=asset(i + 1), correlation=VALUE)
                    for i in range(50)
                ],
            )
        case "intraday_scan":
            rows = [
                IntradaySession(
                    rank=i + 1,
                    asset_id=asset(i),
                    session=date(2026, 3, 12),
                    open=VALUE,
                    high=VALUE,
                    low=VALUE,
                    close=VALUE,
                    vwap=VALUE,
                    volume=VOLUME,
                    bar_count=391,
                    open_to_close_return=VALUE,
                    intraday_range=VALUE,
                    realized_volatility=VALUE,
                    max_drawdown=VALUE,
                    opening_volume_share=VALUE,
                    closing_volume_share=VALUE,
                )
                for i in range(ROWS)
            ]
            return IntradayScanPayload(
                rank_by="intraday_range",
                direction="highest",
                assets_scanned=2000,
                sessions_scanned=100_000,
                files_read=2000,
                batches=10,
                observations=rows,
            )
    raise AssertionError(tool)


def worst_result(tool: str) -> dict[str, Any]:
    payload = worst_payload(tool)
    return MarketResult[type(payload)](
        operation_id=tool,
        status="succeeded",
        source_id="market_data",
        database_name="market_fixture",
        payload=payload,
        engine=Engine(device="gpu", library="cudf.pandas", version="26.06.00", engine_id="cudf-gpu.v1"),
        timing=Timing(compute_ms=VALUE, setup_ms=VALUE, engine_ms=VALUE, total_ms=VALUE),
        rows_scanned=1_340_000,
        asset_count=2000,
        warnings=["A warning the tool itself added, as long as the longest one it adds today. " * 3],
        limitations=["Results are descriptive historical observations and are not investment advice."] * 3,
    ).model_dump(mode="json")


class WorstWorker:
    """Answers every call with the tool's worst-case result."""

    alive, pid = True, 4242

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return worst_result(tool)


ARGUMENTS = {
    "market_scan": {"universe_id": "all_assets", "end": "2026-06-30T00:00:00Z", "sessions": 20, "metrics": ["return"]},
    "market_anomaly_scan": {
        "universe_id": "all_assets",
        "training_start": "2026-06-01T00:00:00Z",
        "training_end": "2026-06-30T00:00:00Z",
        "scoring_start": "2026-07-01T00:00:00Z",
        "scoring_end": "2026-07-31T00:00:00Z",
    },
    "price_context": {"asset_ids": ["asset-alpha"], "start": "2026-06-01T00:00:00Z", "end": "2026-06-30T00:00:00Z"},
    "sentiment_timeline": {"start": "2026-06-01T00:00:00Z", "end": "2026-06-30T00:00:00Z"},
    "analyze_news_price_relationship": {
        "published_from": "2026-06-01T00:00:00Z",
        "published_to": "2026-06-30T00:00:00Z",
        "event_limit": 500,
    },
    "analyze_market_relationships": {"top_k": 50},
    "intraday_scan": {"start": "2026-06-01T00:00:00Z", "end": "2026-06-30T00:00:00Z", "universe_id": "all_assets"},
}


def as_hermes_reads_it(text: str) -> str:
    """Hermes puts the MCP text content under "result"; the execution-receipts plugin adds the evidence id first."""
    return json.dumps({"evidence_id": budget.EVIDENCE_ID, "result": text}, ensure_ascii=False)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize("tool", list(ARGUMENTS))
async def test_the_worst_case_of_every_tool_fits_the_budget(pack: Pack, tool: str) -> None:
    unbounded = len(as_hermes_reads_it(json.dumps(worst_result(tool), indent=2)))
    async with Client(create_server(pack, WorstWorker())) as client:
        result = await client.call_tool(tool, ARGUMENTS[tool])

    assert not result.is_error
    read = as_hermes_reads_it(result.content[0].text)
    assert len(read) <= budget.MAX_RESULT_CHARS < HERMES_MCP_LIMIT
    payload_type = type(worst_payload(tool))
    assert len(read) == budget.agent_chars(MarketResult[payload_type].model_validate(result.structured_content))
    content = result.structured_content
    for listing in budget.LISTINGS[tool]:
        rows = content["payload"][listing.field]
        assert rows, f"{tool} kept none of its {listing.field}"
        if listing.cap is not None:
            assert len(rows) <= listing.cap
        if "rank" in rows[0]:  # ranked rows keep the top of the ranking, in order
            assert [row["rank"] for row in rows] == list(range(1, len(rows) + 1))
    if unbounded > budget.MAX_RESULT_CHARS:
        assert len(content["warnings"]) > 1, "the rows left out are summarized in a warning"


async def test_left_out_rows_are_summarized(pack: Pack) -> None:
    async with Client(create_server(pack, WorstWorker())) as client:
        scan = (await client.call_tool("market_scan", ARGUMENTS["market_scan"])).structured_content
        news = await client.call_tool("analyze_news_price_relationship", ARGUMENTS["analyze_news_price_relationship"])
        prices = (await client.call_tool("price_context", ARGUMENTS["price_context"])).structured_content

    listed = len(scan["payload"]["observations"])
    assert 40 <= listed <= 50, "the cap, or fewer when four metrics with their z-scores do not fit"
    assert scan["warnings"][-1].startswith(f"Listed ranks 1-{listed} of the 500 assets the call asked for")
    assert f"ranks {listed + 1}-500 are left out, with score from -0.1235 to -0.1235" in scan["warnings"][-1]

    payload = news.structured_content["payload"]
    assert payload["events_truncated"] and len(payload["events"]) <= 50
    assert len(payload["asset_summaries"]) == 50
    assert any("asset_summaries and summaries count them" in w for w in news.structured_content["warnings"])

    assert len(prices["payload"]["summaries"]) == 50, "every named asset keeps its summary"
    assert prices["payload"]["series_truncated"] and 0 < len(prices["payload"]["series"]) < 2000


def test_a_short_result_is_unchanged() -> None:
    result = MarketResult[MarketScanPayload].model_validate(worst_result("market_scan"))
    result.payload.observations = result.payload.observations[:5]

    assert budget.fit(result) == result
