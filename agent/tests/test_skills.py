# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Skills lint: the Agent Skills spec, Hermes' skill index budget, and tool names."""

import re
from pathlib import Path

import pytest
import skills_ref
from common import MCP_TOOL
from common import SKILL_DIRS
from common import exposed_tools

HERMES_INDEX_LIMIT = 60  # SKILL_PROMPT_DESC_LIMIT: Hermes truncates longer descriptions in the skill index


def test_skills_exist():
    assert SKILL_DIRS


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda path: path.name)
def test_skill_follows_the_agent_skills_spec(skill_dir: Path):
    assert skills_ref.validate(skill_dir) == []


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda path: path.name)
def test_description_fits_the_hermes_skill_index(skill_dir: Path):
    description = skills_ref.read_properties(skill_dir).description
    assert len(description) <= HERMES_INDEX_LIMIT, f"{len(description)} chars; put the trigger words first"


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda path: path.name)
def test_named_tools_are_exposed_by_the_config(skill_dir: Path, config: dict):
    named = {match.group(0) for match in MCP_TOOL.finditer((skill_dir / "SKILL.md").read_text(encoding="utf-8"))}
    assert named <= exposed_tools(config)


# A form name in a retrieval query matches every filing's cover page, which then crowds out what the filings say
FORM_NAME = re.compile(r"\b(?:8-K|6-K|10-K|10-Q|20-F)\b|current report", re.IGNORECASE)
EXAMPLE_QUERY = re.compile(r'retrieve_evidence\(query="([^"]+)"')


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda path: path.name)
def test_example_retrieval_queries_name_no_filing_form(skill_dir: Path):
    queries = EXAMPLE_QUERY.findall((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    assert not [query for query in queries if FORM_NAME.search(query)]


def test_the_search_skill_keeps_form_names_out_of_queries():
    skill = next(path for path in SKILL_DIRS if path.name == "searching-documents").joinpath("SKILL.md").read_text()
    procedure = " ".join(skill.split("## Procedure")[1].split("## Pitfalls")[0].split())

    assert 'When you search filings, leave form names (8-K, 6-K) and words such as "SEC filing"' in procedure
    assert "never claim that a source contains no such document" in procedure
    assert len(EXAMPLE_QUERY.findall(skill)) >= 2  # a rule, and a kind of event in filings


# Ultra left a leaders-and-laggards table uncited about one ask in three: the market skill's example shows the rows
# themselves carrying the token of the call that produced them
EVIDENCE_TOKEN = re.compile(r"\[evidence:<evidence_id of the (highest|lowest) call>\]")


def test_the_market_skill_example_cites_every_table_row():
    skill = next(path for path in SKILL_DIRS if path.name == "analyzing-market-data").joinpath("SKILL.md").read_text()
    example = skill.split("## Example")[1]
    rows = [line for line in example.splitlines() if line.startswith("| ") and not line.startswith("| Rank")]
    rows = [row for row in rows if not set(row) <= set("|- ")]

    assert rows and all(EVIDENCE_TOKEN.search(row) for row in rows)
    assert {EVIDENCE_TOKEN.search(row).group(1) for row in rows} == {"highest", "lowest"}
