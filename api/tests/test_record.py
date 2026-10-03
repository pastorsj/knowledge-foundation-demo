# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""``demo-api record --pack <id>`` writes that pack's v2 recordings bundle, which the UI replays."""

from __future__ import annotations

import json

import httpx
import pytest

from demo_api.cli import main
from demo_api.cli import public_model_ids
from demo_api.cli import record

PACK_VIEW = {
    "id": "retail",
    "kind": "industry",
    "title": "Retail",
    "description": "A fictional omnichannel retailer.",
    "icon": "Store",
    "status": "ready",
    "version": "1.0.0",
    "as_of": "2026-09-30",
    "disclaimer": "Synthetic data for a software demonstration.",
    "questions": [
        {
            "id": "top-stores",
            "label": "Top stores by revenue",
            "tag": "SQL",
            "description": None,
            "question": "Which five stores had the highest net revenue in the last full quarter?",
            "sources": ["retail.sales"],
            "tools": ["duckdb"],
            "featured": True,
        },
        {
            "id": "electronics-returns",
            "label": "Electronics return window",
            "tag": "DOCUMENTS",
            "description": None,
            "question": "How long do customers have to return electronics?",
            "sources": ["retail.policies"],
            "tools": ["retrieval"],
            "featured": False,
        },
    ],
    "examples": ["top-stores", "electronics-returns"],
    "conversations": [
        {
            "id": "returns-follow-up",
            "label": "Returns follow-up",
            "tag": None,
            "description": None,
            "sources": ["retail.policies"],
            "turns": ["What is the return window for electronics?", "And for furniture?"],
        }
    ],
}
QUESTIONS = [question["question"] for question in PACK_VIEW["questions"]]
TURNS = PACK_VIEW["conversations"][0]["turns"]
SOURCES = [
    {"id": "retail.policies", "pack_id": "retail", "name": "Policies", "kind": "documents", "database_name": None},
    {"id": "retail.sales", "pack_id": "retail", "name": "Sales", "kind": "structured", "database_name": "retail_sales"},
]

SQL = "SELECT store_id, sum(net_amount) FROM retail_sales.orders GROUP BY 1"
SQL_RECEIPT = {"artifactKind": "structured_query", "content": {"databaseName": "retail_sales", "sql": SQL}}
# A query over two attached sources names no single database; the data viewer of one source cannot rerun it
MULTI_SOURCE_RECEIPT = {"artifactKind": "structured_query", "content": {"databaseName": "knowledge", "sql": "SELECT 2"}}
SCHEMA = {
    "source_id": "retail.sales",
    "database_name": "retail_sales",
    "tables": [
        {"name": "customers", "schema": "main", "kind": "table", "columns": []},
        {"name": "orders", "schema": "main", "kind": "table", "columns": []},
    ],
    "relationships": [],
}
# A model call served through a gateway that prefixes model ids with their provider
GATEWAY_LLM_CALL = {
    "eventKind": "llm.call",
    "display": {"attributes": {"served_model": "vertex/google/frontier-model-1", "tier": "capable"}},
}
ROWS = {"columns": ["n"], "types": ["BIGINT"], "rows": [[1]], "truncated": False, "duration_ms": 3}


def fake_api(
    statuses: dict[str, str] | None = None,
    requests: list[str] | None = None,
    submitted: list[dict] | None = None,
    *,
    pack: dict | None = None,
) -> httpx.MockTransport:
    """Answers every question at once; ``statuses`` maps a question to its final job status (default success).

    ``submitted`` collects each submit body with its conversation-id.
    """
    jobs: dict[str, dict] = {}
    seen = requests if requests is not None else []
    view = pack or PACK_VIEW

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(f"{request.method} {path}")
        if path == "/v1/pack":
            if request.url.params["id"] != view["id"]:
                return httpx.Response(404, json={"detail": f"No pack {request.url.params['id']}"})
            return httpx.Response(200, json=view)
        if path == "/v1/data_sources":
            assert request.url.params["pack"] == "retail"
            return httpx.Response(200, json=SOURCES)
        if path.endswith("/schema"):
            assert path == "/v1/data_sources/retail.sales/schema"
            return httpx.Response(200, json=SCHEMA)
        if path.endswith("/preview"):
            assert request.url.params["limit"] == "8"
            return httpx.Response(200, json={"table": request.url.params["table"], **ROWS})
        if path.endswith("/query"):
            return httpx.Response(200, json=ROWS)
        if path == "/v1/jobs/async/submit":
            body = json.loads(request.content)
            jobs[body["job_id"]] = {"question": body["input"], "status": (statuses or {}).get(body["input"], "success")}
            if submitted is not None:
                submitted.append(body | {"conversation_id": request.headers["conversation-id"]})
            return httpx.Response(200, json={"job_id": body["job_id"], "status": "submitted"})
        job_id = path.split("/")[5]
        job = jobs[job_id]
        if path.endswith("/export"):
            report = {"markdown": "Answer [1]", "citations": []}
            turn = {"jobId": job_id, "question": job["question"], "status": job["status"], "report": report}
            receipts = [SQL_RECEIPT, MULTI_SOURCE_RECEIPT]
            return httpx.Response(200, json=turn | {"events": [GATEWAY_LLM_CALL], "receipts": receipts})
        return httpx.Response(200, json={"job_id": job_id, "status": job["status"], "error": None})

    return httpx.MockTransport(handle)


def run(transport: httpx.MockTransport, out_dir, *, pack_id: str = "retail", **options) -> int:
    options = {"question_ids": [], "featured_only": False, "timeout": 5} | options
    with httpx.Client(transport=transport, base_url="http://api.test") as client:
        return record(client, pack_id=pack_id, out_dir=out_dir, **options)


def test_record_writes_the_pack_s_bundle_with_one_session_per_question_and_conversation(tmp_path):
    requests: list[str] = []
    submitted: list[dict] = []
    out = tmp_path / "rec"
    stale = out / "sessions" / "retired.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}")

    assert run(fake_api({}, requests, submitted), out) == 0

    index = json.loads((out / "index.json").read_text())
    assert (index["schemaVersion"], index["pack"]) == (2, {"id": "retail", "version": "1.0.0"})
    assert all(session["tools"] == [] for session in index["sessions"])  # the fake runs called no tool
    assert [(s["id"], s["title"], s["featured"], len(s["turns"])) for s in index["sessions"]] == [
        ("top-stores", "Top stores by revenue", True, 1),
        ("electronics-returns", "Electronics return window", False, 1),
        ("returns-follow-up", "Returns follow-up", False, 2),
    ]
    session = json.loads((out / "sessions" / "top-stores.json").read_text())
    assert (session["schemaVersion"], session["id"], session["title"]) == (2, "top-stores", "Top stores by revenue")
    assert session["turns"][0]["jobId"] == index["sessions"][0]["turns"][0]["jobId"]
    assert not stale.exists()
    # Each question is asked once, in its pack, with its own sources; a conversation's turns share a conversation
    assert [(body["input"], body["pack_id"], body["data_sources"]) for body in submitted] == [
        (QUESTIONS[0], "retail", ["retail.sales"]),
        (QUESTIONS[1], "retail", ["retail.policies"]),
        (TURNS[0], "retail", ["retail.policies"]),
        (TURNS[1], "retail", ["retail.policies"]),
    ]
    conversations = [body["conversation_id"] for body in submitted]
    assert len(set(conversations)) == 3 and conversations[2] == conversations[3]
    follow_up = json.loads((out / "sessions" / "returns-follow-up.json").read_text())
    assert [turn["question"] for turn in follow_up["turns"]] == TURNS
    # Replay needs no API: the pack's view and its sources come with the bundle
    assert json.loads((out / "pack.json").read_text()) == PACK_VIEW
    assert json.loads((out / "sources.json").read_text()) == SOURCES


def test_database_json_holds_each_structured_source_of_the_pack_by_id(tmp_path):
    assert run(fake_api(), tmp_path, question_ids=["top-stores"]) == 0

    database = json.loads((tmp_path / "database.json").read_text())
    assert database == {
        "schemaVersion": 1,
        "sources": [
            {
                "id": "retail.sales",
                "name": "Sales",
                "databaseName": "retail_sales",
                "schema": SCHEMA,
                "previews": {"customers": {"table": "customers", **ROWS}, "orders": {"table": "orders", **ROWS}},
                "queries": [
                    {"sql": SQL, "result": ROWS},  # the recorded answer's query, so replay can rerun it
                    {"sql": 'SELECT * FROM "main"."customers" LIMIT 25', "result": ROWS},
                    {"sql": 'SELECT * FROM "main"."orders" LIMIT 25', "result": ROWS},
                ],
            }
        ],
    }


def test_a_question_that_fails_is_left_out_and_the_command_fails(tmp_path):
    assert run(fake_api({QUESTIONS[0]: "failure"}), tmp_path, featured_only=True) == 1

    assert json.loads((tmp_path / "index.json").read_text())["sessions"] == []


def test_a_conversation_stops_at_a_failed_turn_and_is_left_out(tmp_path):
    requests: list[str] = []

    assert run(fake_api({TURNS[0]: "failure"}, requests), tmp_path) == 1

    assert [session["id"] for session in json.loads((tmp_path / "index.json").read_text())["sessions"]] == [
        "top-stores",
        "electronics-returns",
    ]
    assert requests.count("POST /v1/jobs/async/submit") == 3  # the second turn is never asked
    assert not (tmp_path / "sessions" / "returns-follow-up.json").exists()


def test_recording_named_sessions_keeps_the_rest_of_the_bundle(tmp_path):
    run(fake_api(), tmp_path)
    before = json.loads((tmp_path / "index.json").read_text())["sessions"]

    assert run(fake_api(), tmp_path, question_ids=["returns-follow-up", "top-stores"], featured_only=True) == 0

    after = json.loads((tmp_path / "index.json").read_text())["sessions"]
    assert [session["id"] for session in after] == ["top-stores", "electronics-returns", "returns-follow-up"]
    assert after[1] == before[1]
    assert after[0]["turns"] != before[0]["turns"] and after[2]["turns"] != before[2]["turns"]
    assert sorted(path.stem for path in (tmp_path / "sessions").glob("*.json")) == [
        "electronics-returns",
        "returns-follow-up",
        "top-stores",
    ]


def test_an_id_the_pack_does_not_offer_stops_the_command_before_anything_is_asked(tmp_path, capsys):
    requests: list[str] = []

    code = run(fake_api({}, requests), tmp_path, question_ids=["top-stores", "top-storez"], featured_only=True)

    assert code == 2
    assert "POST /v1/jobs/async/submit" not in requests
    assert not (tmp_path / "index.json").exists()
    error = capsys.readouterr().err
    assert "Not offered by retail on this stack" in error and "top-storez" in error
    assert "It offers: top-stores, electronics-returns, returns-follow-up" in error


def test_a_pack_the_catalog_does_not_hold_stops_the_command(tmp_path, capsys):
    requests: list[str] = []

    assert run(fake_api({}, requests), tmp_path, pack_id="aerospace") == 2

    assert requests == ["GET /v1/pack"]
    assert "aerospace is not a pack of the running stack's catalog" in capsys.readouterr().err


def test_named_sessions_never_update_a_bundle_of_another_pack_version(tmp_path, capsys):
    run(fake_api(), tmp_path)
    before = (tmp_path / "index.json").read_text()
    requests: list[str] = []

    code = run(
        fake_api({}, requests, pack=PACK_VIEW | {"version": "1.1.0"}),
        tmp_path,
        question_ids=["electronics-returns"],
        featured_only=True,
    )

    assert code == 2
    assert "POST /v1/jobs/async/submit" not in requests
    assert (tmp_path / "index.json").read_text() == before
    assert "holds retail 1.0.0 and this stack serves 1.1.0" in capsys.readouterr().err


def test_served_model_ids_are_recorded_under_their_public_names(tmp_path):
    run(fake_api(), tmp_path, question_ids=["top-stores"], featured_only=True)

    turn = json.loads((tmp_path / "sessions" / "top-stores.json").read_text())["turns"][0]
    assert turn["events"][0]["display"]["attributes"] == {"served_model": "frontier-model-1", "tier": "capable"}


def test_record_needs_a_pack(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["record", "--out", str(tmp_path)])

    assert exit_info.value.code == 2
    assert "--pack" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("served", "public"),
    [
        # Made-up ids in a gateway's <provider>/<publisher>/<model> shape
        ("vertex/google/frontier-model-1", "frontier-model-1"),
        ("bedrock/anthropic/frontier-model-2.5", "frontier-model-2.5"),
        ("gcp/meta/small-model-v3", "small-model-v3"),
        ("Escalated to vertex/google/frontier-model-1.", "Escalated to frontier-model-1."),
        # Public ids and other paths stay as they are
        ("nvidia/nemotron-3-ultra-550b-a55b", "nvidia/nemotron-3-ultra-550b-a55b"),
        ("nvidia/llama-nemotron-rerank-vl-1b-v2", "nvidia/llama-nemotron-rerank-vl-1b-v2"),
        ("/v1/jobs/async/job/x/export", "/v1/jobs/async/job/x/export"),
    ],
)
def test_public_model_ids(served, public):
    assert public_model_ids({"a": [served], "b": 3}) == {"a": [public], "b": 3}
