# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every golden fixture validates, against both the models and the exported schemas."""

import json
from typing import get_args

import pytest
from jsonschema import Draft202012Validator

from demo_api.contracts import FIXTURES
from demo_api.contracts import SCHEMAS
from demo_api.contracts import json_schema
from demo_api.events import COMPONENT_BY_FAMILY
from demo_api.receipts import ArtifactKind

FIXTURE_SCHEMAS = {"execution-events.json": "execution-event", "receipts.json": "receipt"}


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_validates_against_its_model(contracts_dir, name):
    FIXTURES[name].validate_json((contracts_dir / "fixtures" / name).read_bytes())


@pytest.mark.parametrize("stem", SCHEMAS)
def test_exported_schema_is_current(contracts_dir, stem):
    exported = json.loads((contracts_dir / "schemas" / f"{stem}.schema.json").read_text(encoding="utf-8"))
    assert exported == json_schema(*SCHEMAS[stem]), "run scripts/gen-contracts.sh"


@pytest.mark.parametrize(("name", "stem"), FIXTURE_SCHEMAS.items())
def test_fixture_validates_against_the_exported_schema(contracts_dir, name, stem):
    schema = json.loads((contracts_dir / "schemas" / f"{stem}.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    for record in json.loads((contracts_dir / "fixtures" / name).read_text(encoding="utf-8")):
        validator.validate(record)


def test_every_receipt_kind_has_a_golden_receipt(receipts):
    assert {receipt["artifactKind"] for receipt in receipts} == set(get_args(ArtifactKind))


def test_golden_receipts_come_from_registered_tools(receipts, tools):
    kind_by_hermes_name = {tool["hermes_name"]: tool["receipt_kind"] for tool in tools}
    for receipt in receipts:
        assert kind_by_hermes_name[receipt["toolName"]] == receipt["artifactKind"]


def test_golden_events_reference_golden_receipts(events, receipts):
    artifact_refs = {ref for event in events for ref in event["artifactRefs"]}
    assert artifact_refs
    assert artifact_refs <= {receipt["receiptId"] for receipt in receipts}


def test_golden_tool_events_carry_their_family_and_component(events, tools):
    family_by_tool = {(tool["server"], tool["id"]): tool["family"] for tool in tools}
    tool_events = [event for event in events if event["toolName"] is not None]
    assert tool_events
    for event in tool_events:
        family = family_by_tool[event["toolServer"], event["toolName"]]
        assert (event["capabilityId"], event["componentId"]) == (family, COMPONENT_BY_FAMILY[family])


def test_golden_events_are_in_cursor_order(events):
    cursors = [event["cursor"] for event in events]
    assert cursors == sorted(set(cursors))


def test_the_catalog_s_pack_manifests_summarize_as_the_packs_contract(contracts_dir):
    """``GET /v1/packs`` summarizes pack manifests (contracts/catalog), so their kinds and statuses must agree."""
    manifests = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((contracts_dir / "fixtures/catalog/packs").glob("*.json"))
    ]
    fields = ("id", "kind", "title", "description", "icon", "status")
    packs = {"packs": [{field: manifest.get(field) for field in fields} for manifest in manifests]}

    schema = json.loads((contracts_dir / "schemas" / "packs.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(packs)
    assert {pack["kind"] for pack in packs["packs"]} == {"industry", "workspace"}
    manifest_schema = json.loads((contracts_dir / "catalog" / "pack-manifest.schema.json").read_text(encoding="utf-8"))
    for field in ("kind", "status"):
        summary_enum = schema["$defs"]["PackSummary"]["properties"][field]["enum"]
        assert summary_enum == manifest_schema["properties"][field]["enum"]
