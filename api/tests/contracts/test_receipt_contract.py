# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Behavior of the ReceiptV2 union, starting from the golden receipts."""

import copy

import pytest
from pydantic import TypeAdapter
from pydantic import ValidationError

from demo_api.events.models import MAX_JSON_ITEMS
from demo_api.receipts import AnalyticsResultReceipt
from demo_api.receipts import ReceiptV2
from demo_api.receipts import RetrievalEvidenceReceipt
from demo_api.receipts import StructuredPredictionReceipt
from demo_api.receipts import StructuredQueryReceipt

RECEIPT = TypeAdapter(ReceiptV2)


@pytest.fixture
def golden(receipts):
    """A mutable copy of the first completed golden receipt of a kind."""

    def pick(kind: str) -> dict:
        return copy.deepcopy(next(r for r in receipts if r["artifactKind"] == kind and r["status"] == "completed"))

    return pick


@pytest.mark.parametrize(
    ("kind", "variant"),
    [
        ("retrieval_evidence", RetrievalEvidenceReceipt),
        ("analytics_result", AnalyticsResultReceipt),
        ("structured_query", StructuredQueryReceipt),
        ("structured_prediction", StructuredPredictionReceipt),
    ],
)
def test_artifact_kind_selects_the_variant(golden, kind, variant):
    assert isinstance(RECEIPT.validate_python(golden(kind)), variant)


def test_receipts_may_be_posted_with_snake_case_names(receipts):
    for receipt in receipts:
        parsed = RECEIPT.validate_python(receipt)
        snake_case = RECEIPT.dump_python(parsed, mode="json", by_alias=False)
        assert "artifact_kind" in snake_case
        assert RECEIPT.validate_python(snake_case) == parsed


def test_content_must_match_its_artifact_kind(golden):
    receipt = golden("structured_query")
    receipt["artifactKind"] = "retrieval_evidence"
    with pytest.raises(ValidationError):
        RECEIPT.validate_python(receipt)


def test_a_failed_call_may_have_no_content(golden):
    receipt = golden("structured_query") | {
        "status": "failed",
        "content": None,
        "errorType": "tool_timeout",
        "errorSummary": "Auto Ontology did not answer in time.",
    }
    assert RECEIPT.validate_python(receipt).content is None


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"content": None}, "requires content"),
        ({"errorSummary": "boom"}, "no error summary"),
        ({"spanId": None}, "provided together"),
        ({"occurredAt": "2026-09-28T05:00:21"}, "timezone"),
    ],
)
def test_a_completed_receipt_is_complete_and_well_formed(golden, updates, message):
    with pytest.raises(ValidationError, match=message):
        RECEIPT.validate_python(golden("retrieval_evidence") | updates)


def test_analytics_content_must_come_from_the_tool_that_ran_it(golden):
    receipt = golden("analytics_result")
    receipt["toolName"] = "mcp__market_analytics__price_context"
    with pytest.raises(ValidationError, match="tool that ran its operation"):
        RECEIPT.validate_python(receipt)


def test_a_failed_operation_is_never_a_completed_receipt(golden):
    receipt = golden("analytics_result")
    receipt["content"] |= {"status": "failed", "error": {"code": "execution_failed", "message": "GPU fell over"}}
    with pytest.raises(ValidationError, match="cannot be a completed receipt"):
        RECEIPT.validate_python(receipt)


def test_a_completed_prediction_has_scored_rows(golden):
    receipt = golden("structured_prediction")
    receipt["content"]["rows"] = []
    with pytest.raises(ValidationError, match="scored rows"):
        RECEIPT.validate_python(receipt)


def test_open_json_content_rejects_private_fields(golden):
    receipt = golden("structured_query")
    receipt["content"]["rows"] = [{"ticker": "NVDA", "api_key": "leaked"}]
    with pytest.raises(ValidationError, match="not permitted"):
        RECEIPT.validate_python(receipt)


def test_open_json_lists_hold_at_most_the_display_limit(golden):
    receipt = golden("analytics_result")
    receipt["content"]["payload"]["series"] = [0.5] * MAX_JSON_ITEMS
    RECEIPT.validate_python(receipt)
    receipt["content"]["payload"]["series"].append(0.5)
    with pytest.raises(ValidationError, match="exceeds 100 array items"):
        RECEIPT.validate_python(receipt)


@pytest.mark.parametrize(
    ("kind", "set_timestamp"),
    [
        ("retrieval_evidence", lambda content: content["hits"][0].update(publishedAt="2026-05-11T00:00:00")),
        ("structured_prediction", lambda content: content.update(anchor="2026-08-24")),
    ],
)
def test_content_timestamps_need_a_timezone(golden, kind, set_timestamp):
    receipt = golden(kind)
    set_timestamp(receipt["content"])
    with pytest.raises(ValidationError, match="timezone"):
        RECEIPT.validate_python(receipt)


def test_retrieval_hits_come_from_the_selected_sources(golden):
    receipt = golden("retrieval_evidence")
    receipt["content"]["hits"][0]["sourceId"] = "market_regulations"
    with pytest.raises(ValidationError, match="selected source"):
        RECEIPT.validate_python(receipt)


def test_retrieval_hits_cannot_outnumber_their_candidates(golden):
    receipt = golden("retrieval_evidence")
    receipt["content"]["candidateCounts"] = {"market_news": 1}
    with pytest.raises(ValidationError, match="outnumber"):
        RECEIPT.validate_python(receipt)


def test_only_an_operation_that_never_ran_may_omit_its_engine(golden):
    receipt = golden("analytics_result")
    receipt["content"]["engine"] = None
    with pytest.raises(ValidationError, match="name its engine"):
        RECEIPT.validate_python(receipt)

    receipt |= {"status": "failed", "errorType": "deadline_exceeded", "errorSummary": "The worker timed out."}
    receipt["content"] |= {
        "status": "failed",
        "payload": None,
        "error": {"code": "deadline_exceeded", "message": "The worker timed out."},
    }
    assert RECEIPT.validate_python(receipt).content.engine is None


def test_an_analytics_receipt_carries_what_its_receipt_card_shows(golden):
    content = RECEIPT.validate_python(golden("analytics_result")).content
    assert content.engine.engine_id == "cuml-pca-anomaly-gpu.v1"
    assert content.asset_count == 12
    assert (content.timing.setup_ms, content.timing.engine_ms) == (1.2584, 8.7481)
    assert content.payload["policy_id"] == "pca-reconstruction-market-v1"


def test_an_analytics_receipt_recorded_before_those_fields_still_validates(golden):
    """Recordings made before the engine id, asset count and setup time keep validating, with the fields null."""
    receipt = golden("analytics_result")
    content = receipt["content"]
    del content["engine"]["engineId"], content["assetCount"], content["timing"]["setupMs"]
    del content["timing"]["engineMs"], content["payload"]["policy_id"]
    parsed = RECEIPT.validate_python(receipt).content
    assert (parsed.engine.engine_id, parsed.asset_count, parsed.timing.setup_ms, parsed.timing.engine_ms) == (
        None,
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("engine", {"engineId": "not an id"}), ("timing", {"setupMs": -0.5}), ("assetCount", -1)],
)
def test_analytics_receipt_card_fields_are_bounded(golden, field, value):
    receipt = golden("analytics_result")
    content = receipt["content"]
    content[field] = content[field] | value if isinstance(value, dict) else value
    with pytest.raises(ValidationError):
        RECEIPT.validate_python(receipt)


def test_an_unavailable_prediction_gives_its_reason(golden):
    receipt = golden("structured_prediction")
    receipt["content"] |= {"available": False, "rows": []}
    with pytest.raises(ValidationError, match="reason is required"):
        RECEIPT.validate_python(receipt)
