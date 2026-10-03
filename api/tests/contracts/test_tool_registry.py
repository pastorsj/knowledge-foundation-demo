# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""contracts/tool-registry.json agrees with its schema and with the receipt union."""

from typing import get_args

from jsonschema import Draft202012Validator

from demo_api.events import COMPONENT_BY_FAMILY
from demo_api.receipts import ArtifactKind
from demo_api.receipts.models import MarketOperation


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


def test_analytics_tools_are_the_market_operations(tools):
    analytics_tools = {tool["id"] for tool in tools if tool["receipt_kind"] == "analytics_result"}
    assert analytics_tools == set(get_args(MarketOperation))


def test_each_family_has_an_event_component(registry_schema):
    assert set(COMPONENT_BY_FAMILY) == set(registry_schema["$defs"]["Tool"]["properties"]["family"]["enum"])
