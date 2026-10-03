# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Every pack's optional `eval/answers.yaml` follows its format and agrees with the pack and the tool registry."""

import json
import re
from pathlib import Path

import pytest
import yaml
from support import REPO

from demo_eval import facts
from demo_eval.spec import FACT
from demo_eval.spec import SpecError
from demo_eval.spec import catalog_id
from demo_eval.spec import load_answers

# A pack's answers file is optional: only the packs that have one are checked
PACKS = sorted(path.parents[1] for path in (REPO / "data" / "packs").glob("*/eval/answers.yaml"))
TOOLS = {tool["id"]: tool for tool in json.loads((REPO / "contracts" / "tool-registry.json").read_text())["tools"]}


def questions(pack: Path) -> dict[str, dict]:
    return {q["id"]: q for q in yaml.safe_load((pack / "questions.yaml").read_text())["questions"]}


def sources(pack: Path) -> dict[str, str]:
    """The pack's catalog source ids, by kind."""
    listed = yaml.safe_load((pack / "pack.yaml").read_text())["sources"]
    return {catalog_id(pack.name, source["id"]): source["kind"] for source in listed}


@pytest.mark.parametrize("pack", PACKS, ids=lambda pack: pack.name)
def test_the_answer_checks_name_real_questions_tools_sources_and_oracle_columns(pack):
    spec = load_answers(pack)
    offered, catalog = questions(pack), sources(pack)
    assert not spec.source or catalog.get(catalog_id(pack.name, spec.source)) == "structured", spec.source
    for qid, question in spec.questions.items():
        assert qid in offered, f"{pack.name}: answers.yaml checks {qid}, which questions.yaml lacks"
        for group in question.tools:
            assert group <= set(TOOLS), f"{pack.name}/{qid}: unknown tools {sorted(group - set(TOOLS))}"
        # The question's declared pills come from tools its checks require
        required = {pill for group in question.tools for tool in group for pill in TOOLS[tool]["pills"]}
        assert set(offered[qid].get("tools") or []) & required or not question.tools, qid
        for check in question.checks:
            if check.kind in ("mentions", "percent"):  # each referenced column is one the oracle's SQL selects
                oracle, field = re.match(r"(\w+)\[.*\]\.(\w+)", check.value).groups()
                sql = next(o.sql for o in question.oracles if o.name == oracle)
                assert re.search(rf"\b{field}\b", (pack / "eval" / "oracles" / f"{sql}.sql").read_text()), (
                    f"{pack.name}/{qid}: {sql}.sql has no column {field}"
                )
            if check.kind == "retrieved_source":
                assert catalog.get(check.value) == "documents", (
                    f"{pack.name}/{qid}: {check.value} is no documents source"
                )
            if check.kind == "prediction_available":
                assert "kumo" in (offered[qid].get("tools") or []), f"{pack.name}/{qid} does not declare kumo"


@pytest.mark.parametrize("pack", PACKS, ids=lambda pack: pack.name)
def test_the_facts_render_every_placeholder(pack):
    spec = load_answers(pack)
    for question in spec.questions.values():
        assert question.facts, f"{pack.name}/{question.id} has no reference facts for the grader"
        placeholders = list(FACT.finditer(question.facts))
        fields = {name.strip() for match in placeholders for name in match["fields"].split(",")}
        rows = {o.name: [dict.fromkeys(fields, 0.1)] * 25 for o in question.oracles}
        rendered = facts.render(spec, question, rows)
        assert not FACT.search(rendered), question.id
        for field in fields:
            assert f"{field}=" in rendered, f"{pack.name}/{question.id}: {field} did not render"


def _pack(tmp_path: Path, answers: str) -> Path:
    (tmp_path / "eval" / "oracles").mkdir(parents=True)
    (tmp_path / "eval" / "oracles" / "leaders.sql").write_text("SELECT 'a' AS store")
    (tmp_path / "eval" / "answers.yaml").write_text(answers)
    return tmp_path


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        ("questions: {q: {oracles: {x: {}}}}", "no eval/oracles/x.sql"),
        ("questions: {q: {oracles: {leaders: {limit: 500}}}}", "limit must be 1 to 100"),
        ("questions: {q: {checks: [{id: a, mentions: 'leaders[0].store'}]}}", "names oracle leaders"),
        ("questions: {q: {checks: [{id: a, pattern: '('}]}}", "bad pattern"),
        ("questions: {q: {checks: [{id: a}]}}", "exactly one of"),
        ("questions: {q: {checks: [{id: a, pattern: x}, {id: a, pattern: y}]}}", "check ids repeat"),
        ("questions: {q: {checks: [{id: a, contains: []}]}}", "contains is a non-empty string or list"),
        ("questions: {q: {checks: [{id: a, contains: [x, '']}]}}", "contains is a non-empty string or list"),
        ("questions: {q: {checks: [{id: a, percent_grounding: 90}]}}", "percent_grounding is a share"),
        ("questions: {q: {checks: [{id: a, prediction_available: false}]}}", "prediction_available is true"),
        ("questions: {q: {checks: [{id: a, named: 'x[0].y'}]}}", "exactly one of"),  # a market check: gone
        ("questions: {q: {tools: [retrieve_evidence]}}", "tools is a list"),
        ("questions: {q: {facts: '{leaders: store}'}}", "facts name oracle leaders"),
        ("questions: {q: {colour: red}}", "unknown key"),
        ("names: SELECT 1\nquestions: {}", "unknown key(s) names"),  # the market answers.yaml header: gone
        (
            "questions: {q: {oracles: {leaders: {}}}, r: {oracles: {leaders: {limit: 5}}}}",
            "oracle leaders is defined differently",
        ),
    ],
)
def test_a_malformed_answers_file_is_refused_with_its_entry(tmp_path, answers, message):
    with pytest.raises(SpecError, match=re.escape(message)):
        load_answers(_pack(tmp_path, answers))


def test_a_pack_without_an_answers_file_gets_only_the_generic_checks(tmp_path):
    assert load_answers(tmp_path).questions == {}


def test_the_checks_of_a_valid_file_are_normalized(tmp_path):
    answers = """
dataset: "synthetic: test"
source: sales
format: {percent: [share]}
questions:
  q:
    oracles: {leaders: {}}
    tools: [[query_tables], [retrieve_evidence, predict]]
    checks:
      - {id: a, contains: ["30 days", "restocking"]}
      - {id: b, contains: gold}
      - {id: c, retrieved_source: policies}
      - {id: d, retrieved_source: other.policies}
      - {id: e, prediction_available: true}
      - {id: f, mentions: "leaders[:3].store", at_least: 2}
"""
    spec = load_answers(_pack(tmp_path / "retail", answers))
    checks = {check.id: check for check in spec.question("q").checks}

    assert (spec.pack, spec.dataset, spec.source, spec.percent) == ("retail", "synthetic: test", "sales", {"share"})
    assert spec.question("q").tools == (frozenset({"query_tables"}), frozenset({"retrieve_evidence", "predict"}))
    assert checks["a"].value == ("30 days", "restocking") and checks["b"].value == ("gold",)
    assert (checks["c"].value, checks["d"].value) == ("retail.policies", "other.policies")  # the catalog id
    assert checks["e"].value is True
    assert (checks["f"].kind, checks["f"].at_least, checks["f"].any) == ("mentions", 2, False)


def test_a_catalog_id_is_the_pack_id_and_the_source_id():
    assert catalog_id("retail", "sales") == "retail.sales"
    assert catalog_id("retail", "retail.sales") == "retail.sales"
