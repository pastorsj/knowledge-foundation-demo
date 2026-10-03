# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Behavior of the ReceiptV2 union, starting from the golden receipts."""

import copy

import pytest
from pydantic import TypeAdapter
from pydantic import ValidationError

from demo_api.events.models import MAX_JSON_ITEMS
from demo_api.receipts import ReceiptV2
from demo_api.receipts import RetrievalEvidenceReceipt
from demo_api.receipts import StructuredPredictionReceipt
from demo_api.receipts import StructuredQueryReceipt

RECEIPT = TypeAdapter(ReceiptV2)


@pytest.fixture
def golden(receipts):
    """A mutable copy of the first completed golden receipt of a kind (and, optionally, tool)."""

    def pick(kind: str, tool: str | None = None) -> dict:
        return copy.deepcopy(
            next(
                r
                for r in receipts
                if r["artifactKind"] == kind and r["status"] == "completed" and tool in (None, r["toolName"])
            )
        )

    return pick


@pytest.mark.parametrize(
    ("kind", "variant"),
    [
        ("retrieval_evidence", RetrievalEvidenceReceipt),
        ("structured_query", StructuredQueryReceipt),
        ("structured_prediction", StructuredPredictionReceipt),
    ],
)
def test_artifact_kind_selects_the_variant(golden, kind, variant):
    assert isinstance(RECEIPT.validate_python(golden(kind)), variant)


def test_market_analytics_receipts_are_no_longer_accepted(golden):
    receipt = golden("structured_query") | {"artifactKind": "analytics_result"}
    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        RECEIPT.validate_python(receipt)


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
        "errorSummary": "The query ran past its 10 second limit.",
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


def test_a_table_query_and_an_ontology_answer_share_the_structured_query_shape(golden):
    tables = RECEIPT.validate_python(golden("structured_query", "mcp__tables__query_tables")).content
    ontology = RECEIPT.validate_python(golden("structured_query", "mcp__auto_ontology__ask_question")).content

    assert (tables.database_name, tables.answer, tables.resolution_lineage) == ("retail_sales", None, ())
    assert tables.sql.startswith("SELECT") and tables.source_row_count == len(tables.rows)
    assert ontology.answer and ontology.resolution_lineage


def test_a_retrieval_without_a_rerank_model_keeps_vector_order(receipts):
    unranked = [
        r for r in receipts if r["artifactKind"] == "retrieval_evidence" and not r["content"]["models"]["rerank"]
    ]
    assert unranked, "the golden receipts include a retrieval without a rerank model"
    content = RECEIPT.validate_python(unranked[0]).content

    assert content.models.rerank is None and content.timings.rerank_ms == 0
    assert all(hit.score == hit.vector_score for hit in content.hits)


def test_a_retrieval_names_its_rerank_model_or_null_never_empty(golden):
    receipt = golden("retrieval_evidence")
    receipt["content"]["models"]["rerank"] = ""
    with pytest.raises(ValidationError, match="at least 1 character"):
        RECEIPT.validate_python(receipt)
    del receipt["content"]["models"]["rerank"]
    with pytest.raises(ValidationError, match="Field required"):
        RECEIPT.validate_python(receipt)


def test_a_completed_prediction_has_scored_rows(golden):
    receipt = golden("structured_prediction")
    receipt["content"]["rows"] = []
    with pytest.raises(ValidationError, match="scored rows"):
        RECEIPT.validate_python(receipt)


def test_a_prediction_scores_entities_of_any_source(golden):
    content = RECEIPT.validate_python(golden("structured_prediction")).content

    assert (content.source_id, content.template_id, content.entity_table) == ("retail.sales", "churn_90d", "customers")
    assert content.task_type == "binary_classification"
    assert content.anchor_time.isoformat() == "2026-09-30T00:00:00+00:00"
    assert [(row.entity_id, row.value, row.label) for row in content.rows] == [
        ("C2", None, None),
        ("C1", None, None),
        ("C3", None, None),
    ]


def test_a_prediction_row_may_carry_a_value_or_a_label(golden):
    receipt = golden("structured_prediction")
    receipt["content"] |= {"taskType": "regression", "templateId": None, "anchorTime": None, "horizon": None}
    receipt["content"]["rows"] = [
        {"entityId": "C1", "probability": None, "value": 212.5, "label": None},
        {"entityId": "C2", "probability": 0.62, "value": None, "label": "silver"},
    ]
    rows = RECEIPT.validate_python(receipt).content.rows
    assert [(row.value, row.label) for row in rows] == [(212.5, None), (None, "silver")]


@pytest.mark.parametrize("probability", [-0.01, 1.01])
def test_a_probability_is_between_zero_and_one(golden, probability):
    receipt = golden("structured_prediction")
    receipt["content"]["rows"][0]["probability"] = probability
    with pytest.raises(ValidationError):
        RECEIPT.validate_python(receipt)


def test_an_unavailable_prediction_gives_its_reason(golden):
    receipt = golden("structured_prediction")
    receipt["content"] |= {"available": False, "rows": []}
    with pytest.raises(ValidationError, match="reason is required"):
        RECEIPT.validate_python(receipt)


def test_an_unavailable_prediction_has_no_rows(receipts):
    receipt = copy.deepcopy(
        next(r for r in receipts if r["artifactKind"] == "structured_prediction" and r["status"] == "failed")
    )
    assert RECEIPT.validate_python(receipt).content.reason

    receipt["content"]["rows"] = [{"entityId": "C1", "probability": 0.5, "value": None, "label": None}]
    with pytest.raises(ValidationError, match="no rows"):
        RECEIPT.validate_python(receipt)


def test_open_json_content_rejects_private_fields(golden):
    receipt = golden("structured_query")
    receipt["content"]["rows"] = [{"tier": "gold", "api_key": "leaked"}]
    with pytest.raises(ValidationError, match="not permitted"):
        RECEIPT.validate_python(receipt)


def test_open_json_lists_hold_at_most_the_display_limit(golden):
    receipt = golden("retrieval_evidence")
    receipt["content"]["hits"][0]["metadata"]["headings"] = ["Returns"] * MAX_JSON_ITEMS
    RECEIPT.validate_python(receipt)
    receipt["content"]["hits"][0]["metadata"]["headings"].append("Returns")
    with pytest.raises(ValidationError, match="exceeds 100 array items"):
        RECEIPT.validate_python(receipt)


@pytest.mark.parametrize(
    ("kind", "set_timestamp"),
    [
        ("retrieval_evidence", lambda content: content["hits"][0].update(publishedAt="2026-05-11T00:00:00")),
        ("structured_prediction", lambda content: content.update(anchorTime="2026-09-30T00:00:00")),
    ],
)
def test_content_timestamps_need_a_timezone(golden, kind, set_timestamp):
    receipt = golden(kind)
    set_timestamp(receipt["content"])
    with pytest.raises(ValidationError, match="timezone"):
        RECEIPT.validate_python(receipt)


def test_retrieval_hits_come_from_the_selected_sources(golden):
    receipt = golden("retrieval_evidence")
    receipt["content"]["hits"][0]["sourceId"] = "retail.handbooks"
    with pytest.raises(ValidationError, match="selected source"):
        RECEIPT.validate_python(receipt)


def test_retrieval_hits_cannot_outnumber_their_candidates(golden):
    receipt = golden("retrieval_evidence")
    receipt["content"]["candidateCounts"] = {"retail.policies": 1}
    with pytest.raises(ValidationError, match="outnumber"):
        RECEIPT.validate_python(receipt)
