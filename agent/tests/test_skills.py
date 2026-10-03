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


def skill_text(name: str) -> str:
    return next(path for path in SKILL_DIRS if path.name == name).joinpath("SKILL.md").read_text(encoding="utf-8")


def section(text: str, heading: str) -> str:
    """The body of one ``## <heading>`` section."""
    return re.split(r"^## ", text.split(f"\n## {heading}\n", 1)[1], maxsplit=1, flags=re.M)[0]


EXAMPLE_QUERY = re.compile(r'retrieve_evidence\(query="([^"]+)"')


def test_the_search_skill_never_claims_a_document_is_absent():
    skill = skill_text("searching-documents")
    procedure = " ".join(section(skill, "Procedure").split())

    assert "never claim that a source contains no such document" in procedure
    assert "Pass only `query`" in skill  # the application sets source_ids
    assert len(EXAMPLE_QUERY.findall(skill)) >= 2


# Ultra left a leaders-and-laggards table uncited about one ask in three: an example answer table shows its rows
# carrying the token of the call that produced them.
EVIDENCE_TOKEN = re.compile(r"\[evidence:<evidence_id of the call>\]")


@pytest.mark.parametrize("name", ["querying-tables", "predicting-with-kumo"])
def test_example_answer_tables_cite_every_row(name):
    example = section(skill_text(name), "Example")
    rows = [line for line in example.splitlines() if line.startswith("| ") and not set(line) <= set("|- ")]
    header, *body = rows

    assert "Evidence" in header and body
    assert all(EVIDENCE_TOKEN.search(row) for row in body)


TABLE_REFERENCE = re.compile(r"\b(?:FROM|JOIN)\s+(\(|[A-Za-z_][\w.]*)", re.IGNORECASE)


def test_example_sql_qualifies_every_table_with_its_source_alias():
    sql = " ".join(re.findall(r'"([^"]*)"', section(skill_text("querying-tables"), "Example")))
    references = [name for name in TABLE_REFERENCE.findall(sql) if name != "("]

    assert references and all(re.fullmatch(r"[a-z_]+\.[a-z_]+", name) for name in references), references


PQL = re.compile(
    r"PREDICT (?:SUM|AVG|MIN|MAX|COUNT)\(\w+\.(?:\w+|\*), -?\d+, \d+, days\)(?: (?:=|!=|>=|<=|>|<) \S+)?"
    r" FOR EACH \w+\.\w+(?: WHERE .+)?"
)


def test_example_pql_follows_the_taught_grammar():
    skill = skill_text("predicting-with-kumo")
    examples = re.findall(r"^PREDICT .+$", skill, re.M) + re.findall(r'pql="(PREDICT [^"]+)"', skill)
    queries = [query for query in examples if "<target>" not in query]  # all but the grammar line

    assert len(queries) >= 6
    assert [query for query in queries if not PQL.fullmatch(query)] == []
    assert all(" FOR EACH " in query and "retail_sales." not in query for query in queries)  # no DuckDB alias


def test_the_kumo_skill_teaches_templates_and_an_unavailable_endpoint():
    skill = " ".join(skill_text("predicting-with-kumo").split())

    assert "`template:<id>`" in skill and 'pql="template:churn_90d"' in skill
    assert "If `available` is `false`, do not retry and do not substitute anything." in skill
    assert "say plainly that prediction is unavailable" in skill.lower()
