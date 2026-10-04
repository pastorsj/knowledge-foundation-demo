# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The agent plugin's routes: execution scope and tool receipts."""

from __future__ import annotations

import asyncio

import pytest
from support import RECEIPT_KEY
from support import receipt_for

from demo_api.jobs.store import JobStatus


async def start_job(api, app, job_id: str = "job-1", sources: list[str] | None = None) -> None:
    body = {"input": "q", "job_id": job_id, "data_sources": sources or ["retail.policies", "retail.sales"]}
    assert (await api.post("/v1/jobs/async/submit", json=body)).status_code == 200
    async with asyncio.timeout(5):
        while (await app.state.services.store.get(job_id)).hermes_run_id is None:
            await asyncio.sleep(0.01)


def scope(api, job_id: str = "job-1", key: str = RECEIPT_KEY):
    return api.get(f"/internal/hermes/jobs/{job_id}/execution-scope", headers={"X-Receipt-Key": key})


async def test_execution_scope_names_the_jobs_sources_database_and_collection(settings, api, app, fake_hermes):
    settings.agent_efficient_model, settings.agent_capable_model = "nvidia/efficient", "openai/capable"
    await start_job(api, app)

    assert (await scope(api)).json() == {
        "job_id": "job-1",
        "source_ids": ["retail.policies", "retail.sales"],
        "sources": [
            {"id": "retail.policies", "capabilities": ["unstructured_retrieval"]},
            {"id": "retail.sales", "capabilities": ["structured_retrieval"]},
        ],
        "database_name": "retail_sales",
        "collection": "knowledge",
        "ontology_source_id": None,  # no ontology feature
        "ontology_database_name": None,
        "models": {"efficient": "nvidia/efficient", "capable": "openai/capable"},
    }


@pytest.mark.parametrize("features", ["retrieval,tables,ontology"])
async def test_execution_scope_names_the_source_auto_ontology_answers_from(api, app, fake_hermes):
    await start_job(api, app, sources=["retail.policies", "retail.sales"])  # retail.sales: AUTO_ONTOLOGY_SOURCE

    body = (await scope(api)).json()
    assert (body["ontology_source_id"], body["ontology_database_name"]) == ("retail.sales", "retail_sales")


async def test_execution_scope_omits_what_the_job_did_not_select(api, app, fake_hermes):
    await start_job(api, app, sources=["retail.sales"])

    body = (await scope(api)).json()
    assert (body["source_ids"], body["database_name"], body["collection"]) == (["retail.sales"], "retail_sales", None)
    body = {"input": "q", "job_id": "job-2", "data_sources": ["retail.policies"]}
    assert (await api.post("/v1/jobs/async/submit", json=body)).status_code == 200  # queued behind job-1

    body = (await scope(api, "job-2")).json()
    assert (body["source_ids"], body["database_name"], body["collection"]) == (["retail.policies"], None, "knowledge")


@pytest.mark.parametrize("key", ["", "wrong-key"])
async def test_internal_routes_require_the_receipt_key(api, post_receipt, key):
    assert (await scope(api, key=key)).status_code == 401
    assert (await post_receipt(receipt_for("job-1"), key=key)).status_code == 401


async def test_receipts_for_unknown_or_finished_jobs_are_refused(api, app, fake_hermes, post_receipt):
    assert (await post_receipt(receipt_for("missing"))).status_code == 404
    await start_job(api, app)
    await api.post("/v1/jobs/async/job/job-1/cancel")

    assert (await post_receipt(receipt_for("job-1"))).status_code == 409
    assert (await scope(api)).status_code == 409


async def test_a_receipt_before_the_run_is_recorded_asks_the_plugin_to_retry(api, app, post_receipt):
    await app.state.services.store.create("job-1", {"question": "q"})

    response = await post_receipt(receipt_for("job-1"))
    assert (response.status_code, response.headers["Retry-After"]) == (503, "1")


async def test_invalid_receipts_are_refused(api, app, fake_hermes, post_receipt):
    await start_job(api, app)
    wrong_job = receipt_for("job-1") | {"jobId": "job-2"}
    wrong_kind = receipt_for("job-1") | {"toolName": "mcp__tables__query_tables"}
    retired = receipt_for("job-1") | {"toolName": "mcp__market_analytics__market_scan"}
    unregistered = receipt_for("job-1") | {"toolName": "mcp__retrieval__other_tool"}
    completed_without_content = receipt_for("job-1") | {"content": None}

    response = await api.post(
        "/internal/hermes/jobs/job-1/tool-receipts", json=wrong_job, headers={"X-Receipt-Key": RECEIPT_KEY}
    )
    assert response.status_code == 409
    for receipt in (wrong_kind, retired, unregistered, completed_without_content):
        assert (await post_receipt(receipt)).status_code == 422
    assert await app.state.services.store.receipts("job-1") == []


async def test_a_failed_receipt_is_a_tool_observation(api, app, fake_hermes, post_receipt):
    await start_job(api, app)
    receipt = receipt_for("job-1", kind="structured_prediction") | {
        "status": "failed",
        "errorType": "evidence_unavailable",
        "errorSummary": "Kumo is not running",
    }

    assert (await post_receipt(receipt)).status_code == 200
    events = await app.state.services.store.events("job-1")
    observed = events[-1]
    assert (observed["eventKind"], observed["state"]) == ("tool.observed", "failed")
    assert observed["display"]["summary"] == "Kumo is not running"
    assert observed["componentId"] == "nvidia.kumo"
    assert (await app.state.services.store.get("job-1")).status == JobStatus.RUNNING


async def test_a_table_query_receipt_is_structured_evidence(api, app, fake_hermes, post_receipt):
    await start_job(api, app)
    receipt = receipt_for("job-1", kind="structured_query")
    assert receipt["toolName"] == "mcp__tables__query_tables"

    assert (await post_receipt(receipt)).status_code == 200
    available = (await app.state.services.store.events("job-1"))[-1]
    assert (available["eventKind"], available["toolServer"], available["toolName"]) == (
        "artifact.available",
        "tables",
        "query_tables",
    )
    assert (available["capabilityId"], available["componentId"]) == ("structured_retrieval", "duckdb.tables")
    assert available["display"]["label"] == "Structured query result available"


async def test_a_model_call_becomes_an_llm_call_event_with_its_tier(api, app, fake_hermes):
    await start_job(api, app)
    call = {
        "api_request_id": "req-1",
        "turn_id": None,
        "served_model": "gpt-6-sol",
        "tier": "capable",
        "input_tokens": 41870,
        "output_tokens": 2796,
        "started_at": "2026-09-28T05:00:10Z",
        "completed_at": "2026-09-28T05:00:12Z",
    }

    response = await api.post(
        "/internal/hermes/jobs/job-1/llm-calls", json=call, headers={"X-Receipt-Key": RECEIPT_KEY}
    )
    assert response.json() == {"invocation_id": "hermes-llm:req-1"}
    llm = (await app.state.services.store.events("job-1"))[-1]
    assert (llm["eventKind"], llm["componentId"], llm["runId"]) == ("llm.call", "switchyard.router", "run-1")
    assert llm["display"]["attributes"] == {
        "served_model": "gpt-6-sol",
        "tier": "capable",
        "input_tokens": 41870,
        "output_tokens": 2796,
    }
    bad = call | {"tier": "premium"}
    headers = {"X-Receipt-Key": RECEIPT_KEY}
    assert (await api.post("/internal/hermes/jobs/job-1/llm-calls", json=bad, headers=headers)).status_code == 422


def test_jobs_run_one_at_a_time_as_the_plugin_keeps_one_scope():
    from pydantic import ValidationError

    from demo_api.settings import Settings

    with pytest.raises(ValidationError, match="job_max_active"):
        Settings(job_max_active=2)
