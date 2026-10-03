# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""contracts/tool-registry.json agrees with its schema and with the receipt union."""

from typing import get_args

from jsonschema import Draft202012Validator

from demo_api.events import COMPONENT_BY_SERVER
from demo_api.pills import PILL_ORDER
from demo_api.receipts import ArtifactKind


def test_registry_matches_its_schema(registry, registry_schema):
    Draft202012Validator.check_schema(registry_schema)
    Draft202012Validator(registry_schema).validate(registry)


def test_hermes_name_is_mcp_server_and_id(tools):
    for tool in tools:
        assert tool["hermes_name"] == f"mcp__{tool['server']}__{tool['id']}"


def test_ids_and_hermes_names_are_unique(tools):
    names = [tool["id"] for tool in tools] + [tool["hermes_name"] for tool in tools]
    assert len(names) == len(set(names))


def test_each_receipt_kind_is_one_union_variant(registry_schema, tools):
    kinds = set(get_args(ArtifactKind))
    assert set(registry_schema["$defs"]["Tool"]["properties"]["receipt_kind"]["enum"]) == kinds
    assert {tool["receipt_kind"] for tool in tools} == kinds


def test_the_registry_holds_the_four_knowledge_tools(tools):
    assert {tool["hermes_name"]: (tool["family"], tool["receipt_kind"], tool["pills"]) for tool in tools} == {
        "mcp__retrieval__retrieve_evidence": ("unstructured_retrieval", "retrieval_evidence", ["retrieval"]),
        "mcp__tables__query_tables": ("structured_retrieval", "structured_query", ["duckdb"]),
        "mcp__prediction__predict": ("structured_prediction", "structured_prediction", ["kumo"]),
        "mcp__auto_ontology__ask_question": ("structured_retrieval", "structured_query", ["ontology"]),
    }


def test_each_tool_server_is_one_event_component(tools):
    """Tools of one family on different servers are different components of the graph (DuckDB is not Auto Ontology)."""
    assert COMPONENT_BY_SERVER == {
        "retrieval": "milvus.retrieval",
        "tables": "duckdb.tables",
        "prediction": "nvidia.kumo",
        "auto_ontology": "nvidia.ontology",
    }
    assert set(COMPONENT_BY_SERVER) == {tool["server"] for tool in tools}


def test_pills_are_ordered_as_the_schema_lists_them(registry_schema):
    assert PILL_ORDER == tuple(registry_schema["$defs"]["Pill"]["enum"])
