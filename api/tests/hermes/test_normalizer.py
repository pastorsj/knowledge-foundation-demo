# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Hermes Runs events become execution.v2 events labelled from the tool registry."""

from __future__ import annotations

from demo_api.hermes.client import RunEvent
from demo_api.hermes.normalizer import EventNormalizer
from demo_api.hermes.normalizer import tool_invocation_id
from demo_api.hermes.request import PriorTurn
from demo_api.hermes.request import build_run_request

QUERY = "mcp__tables__query_tables"


def run_events(*events: tuple[str, dict]) -> list[RunEvent]:
    return [
        RunEvent(name, "run-1", {"event": name, **data}, 1_790_000_000.0 + n) for n, (name, data) in enumerate(events)
    ]


def normalize(tool_registry, *events: tuple[str, dict]):
    normalizer = EventNormalizer(job_id="job-1", session_id="conv-1", registry=tool_registry)
    return normalizer, [normalizer.normalize(event) for event in run_events(*events)]


def test_registered_tool_calls_carry_registry_identity_and_join_their_receipts(tool_registry):
    normalizer, (started, completed) = normalize(
        tool_registry,
        ("tool.started", {"tool": QUERY, "tool_call_id": "call_9"}),
        ("tool.completed", {"tool": QUERY, "tool_call_id": "call_9", "duration": 0.25, "error": False}),
    )

    assert started.invocation_id == completed.invocation_id == "hermes-tool:call_9" == tool_invocation_id("call_9")
    assert (completed.tool_name, completed.tool_server, completed.capability_id) == (
        "query_tables",
        "tables",
        "structured_retrieval",
    )
    assert completed.component_id == "duckdb.tables"
    assert completed.display.label == "Table Query completed"
    assert completed.display.attributes == {"reported_error": False, "duration_seconds": 0.25}
    assert completed.parent_invocation_id == started.parent_invocation_id
    assert normalizer.completed_registered_calls == {"hermes-tool:call_9"}


def test_tool_calls_without_ids_pair_first_in_first_out(tool_registry):
    _, events = normalize(
        tool_registry,
        ("tool.started", {"tool": "skill_view"}),
        ("tool.started", {"tool": "skill_view"}),
        ("tool.completed", {"tool": "skill_view", "error": True}),
    )

    assert events[2].invocation_id == events[0].invocation_id != events[1].invocation_id
    assert (events[2].state, events[2].component_id, events[2].display.inspectable) == ("failed", "hermes.tool", False)


def test_model_text_is_never_stored(tool_registry):
    normalizer, events = normalize(
        tool_registry,
        ("message.delta", {"delta": "secret plans"}),
        ("reasoning.available", {"text": "chain of thought"}),
        ("run.completed", {"output": "final", "usage": {"input_tokens": 3, "output_tokens": 4}}),
    )

    assert events[0] is None
    assert events[1].display.label == "Reasoning in progress"
    assert "chain of thought" not in events[1].model_dump_json()
    assert events[2].display.attributes == {"input_tokens": 3, "output_tokens": 4, "output_characters": 5}
    assert normalizer.terminal_seen


def test_unknown_events_are_kept_generically(tool_registry):
    _, (event,) = normalize(tool_registry, ("subagent.complete", {"status": "ok"}))

    assert (event.event_kind, event.state, event.component_id) == ("subagent.complete", "completed", "hermes.runtime")
    assert event.provenance.source_system == "hermes.runs"


def test_run_request_follows_the_agent_run_contract():
    catalog = [{"id": "market_news", "name": "SEC Filings", "capabilities": ["unstructured_retrieval"]}]
    request = build_run_request(
        job_id="job-1",
        session_id="conv-1",
        question="What changed?",
        catalog=catalog,
        toolsets=["skills", "retrieval"],
        prior_turns=[PriorTurn("Earlier?", "Yes [1].\n\n## Sources\n\n- [1] evidence")],
    )

    assert (request["model"], request["session_id"], request["enabled_toolsets"]) == (
        "enterprise-research",
        "job-1",
        ["skills", "retrieval"],
    )
    assert set(request["metadata"]) == {"aiq.job.ref", "aiq.session.ref", "aiq.execution.mode", "aiq.agent.type"}
    assert request["conversation_history"] == [
        {"role": "user", "content": "Earlier?"},
        {"role": "assistant", "content": "Yes [1]."},
    ]
    assert '"id":"market_news"' in request["instructions"]
