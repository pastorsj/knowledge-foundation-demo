# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""``GET /v1/packs`` and ``GET /v1/pack?id=``: the packs a user picks from, and one pack's questions."""

from __future__ import annotations

import json
import shutil

import pytest
from jsonschema import Draft202012Validator
from support import REPO
from support import read_manifest
from support import update_manifest


def schema(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((REPO / "contracts" / "schemas" / name).read_text(encoding="utf-8")))


async def test_packs_lists_the_industries_then_the_workspace(api):
    response = await api.get("/v1/packs")

    assert response.status_code == 200
    schema("packs.schema.json").validate(response.json())
    assert response.json() == {
        "packs": [
            {
                "id": "retail",
                "kind": "industry",
                "title": "Retail",
                "description": (
                    "A fictional omnichannel retailer: stores, customers, orders and returns, beside its policies and "
                    "merchandising reviews."
                ),
                "icon": "Store",
                "status": "ready",
            },
            {
                "id": "workspace",
                "kind": "workspace",
                "title": "Your data",
                "description": "Files you upload, parsed with NVIDIA Nemotron Parse and loaded into DuckDB.",
                "icon": "Upload",
                "status": "empty",
            },
        ]
    }


@pytest.mark.parametrize(
    ("features", "questions"),
    [
        ("retrieval,tables", ["electronics-returns", "top-stores"]),
        ("retrieval,tables,kumo", ["electronics-returns", "top-stores", "churn-risk"]),
    ],
)
async def test_a_pack_offers_the_questions_the_running_tools_can_answer(api, questions):
    response = await api.get("/v1/pack", params={"id": "retail"})

    assert response.status_code == 200
    view = response.json()
    schema("pack.schema.json").validate(view)
    assert (view["id"], view["kind"], view["icon"], view["status"]) == ("retail", "industry", "Store", "ready")
    assert (view["version"], view["as_of"]) == ("1.0.0", "2026-09-30")
    assert view["disclaimer"].startswith("Synthetic data")
    assert [question["id"] for question in view["questions"]] == questions
    assert view["examples"] == questions
    top_stores = view["questions"][1]
    assert (top_stores["sources"], top_stores["tools"], top_stores["featured"]) == (["retail.sales"], ["duckdb"], True)


async def test_the_default_pack_is_the_first_industry(api):
    assert (await api.get("/v1/pack")).json()["id"] == "retail"


async def test_the_workspace_pack_has_no_questions_yet(api):
    view = (await api.get("/v1/pack", params={"id": "workspace"})).json()

    assert (view["kind"], view["title"], view["status"]) == ("workspace", "Your data", "empty")
    assert (view["questions"], view["examples"], view["conversations"]) == ([], [], [])


async def test_a_question_whose_source_is_not_ready_is_not_offered(api, knowledge_dir):
    update_manifest(knowledge_dir, "sources", "retail.policies", status="failed")

    view = (await api.get("/v1/pack", params={"id": "retail"})).json()
    assert [question["id"] for question in view["questions"]] == ["top-stores"]


async def test_conversations_are_offered_when_their_sources_are(api, knowledge_dir):
    conversation = {
        "id": "returns-then-sales",
        "label": "Returns, then sales",
        "tag": None,
        "description": None,
        "sources": ["retail.policies", "retail.sales"],
        "turns": ["What is the return window?", "How many orders were returned last month?"],
    }
    update_manifest(knowledge_dir, "packs", "retail", conversations=[conversation])
    assert (await api.get("/v1/pack", params={"id": "retail"})).json()["conversations"] == [conversation]

    update_manifest(knowledge_dir, "sources", "retail.policies", status="empty")
    assert (await api.get("/v1/pack", params={"id": "retail"})).json()["conversations"] == []


async def test_without_declared_examples_the_picker_offers_featured_questions_first(api, knowledge_dir):
    pack = read_manifest(knowledge_dir, "packs", "retail")
    question = pack["questions"][1]
    questions = [question | {"id": f"q{n}", "featured": n in (7, 14)} for n in range(15)]
    update_manifest(knowledge_dir, "packs", "retail", questions=questions, examples=[])

    view = (await api.get("/v1/pack", params={"id": "retail"})).json()
    assert len(view["questions"]) == 15
    assert view["examples"] == ["q7", "q14", "q0", "q1", "q2", "q3", "q4", "q5", "q6", "q8", "q9", "q10"]


@pytest.mark.parametrize(("pack_id", "status"), [("aerospace", 404), ("../retail", 422), ("Retail", 422)])
async def test_an_unknown_or_malformed_pack_id_is_refused(api, pack_id, status):
    assert (await api.get("/v1/pack", params={"id": pack_id})).status_code == status


async def test_before_the_catalog_exists_the_pack_routes_are_503(api, knowledge_dir):
    shutil.rmtree(knowledge_dir / "catalog")

    for path in ("/v1/packs", "/v1/pack", "/v1/data_sources"):
        response = await api.get(path)
        assert response.status_code == 503, path
        assert "catalog" in response.json()["detail"]
