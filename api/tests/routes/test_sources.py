# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The pack, its data sources, and the read-only data viewer."""

from __future__ import annotations

import json

import httpx
import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import SecretStr
from support import REPO

STRUCTURED = "/v1/data_sources/market_analysis_structured"


async def test_pack_lists_questions_the_running_sources_can_answer(api, data_dir):
    pack = (await api.get("/v1/pack")).json()
    assert (pack["id"], pack["title"]) == ("market-analysis", "Synthetic Multi-Asset Market Analysis")
    assert (pack["kind"], pack["icon"], pack["status"]) == ("industry", None, "ready")  # a pack.json without them
    assert [(q["id"], q["featured"]) for q in pack["questions"]] == [("market-leaders", True), ("filings", False)]
    assert pack["examples"] == ["market-leaders", "filings"]
    assert [(c["id"], len(c["turns"])) for c in pack["conversations"]] == [("leaders-follow-up", 2)]

    (data_dir / "collection-manifest.json").unlink()
    pack = (await api.get("/v1/pack")).json()
    assert [q["id"] for q in pack["questions"]] == ["market-leaders"]
    assert pack["examples"] == ["market-leaders"]


async def test_pack_matches_its_contract(api):
    schema = json.loads((REPO / "contracts" / "schemas" / "pack.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate((await api.get("/v1/pack")).json())


async def test_the_example_picker_offers_the_pack_s_examples_in_order(api, data_dir):
    pack = json.loads((data_dir / "pack.json").read_text())
    extra = {
        "id": "outlook",
        "label": "Outlook",
        "question": "What comes next?",
        "sources": ["market_analysis_structured"],
    }
    pack["questions"].append(extra | {"tools": ["kumo"]})
    pack["examples"] = ["outlook", "filings", "market-leaders"]
    (data_dir / "pack.json").write_text(json.dumps(pack))

    assert (await api.get("/v1/pack")).json()["examples"] == ["outlook", "filings", "market-leaders"]

    # Only those whose sources the stack serves: no retrieval index, no filings
    (data_dir / "collection-manifest.json").unlink()
    assert (await api.get("/v1/pack")).json()["examples"] == ["outlook", "market-leaders"]


async def test_without_a_list_the_picker_offers_the_featured_questions_then_the_rest_up_to_12(api, data_dir):
    pack = json.loads((data_dir / "pack.json").read_text())
    question = pack["questions"][0]
    pack["questions"] = [question | {"id": f"q{n}", "featured": n in (7, 14)} for n in range(15)]
    (data_dir / "pack.json").write_text(json.dumps(pack))

    view = (await api.get("/v1/pack")).json()
    assert len(view["questions"]) == 15
    assert view["examples"] == ["q7", "q14", "q0", "q1", "q2", "q3", "q4", "q5", "q6", "q8", "q9", "q10"]


async def test_data_sources_offer_only_capabilities_of_the_running_tools(api):
    sources = {source["id"]: source for source in (await api.get("/v1/data_sources")).json()}

    # AGENT_FEATURES=retrieval,tables: no Auto Ontology, no Kumo.
    assert sources["market_analysis_structured"]["capabilities"] == ["structured_retrieval"]
    assert sources["market_analysis_structured"]["database_name"] == "market_analysis"
    assert sources["market_news"]["capabilities"] == ["unstructured_retrieval"]


async def test_an_unbuilt_pack_is_503(api, data_dir):
    (data_dir / "pack.json").unlink()
    assert (await api.get("/v1/pack")).status_code == 503
    assert (await api.get("/v1/data_sources")).status_code == 503


async def test_schema_lists_tables_views_keys_and_relationships(api):
    schema = (await api.get(f"{STRUCTURED}/schema")).json()

    tables = {table["name"]: table for table in schema["tables"]}
    assert set(tables) == {"assets", "daily_prices", "prediction.asset_entities"}
    assert tables["prediction.asset_entities"]["kind"] == "view"
    assert tables["daily_prices"]["primary_key"] == ["price_id"]
    assert tables["daily_prices"]["columns"][2] == {
        "name": "close",
        "type": "DOUBLE",
        "nullable": True,
        "primary_key": False,
    }
    assert schema["relationships"] == [
        {"from_table": "daily_prices", "from_column": "asset_id", "to_table": "assets", "to_column": "asset_id"}
    ]


async def test_preview_returns_the_first_rows(api):
    preview = (await api.get(f"{STRUCTURED}/preview", params={"table": "daily_prices"})).json()

    assert preview["columns"] == ["price_id", "asset_id", "close", "traded_at"]
    assert preview["types"] == ["VARCHAR", "VARCHAR", "DOUBLE", "TIMESTAMP WITH TIME ZONE"]
    assert (len(preview["rows"]), preview["truncated"]) == (100, True)
    assert preview["rows"][0][3].startswith("2026-08-01")
    few = (await api.get(f"{STRUCTURED}/preview", params={"table": "prediction.asset_entities", "limit": 1})).json()
    assert (len(few["rows"]), few["truncated"]) == (1, True)
    assert (await api.get(f"{STRUCTURED}/preview", params={"table": "secrets"})).status_code == 404


async def test_query_runs_one_bounded_select(api):
    sql = "SELECT a.name, count(*) AS n FROM daily_prices p JOIN assets a USING (asset_id) GROUP BY 1 ORDER BY 1"
    result = (await api.post(f"{STRUCTURED}/query", json={"sql": sql})).json()

    assert result.pop("duration_ms") >= 0
    assert result == {
        "columns": ["name", "n"],
        "types": ["VARCHAR", "BIGINT"],
        "rows": [["Alpha", 75], ["Beta", 75]],
        "truncated": False,
    }


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM assets",
        "SELECT 1; SELECT 2",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM other_db.main.assets",
        "SELECT * FROM missing_table",
        "COPY assets TO '/tmp/out.csv'",
        "SELECT FROM WHERE",
    ],
)
async def test_query_refuses_anything_but_a_select_over_the_database(api, sql):
    response = await api.post(f"{STRUCTURED}/query", json={"sql": sql})
    assert response.status_code == 422
    assert "detail" in response.json()


async def test_viewer_routes_are_404_for_a_document_source(api):
    assert (await api.get("/v1/data_sources/market_news/schema")).status_code == 404


async def test_ontology_is_404_without_the_ontology_profile(api):
    assert (await api.get(f"{STRUCTURED}/ontology")).status_code == 404


EXPORT = {
    "data_layer": {
        "databases": [
            {
                "id": "db-1",
                "dialect": "duckdb",
                "connection": {"password": "never-leaks"},
                "schemas": [
                    {
                        "id": "s-1",
                        "name": "main",
                        "tables": [
                            {
                                "id": "t-1",
                                "name": "assets",
                                "pk": ["c-1"],
                                "columns": [{"id": "c-1", "name": "asset_id", "type": "VARCHAR", "sample": "A1"}],
                            }
                        ],
                    }
                ],
            }
        ]
    },
    "semantic_layer": {
        "terms": [
            {
                "id": "term-1",
                "name": "Asset",
                "represents": ["t-1"],
                "columns_attributes": [{"id": "attr-1", "name": "Asset id", "column_id": "c-1"}],
            }
        ]
    },
}


@pytest.fixture
def ontology_service(settings, upstreams) -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/datasources/dbs":
            return httpx.Response(200, json={"data": [{"id": "db-1", "name": "market_analysis"}], "count": 1})
        if request.url.path == "/api/model/export":
            return httpx.Response(200, text=yaml.safe_dump(EXPORT), headers={"content-type": "application/x-yaml"})
        return httpx.Response(200, json={})

    settings.auto_ontology_url = "http://ontology.test"
    settings.auto_ontology_email = "demo@example.com"
    settings.auto_ontology_password = SecretStr("not-a-real-password")
    upstreams["ontology.test"] = handle
    return calls


async def test_ontology_is_a_bounded_graph_from_auto_ontology(ontology_service, api):
    snapshot = (await api.get(f"{STRUCTURED}/ontology")).json()

    assert [call.url.path for call in ontology_service] == [
        "/api/auth/sign-in/email",
        "/api/datasources/dbs",
        "/api/model/export",
        "/api/auth/sign-out",
    ]
    assert [(node["kind"], node["label"]) for node in snapshot["nodes"]] == [
        ("database", "market_analysis"),
        ("schema", "main"),
        ("table", "assets"),
        ("column", "asset_id"),
        ("term", "Asset"),
        ("attribute", "Asset id"),
    ]
    assert {edge["kind"] for edge in snapshot["edges"]} == {"contains", "represents", "has_attribute", "maps_to"}
    assert "never-leaks" not in str(snapshot) and "db-1" not in str(snapshot)
    column = next(node for node in snapshot["nodes"] if node["kind"] == "column")
    assert column["primary_key"] is True
