# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The knowledge catalog: packs, their sources, and what the agent sees of each source."""

from __future__ import annotations

import json
import shutil

import pytest
from support import DOCUMENTS
from support import TABLES
from support import manifest_path
from support import read_manifest
from support import update_manifest
from support import write_manifest

from demo_api.catalog import MAX_AGENT_COLUMNS
from demo_api.catalog import MAX_AGENT_TABLES
from demo_api.catalog import CatalogUnavailableError
from demo_api.catalog import KnowledgeCatalog
from demo_api.catalog import PackNotFoundError
from demo_api.registry import ToolRegistry


def catalog(knowledge_dir, registry: ToolRegistry, features: str = "retrieval,tables") -> KnowledgeCatalog:
    return KnowledgeCatalog(knowledge_dir, registry, frozenset(features.split(",")))


def test_packs_list_the_industries_by_title_then_the_workspace(knowledge_dir, tool_registry):
    manufacturing = read_manifest(knowledge_dir, "packs", "retail") | {
        "id": "manufacturing",
        "title": "Manufacturing",
        "icon": "Factory",
        "status": "ingesting",
        "sources": [],
        "questions": [],
        "examples": [],
    }
    write_manifest(knowledge_dir, "packs", manufacturing)

    packs = catalog(knowledge_dir, tool_registry).packs()

    assert [(pack.id, pack.kind, pack.title, pack.icon, pack.status) for pack in packs] == [
        ("manufacturing", "industry", "Manufacturing", "Factory", "ingesting"),
        ("retail", "industry", "Retail", "Store", "ready"),
        ("workspace", "workspace", "Your data", "Upload", "empty"),
    ]
    assert packs[1].description.startswith("A fictional omnichannel retailer")


def test_an_unreadable_manifest_is_skipped(knowledge_dir, tool_registry):
    manifest_path(knowledge_dir, "packs", "broken").write_text("{not json")
    manifest_path(knowledge_dir, "sources", "retail.broken").write_text("[]")

    knowledge = catalog(knowledge_dir, tool_registry)
    assert [pack.id for pack in knowledge.packs()] == ["retail", "workspace"]
    assert [source.id for source in knowledge.sources()] == [DOCUMENTS, TABLES]


def test_without_a_catalog_nothing_is_available(tmp_path, tool_registry):
    knowledge = catalog(tmp_path / "missing", tool_registry)

    for call in (knowledge.packs, knowledge.pack_view, knowledge.sources):
        with pytest.raises(CatalogUnavailableError):
            call()


def test_a_pack_view_defaults_to_the_first_industry(knowledge_dir, tool_registry):
    knowledge = catalog(knowledge_dir, tool_registry)

    assert knowledge.pack_view().id == "retail"
    assert knowledge.pack_view("workspace").kind == "workspace"
    with pytest.raises(PackNotFoundError):
        knowledge.pack_view("aerospace")


@pytest.mark.parametrize(
    ("features", "offered"),
    [
        ("retrieval,tables", ["electronics-returns", "top-stores"]),
        ("retrieval,tables,kumo", ["electronics-returns", "top-stores", "churn-risk"]),
        ("tables,kumo", ["top-stores", "churn-risk"]),
    ],
)
def test_a_question_is_offered_when_its_sources_and_tool_pills_are_served(
    knowledge_dir, tool_registry, features, offered
):
    view = catalog(knowledge_dir, tool_registry, features).pack_view("retail")

    assert [question.id for question in view.questions] == offered
    assert view.examples == offered  # the pack's examples, among the offered questions


def test_sources_are_narrowed_to_the_running_tools(knowledge_dir, tool_registry):
    sources = {source.id: source for source in catalog(knowledge_dir, tool_registry).sources()}
    assert sources[TABLES].capabilities == ("structured_retrieval",)
    assert sources[DOCUMENTS].capabilities == ("unstructured_retrieval",)

    with_kumo = catalog(knowledge_dir, tool_registry, "retrieval,tables,kumo").source(TABLES)
    assert with_kumo.capabilities == ("structured_retrieval", "structured_prediction")
    assert [source.id for source in catalog(knowledge_dir, tool_registry, "tables").sources()] == [TABLES]


def test_sources_of_one_pack_or_of_all(knowledge_dir, tool_registry):
    knowledge = catalog(knowledge_dir, tool_registry)
    retail_sales = read_manifest(knowledge_dir, "sources", TABLES)
    database = retail_sales["database"] | {
        "path": "sources/workspace.tables/tables.duckdb",
        "alias": "workspace_tables",
    }
    workspace_tables = retail_sales | {"id": "workspace.tables", "pack_id": "workspace", "database": database}
    write_manifest(knowledge_dir, "sources", workspace_tables)

    assert [source.id for source in knowledge.sources("retail")] == [DOCUMENTS, TABLES]
    assert [source.id for source in knowledge.sources("workspace")] == ["workspace.tables"]
    assert [source.id for source in knowledge.sources()] == [DOCUMENTS, TABLES, "workspace.tables"]
    assert knowledge.source(TABLES).pack_id == "retail"
    # A structured source's database is in its own directory, never another source's
    update_manifest(knowledge_dir, "sources", "workspace.tables", database=retail_sales["database"])
    assert [source.id for source in knowledge.sources("workspace")] == []
    with pytest.raises(PackNotFoundError):
        knowledge.sources("aerospace")


@pytest.mark.parametrize(
    ("status", "offered"), [("ready", True), ("ingesting", True), ("empty", False), ("failed", False)]
)
def test_a_source_is_offered_while_it_has_usable_content(knowledge_dir, tool_registry, status, offered):
    update_manifest(knowledge_dir, "sources", DOCUMENTS, status=status)
    update_manifest(knowledge_dir, "sources", TABLES, status=status)

    knowledge = catalog(knowledge_dir, tool_registry)
    assert (knowledge.source(DOCUMENTS) is not None, knowledge.source(TABLES) is not None) == (offered, offered)


def test_a_structured_source_needs_a_table_and_a_database_inside_the_knowledge_volume(knowledge_dir, tool_registry):
    manifest = read_manifest(knowledge_dir, "sources", TABLES)
    knowledge = catalog(knowledge_dir, tool_registry)

    update_manifest(knowledge_dir, "sources", TABLES, database=manifest["database"] | {"tables": []})
    assert knowledge.source(TABLES) is None
    update_manifest(knowledge_dir, "sources", TABLES, database=manifest["database"] | {"path": "../etc/tables.duckdb"})
    assert knowledge.source(TABLES) is None
    assert knowledge.database_path(TABLES) is None


def test_database_path_is_the_structured_source_s_duckdb(knowledge_dir, tool_registry):
    knowledge = catalog(knowledge_dir, tool_registry)

    assert knowledge.database_path(TABLES) == knowledge_dir / "sources" / "retail.sales" / "tables.duckdb"
    assert knowledge.database_path(DOCUMENTS) is None
    assert knowledge.database_path("retail.nothing") is None


def test_public_sources_name_their_pack_status_and_database(knowledge_dir, tool_registry):
    public = [source.public() for source in catalog(knowledge_dir, tool_registry).sources()]

    assert public == [
        {
            "id": DOCUMENTS,
            "pack_id": "retail",
            "name": "Policies & Procedures",
            "description": "Return policy, store operating procedures and supplier terms.",
            "default_enabled": True,
            "kind": "documents",
            "capabilities": ["unstructured_retrieval"],
            "synthetic": True,
            "status": "ready",
            "database_name": None,
        },
        {
            "id": TABLES,
            "pack_id": "retail",
            "name": "Sales & Customers",
            "description": "Stores, customers, orders and returns.",
            "default_enabled": True,
            "kind": "structured",
            "capabilities": ["structured_retrieval"],
            "synthetic": True,
            "status": "ready",
            "database_name": "retail_sales",
        },
    ]


def test_the_agent_sees_a_structured_source_s_tables_keys_and_templates(knowledge_dir, tool_registry):
    entry = catalog(knowledge_dir, tool_registry, "retrieval,tables,kumo").source(TABLES).catalog_entry()

    assert entry["description"].startswith("Northwind Retail's operational tables")
    assert entry["capabilities"] == ["structured_retrieval", "structured_prediction"]
    assert entry["database"]["alias"] == "retail_sales"
    customers, orders = entry["database"]["tables"]
    assert customers == {
        "name": "customers",
        "description": "One row per loyalty customer.",
        "row_count": 3,
        "primary_key": "customer_id",
        "time_column": None,
        "columns": [
            {"name": "customer_id", "type": "VARCHAR", "description": ""},
            {"name": "tier", "type": "VARCHAR", "description": "Loyalty tier: bronze, silver or gold."},
            {"name": "joined_at", "type": "DATE", "description": ""},
        ],
        "foreign_keys": [],
    }
    assert (orders["time_column"], orders["row_count"]) == ("ordered_at", 5)
    assert orders["foreign_keys"] == [
        {"column": "customer_id", "references_table": "customers", "references_column": "customer_id"}
    ]
    assert entry["prediction_templates"] == [
        {
            "id": "churn_90d",
            "name": "No order in 90 days",
            "description": "Likelihood that a customer places no order in the next 90 days.",
            "pql": "PREDICT COUNT(orders.*, 0, 90, days) = 0 FOR EACH customers.customer_id",
            "anchor_time": "2026-09-30T00:00:00Z",
        }
    ]


def test_prediction_templates_need_the_prediction_tool(knowledge_dir, tool_registry):
    assert catalog(knowledge_dir, tool_registry).source(TABLES).catalog_entry()["prediction_templates"] == []


def test_a_documents_source_has_no_database_in_its_catalog_entry(knowledge_dir, tool_registry):
    entry = catalog(knowledge_dir, tool_registry).source(DOCUMENTS).catalog_entry()

    assert entry == {
        "id": DOCUMENTS,
        "name": "Policies & Procedures",
        "description": read_manifest(knowledge_dir, "sources", DOCUMENTS)["agent_description"],
        "kind": "documents",
        "capabilities": ["unstructured_retrieval"],
        "synthetic": True,
        "example_questions": ["What restocking fee applies to returned furniture?"],
    }


def test_the_agent_s_view_of_a_large_database_is_capped(knowledge_dir, tool_registry):
    manifest = read_manifest(knowledge_dir, "sources", TABLES)
    table = manifest["database"]["tables"][0]
    columns = [{"name": f"c{n}", "type": "INTEGER", "description": "", "nullable": True} for n in range(45)]
    tables = [table | {"name": f"t{n}", "columns": columns} for n in range(MAX_AGENT_TABLES + 5)]
    update_manifest(knowledge_dir, "sources", TABLES, database=manifest["database"] | {"tables": tables})

    database = catalog(knowledge_dir, tool_registry).source(TABLES).catalog_entry()["database"]

    assert len(database["tables"]) == MAX_AGENT_TABLES
    assert database["omitted_tables"] == 5
    assert len(database["tables"][0]["columns"]) == MAX_AGENT_COLUMNS
    assert database["tables"][0]["omitted_columns"] == 5
    assert len(json.dumps(database)) < 200_000


def test_manifests_are_read_on_every_call(knowledge_dir, tool_registry):
    knowledge = catalog(knowledge_dir, tool_registry)
    assert knowledge.source(DOCUMENTS) is not None

    manifest_path(knowledge_dir, "sources", DOCUMENTS).unlink()
    shutil.rmtree(knowledge_dir / "catalog" / "packs")
    assert knowledge.source(DOCUMENTS) is None
    assert knowledge.packs() == []
