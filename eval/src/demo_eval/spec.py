# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""A pack's answer checks: `data/packs/<id>/eval/answers.yaml`, with its oracle queries in `eval/oracles/*.sql`.

The file is optional and sits outside the pack's ingested files: changing it never re-ingests the pack. A pack
without one gets only the generic checks. The format is described in `eval/README.md`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CHECK_KINDS = frozenset(
    {
        "mentions",  # the rows' values (names, ids) appear in the report; all, `at_least: N`, or `any: true`
        "contains",  # every one of a list of substrings appears in the report, ignoring case
        "percent",  # the rows' fractions appear as percentages, within display rounding
        "pattern",  # the report, without markdown emphasis, matches a regular expression (or any of a list)
        "retrieved_source",  # a retrieval call searched this source and returned hits
        "percent_grounding",  # at least this share of the report's percentages match a number in the evidence
        "prediction_available",  # the run's prediction receipt is available: Kumo scored the entities
    }
)
# <oracle>[<rows>].<field>: rows is an index (0, -1), a slice (:3, -3:, :) or max(<field>)
ROW_REF = re.compile(r"^(?P<oracle>\w+)\[(?P<rows>[^\]]*)\]\.(?P<field>\w+)$")
SLICE = re.compile(r"^(?P<start>-?\d*):(?P<stop>-?\d*)$")
MAXIMUM = re.compile(r"^max\((?P<field>\w+)\)$")


class SpecError(ValueError):
    """An answers file does not follow its format; the message names the file and the entry."""


def catalog_id(pack: str, source: str) -> str:
    """The catalog id of a pack's source: `<pack>.<source>`; an id that already is one is kept."""
    return source if "." in source else f"{pack}.{source}"


@dataclass(frozen=True)
class RowRef:
    oracle: str
    rows: str
    field: str

    @classmethod
    def parse(cls, text: str) -> RowRef:
        match = ROW_REF.match(str(text).strip())
        if not match or not _valid_rows(match["rows"]):
            raise SpecError(f"not a row reference <oracle>[<rows>].<field>: {text!r}")
        return cls(match["oracle"], match["rows"].strip(), match["field"])

    def select(self, oracles: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        """The rows this reference names, in the oracle's order (empty when the oracle returned too few)."""
        return select_rows(oracles.get(self.oracle) or [], self.rows)

    def values(self, oracles: dict[str, list[dict[str, Any]]]) -> list[Any]:
        return [row.get(self.field) for row in self.select(oracles)]


def _valid_rows(rows: str) -> bool:
    rows = rows.strip()
    return bool(rows == "" or re.fullmatch(r"-?\d+", rows) or SLICE.match(rows) or MAXIMUM.match(rows))


def select_rows(rows: list[dict[str, Any]], selector: str) -> list[dict[str, Any]]:
    selector = selector.strip()
    if selector in ("", ":"):
        return list(rows)
    if re.fullmatch(r"-?\d+", selector):
        index = int(selector)
        return [rows[index]] if -len(rows) <= index < len(rows) else []
    if match := SLICE.match(selector):
        start = int(match["start"]) if match["start"] else None
        stop = int(match["stop"]) if match["stop"] else None
        return rows[start:stop]
    if match := MAXIMUM.match(selector):
        values = [row.get(match["field"]) for row in rows if row.get(match["field"]) is not None]
        return [row for row in rows if values and row.get(match["field"]) == max(values)]
    raise SpecError(f"not a row selector: {selector!r}")


@dataclass(frozen=True)
class Oracle:
    """One oracle result: `eval/oracles/<sql>.sql`, optionally reordered and cut, stored under `name`."""

    name: str
    sql: str
    order: str = ""
    limit: int = 100


@dataclass(frozen=True)
class Check:
    id: str
    kind: str
    value: Any
    at_least: int | None = None
    any: bool = False


@dataclass(frozen=True)
class QuestionSpec:
    id: str
    oracles: tuple[Oracle, ...] = ()
    tools: tuple[frozenset[str], ...] = ()  # every group must have one tool called
    checks: tuple[Check, ...] = ()
    facts: str = ""


@dataclass(frozen=True)
class AnswerSpec:
    pack: str
    dataset: str = ""
    source: str = ""  # the pack-local id of the structured source the oracles query; "" when the pack has one
    signed_percent: frozenset[str] = frozenset()
    percent: frozenset[str] = frozenset()
    questions: dict[str, QuestionSpec] | None = None

    def question(self, qid: str) -> QuestionSpec | None:
        return (self.questions or {}).get(qid)


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SpecError(f"{where}: expected a mapping")
    return value


def _only(entry: dict[str, Any], allowed: set[str], where: str) -> None:
    if unknown := set(entry) - allowed:
        raise SpecError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")


def load_answers(pack_dir: Path) -> AnswerSpec:
    """`eval/answers.yaml` of a pack; a pack without one gets only the generic checks."""
    path = pack_dir / "eval" / "answers.yaml"
    if not path.is_file():
        return AnswerSpec(pack=pack_dir.name, questions={})
    raw = _mapping(yaml.safe_load(path.read_text()), str(path))
    _only(raw, {"dataset", "source", "format", "questions"}, str(path))
    formats = _mapping(raw.get("format"), f"{path}: format")
    _only(formats, {"signed_percent", "percent"}, f"{path}: format")
    questions = {}
    oracles: dict[str, Oracle] = {}
    for qid, entry in _mapping(raw.get("questions"), f"{path}: questions").items():
        questions[qid] = _question(pack_dir, qid, _mapping(entry, f"{path}: {qid}"), f"{path}: {qid}")
        for oracle in questions[qid].oracles:  # the run computes each name once, for every question that uses it
            if oracles.setdefault(oracle.name, oracle) != oracle:
                raise SpecError(f"{path}: {qid}: oracle {oracle.name} is defined differently by another question")
    return AnswerSpec(
        pack=pack_dir.name,
        dataset=str(raw.get("dataset") or ""),
        source=str(raw.get("source") or ""),
        signed_percent=frozenset(formats.get("signed_percent") or ()),
        percent=frozenset(formats.get("percent") or ()),
        questions=questions,
    )


def _question(pack_dir: Path, qid: str, entry: dict[str, Any], where: str) -> QuestionSpec:
    _only(entry, {"oracles", "tools", "checks", "facts"}, where)
    oracles = []
    for name, raw_options in _mapping(entry.get("oracles"), f"{where}: oracles").items():
        options = _mapping(raw_options, f"{where}: oracle {name}")
        _only(options, {"sql", "order", "limit"}, f"{where}: oracle {name}")
        sql, order, limit = options.get("sql") or name, options.get("order") or "", options.get("limit") or 100
        oracle = Oracle(name, str(sql), str(order), int(limit))
        if not (pack_dir / "eval" / "oracles" / f"{oracle.sql}.sql").is_file():
            raise SpecError(f"{where}: oracle {name}: no eval/oracles/{oracle.sql}.sql")
        if not 1 <= oracle.limit <= 100:
            raise SpecError(f"{where}: oracle {name}: limit must be 1 to 100 (the API returns at most 100 rows)")
        oracles.append(oracle)
    names = {oracle.name for oracle in oracles}
    tools = []
    for group in entry.get("tools") or []:
        if not isinstance(group, list) or not group:
            raise SpecError(f"{where}: tools is a list of non-empty lists of tool ids")
        tools.append(frozenset(str(tool) for tool in group))
    checks = [
        _check(_mapping(raw, f"{where}: check"), names, pack_dir.name, where) for raw in entry.get("checks") or []
    ]
    if len({check.id for check in checks}) != len(checks):
        raise SpecError(f"{where}: check ids repeat")
    facts = " ".join(str(entry.get("facts") or "").split())
    for match in FACT.finditer(facts):
        if match["oracle"] not in names:
            raise SpecError(f"{where}: facts name oracle {match['oracle']}, which the question does not compute")
    return QuestionSpec(qid, tuple(oracles), tuple(tools), tuple(checks), facts)


def _check(entry: dict[str, Any], oracles: set[str], pack: str, where: str) -> Check:
    kinds = set(entry) & CHECK_KINDS
    if len(kinds) != 1 or "id" not in entry:
        raise SpecError(f"{where}: a check has an id and exactly one of {', '.join(sorted(CHECK_KINDS))}: {entry}")
    _only(entry, {"id", "at_least", "any", *CHECK_KINDS}, where)
    [kind] = kinds
    value = entry[kind]
    if kind in ("mentions", "percent"):
        ref = RowRef.parse(value)
        if ref.oracle not in oracles:
            raise SpecError(f"{where}: check {entry['id']} names oracle {ref.oracle}, which the question lacks")
    elif kind == "contains":
        words = value if isinstance(value, list) else [value]
        if not words or not all(isinstance(word, str) and word.strip() for word in words):
            raise SpecError(f"{where}: check {entry['id']}: contains is a non-empty string or list of them")
        value = tuple(words)
    elif kind == "pattern":
        for pattern in value if isinstance(value, list) else [value]:
            try:
                re.compile(pattern)
            except re.error as error:
                raise SpecError(f"{where}: check {entry['id']}: bad pattern {pattern!r}: {error}") from None
    elif kind == "retrieved_source":
        value = catalog_id(pack, str(value))  # the pack's own id works: its hits carry the catalog id
    elif kind == "percent_grounding":
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0 < value <= 1:
            raise SpecError(f"{where}: check {entry['id']}: percent_grounding is a share, above 0 up to 1")
    elif kind == "prediction_available" and value is not True:
        raise SpecError(f"{where}: check {entry['id']}: prediction_available is true")
    at_least = entry.get("at_least")
    return Check(str(entry["id"]), kind, value, int(at_least) if at_least is not None else None, bool(entry.get("any")))


# {<oracle>[<rows>]: field, field} in a facts template: the rows as "field=value, ...; ..."
FACT = re.compile(r"\{(?P<oracle>\w+)(?:\[(?P<rows>[^\]]*)\])?:\s*(?P<fields>\w+(?:\s*,\s*\w+)*)\s*\}")
