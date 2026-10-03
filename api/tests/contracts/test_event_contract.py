# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Behavior of the execution.v2 event contract."""

from datetime import UTC
from datetime import datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from demo_api.events import DisplaySafeProjection
from demo_api.events import EventProvenance
from demo_api.events import ExecutionEventV2

NOW = datetime(2026, 9, 12, 14, 30, tzinfo=UTC)


def _event(**updates) -> ExecutionEventV2:
    values = {
        "event_id": UUID("11111111-1111-4111-8111-111111111111"),
        "job_id": "job-1",
        "run_id": "run-1",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "event_kind": "tool.completed",
        "state": "completed",
        "component_id": "future_service.tool",
        "invocation_id": "invocation-2",
        "parent_invocation_id": "invocation-1",
        "tool_server": "future_service",
        "tool_name": "analyze",
        "capability_id": "novel_analysis",
        "artifact_refs": ("artifact:display-1",),
        "occurred_at": NOW,
        "display": DisplaySafeProjection(label="Analysis completed", attributes={"result_count": 4}),
        "provenance": EventProvenance(
            source_system="hermes.runs",
            source_event_id="hermes-event:27",
            source_event_kind="tool.completed",
            normalization_version="hermes-runs.v1",
        ),
    }
    values.update(updates)
    return ExecutionEventV2(**values)


def test_event_round_trips_through_the_event_store_envelope():
    stored = _event().to_event_store_dict()

    assert stored["type"] == "execution.v2"
    assert "cursor" not in stored
    assert stored["schemaVersion"] == "2"
    assert stored["parentInvocationId"] == "invocation-1"
    assert stored["provenance"]["sourceSystem"] == "hermes.runs"
    assert ExecutionEventV2.from_event_store_dict({**stored, "_id": 42}) == _event(cursor=42)


def test_event_rejects_a_cursor_that_disagrees_with_the_store_row():
    stored = _event().to_event_store_dict()
    with pytest.raises(ValueError, match="does not match"):
        ExecutionEventV2.from_event_store_dict({**stored, "_id": 42, "cursor": 41})


def test_event_accepts_an_unregistered_tool_without_a_schema_change():
    event = _event(tool_server="partner_product", tool_name="new_tool_v3", capability_id="future_capability.v3")
    assert event.tool_name == "new_tool_v3"


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"occurred_at": datetime(2026, 9, 12, 14, 30)}, "timezone"),
        ({"artifact_refs": ("artifact:1", "artifact:1")}, "duplicates"),
        ({"invocation_id": "same", "parent_invocation_id": "same"}, "own parent"),
        ({"invocation_id": None}, "requires invocation_id"),
    ],
)
def test_event_rejects_ambiguous_lineage_or_time(updates, message):
    with pytest.raises(ValidationError, match=message):
        _event(**updates)


@pytest.mark.parametrize("key", ["hidden_reasoning", "auth_token", "system_prompt", "prompt_tokens"])
def test_display_attributes_reject_private_fields(key):
    with pytest.raises(ValidationError, match="not permitted"):
        DisplaySafeProjection(label="Unsafe", attributes={key: "private"})


def test_llm_call_attributes_carry_the_served_model_and_tier():
    display = DisplaySafeProjection(
        label="Model call completed",
        attributes={"served_model": "gpt-6-sol", "tier": "capable", "output_tokens": 12},
    )

    assert display.model_dump(mode="json")["attributes"] == {
        "served_model": "gpt-6-sol",
        "tier": "capable",
        "output_tokens": 12,
    }
    with pytest.raises(ValidationError, match="tier"):
        DisplaySafeProjection(label="Model call completed", attributes={"tier": "judge"})
