# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The catalog's data sources, and the read-only data viewer of each structured source."""

from __future__ import annotations

import duckdb
import httpx
import pytest
import yaml
from pydantic import SecretStr
from support import DOCUMENTS
from support import TABLES
from support import read_manifest
from support import update_manifest
from support import write_manifest

STRUCTURED = f"/v1/data_sources/{TABLES}"


async def test_data_sources_of_a_pack_offer_only_capabilities_of_the_running_tools(api):
    sources = (await api.get("/v1/data_sources", params={"pack": "retail"})).json()

    # AGENT_FEATURES=retrieval,tables: no Kumo, no Auto Ontology
    assert [(s["id"], s["pack_id"], s["kind"], s["capabilities"], s["database_name"]) for s in sources] == [
        (DOCUMENTS, "retail", "documents", ["unstructured_retrieval"], None),
        (TABLES, "retail", "structured", ["structured_retrieval"], "retail_sales"),
    ]
    assert all(source["status"] == "ready" for source in sources)


async def test_data_sources_cover_every_pack_unless_one_is_named(api, knowledge_dir):
    uploads = read_manifest(knowledge_dir, "sources", DOCUMENTS) | {
        "id": "workspace.documents",
        "pack_id": "workspace",
        "status": "ingesting",
    }
    write_manifest(knowledge_dir, "sources", uploads)

    every = [source["id"] for source in (await api.get("/v1/data_sources")).json()]
    assert every == [DOCUMENTS, TABLES, "workspace.documents"]
    workspace = (await api.get("/v1/data_sources", params={"pack": "workspace"})).json()
    assert [(source["id"], source["status"]) for source in workspace] == [("workspace.documents", "ingesting")]
    assert (await api.get("/v1/data_sources", params={"pack": "aerospace"})).status_code == 404


async def test_schema_lists_the_tables_with_the_keys_the_catalog_profiled(api):
    schema = (await api.get(f"{STRUCTURED}/schema")).json()

    assert (schema["source_id"], schema["database_name"]) == (TABLES, "retail_sales")
    tables = {table["name"]: table for table in schema["tables"]}
    assert set(tables) == {"customers", "orders"}
    # Ingest loads files without constraints; the keys come from the source manifest
    assert tables["orders"]["primary_key"] == ["order_id"]
    assert tables["orders"]["columns"][0] == {
        "name": "order_id",
        "type": "VARCHAR",
        "nullable": True,
        "primary_key": True,
    }
    assert tables["orders"]["columns"][2]["type"] == "TIMESTAMP"
    assert schema["relationships"] == [
        {"from_table": "orders", "from_column": "customer_id", "to_table": "customers", "to_column": "customer_id"}
    ]


async def test_schema_lists_views_and_other_schemas_too(api, knowledge_dir):
    with duckdb.connect(str(knowledge_dir / "sources" / TABLES / "tables.duckdb")) as connection:
        connection.execute("CREATE SCHEMA marts")
        connection.execute("CREATE VIEW marts.gold_customers AS SELECT customer_id FROM main.customers")

    tables = {table["name"]: table for table in (await api.get(f"{STRUCTURED}/schema")).json()["tables"]}
    assert (tables["marts.gold_customers"]["kind"], tables["marts.gold_customers"]["primary_key"]) == ("view", [])
    gold = (await api.get(f"{STRUCTURED}/preview", params={"table": "marts.gold_customers", "limit": 1})).json()
    assert (gold["rows"], gold["truncated"]) == ([["C1"]], True)


async def test_preview_returns_the_first_rows(api):
    preview = (await api.get(f"{STRUCTURED}/preview", params={"table": "orders", "limit": 2})).json()

    assert preview["columns"] == ["order_id", "customer_id", "ordered_at", "net_amount"]
    assert preview["types"] == ["VARCHAR", "VARCHAR", "TIMESTAMP", "DOUBLE"]
    assert (preview["rows"], preview["truncated"]) == (
        [["O1", "C1", "2026-06-01 10:00:00", 120.5], ["O2", "C1", "2026-08-15 12:30:00", 80.0]],
        True,
    )
    assert (await api.get(f"{STRUCTURED}/preview", params={"table": "secrets"})).status_code == 404


@pytest.mark.parametrize(
    "tables",
    [
        "orders o JOIN customers c USING (customer_id)",
        # How the agent writes it: the tables tool attaches each source under its alias
        "retail_sales.orders o JOIN retail_sales.main.customers c USING (customer_id)",
    ],
)
async def test_query_runs_one_bounded_select(api, tables):
    sql = f"SELECT c.tier, count(*) AS n, sum(o.net_amount) AS revenue FROM {tables} GROUP BY 1 ORDER BY 1"
    result = (await api.post(f"{STRUCTURED}/query", json={"sql": sql})).json()

    assert result.pop("duration_ms") >= 0
    assert result == {
        "columns": ["tier", "n", "revenue"],
        "types": ["VARCHAR", "BIGINT", "DOUBLE"],
        "rows": [["gold", 4, 515.5], ["silver", 1, 45.25]],
        "truncated": False,
    }


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "SELECT 1; SELECT 2",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM other_db.main.orders",
        "SELECT * FROM other_db.orders",
        "SELECT * FROM missing_table",
        "COPY orders TO '/tmp/out.csv'",
        "ATTACH '/tmp/other.duckdb' AS other",
        "SELECT FROM WHERE",
    ],
)
async def test_query_refuses_anything_but_a_select_over_the_database(api, sql):
    response = await api.post(f"{STRUCTURED}/query", json={"sql": sql})
    assert response.status_code == 422
    assert "detail" in response.json()


async def test_viewer_routes_are_404_for_a_document_source_or_an_unavailable_one(api, knowledge_dir):
    assert (await api.get(f"/v1/data_sources/{DOCUMENTS}/schema")).status_code == 404
    update_manifest(knowledge_dir, "sources", TABLES, status="failed")
    assert (await api.get(f"{STRUCTURED}/schema")).status_code == 404


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
                                "name": "customers",
                                "pk": ["c-1"],
                                "columns": [{"id": "c-1", "name": "customer_id", "type": "VARCHAR", "sample": "C1"}],
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
                "name": "Customer",
                "represents": ["t-1"],
                "columns_attributes": [{"id": "attr-1", "name": "Customer id", "column_id": "c-1"}],
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
            # Auto Ontology names a DuckDB connection after the file: tables.duckdb is "tables"
            return httpx.Response(200, json={"data": [{"id": "db-1", "name": "tables"}], "count": 1})
        if request.url.path == "/api/model/export":
            return httpx.Response(200, text=yaml.safe_dump(EXPORT), headers={"content-type": "application/x-yaml"})
        return httpx.Response(200, json={})

    settings.auto_ontology_url = "http://ontology.test"
    settings.auto_ontology_email = "demo@example.com"
    settings.auto_ontology_password = SecretStr("not-a-real-password")
    settings.auto_ontology_source = TABLES
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
        ("database", "retail_sales"),
        ("schema", "main"),
        ("table", "customers"),
        ("column", "customer_id"),
        ("term", "Customer"),
        ("attribute", "Customer id"),
    ]
    assert {edge["kind"] for edge in snapshot["edges"]} == {"contains", "represents", "has_attribute", "maps_to"}
    assert "never-leaks" not in str(snapshot) and "db-1" not in str(snapshot)
    column = next(node for node in snapshot["nodes"] if node["kind"] == "column")
    assert column["primary_key"] is True


async def test_ontology_is_404_for_a_source_auto_ontology_does_not_serve(ontology_service, settings, api):
    settings.auto_ontology_source = "manufacturing.operations"

    response = await api.get(f"{STRUCTURED}/ontology")

    assert response.status_code == 404
    assert "manufacturing.operations" in response.json()["detail"]
    assert ontology_service == []
