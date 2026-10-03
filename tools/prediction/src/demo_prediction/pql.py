# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the tool reads from a PQL query before Kumo sees it: the tables it names, its entity, its entity filter, its
horizon and, when the target shows it, its task type. Kumo parses and validates the query itself.

    PREDICT COUNT(orders.*, 0, 90, days) = 0 FOR EACH customers.customer_id WHERE customers.tier = 'gold'
            └──── target, window 0..90 days ┘          └── entity table.key ──┘ └──── entity filter ────┘
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel

# A target's window, e.g. "COUNT(orders.*, 0, 90, days)": outcomes from the anchor to 90 days after it.
WINDOW = re.compile(r",\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*([a-z]+)\s*\)", re.IGNORECASE)
LITERAL = re.compile(r"'(?:''|[^'])*'")
REFERENCE = re.compile(r"\b([A-Za-z_]\w*)\s*\.\s*(?:\*|[A-Za-z_]\w*)")
PREDICT = re.compile(r"^\s*PREDICT\b", re.IGNORECASE)
ENTITY = re.compile(r"\bFOR\s+(EACH\s+)?([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)", re.IGNORECASE)
WHERE = re.compile(r"^\s*WHERE\b", re.IGNORECASE)
ASSUMING = re.compile(r"\bASSUMING\b", re.IGNORECASE)
RANK_TOP = re.compile(r"\bRANK\s+TOP\b", re.IGNORECASE)
COMPARISON = re.compile(r"<=|>=|<>|!=|=|<|>|\b(IS|LIKE|IN|STARTS\s+WITH|ENDS\s+WITH|CONTAINS)\b", re.IGNORECASE)
PARENTHESES = re.compile(r"\([^()]*\)")


class PqlError(ValueError):
    """Not a prediction query this tool can run. The message is safe to show the agent."""


class Horizon(BaseModel):
    value: int
    unit: str


@dataclass(frozen=True)
class Query:
    tables: frozenset[str]  # every table the query names, case-folded
    entity_table: str  # case-folded
    entity_column: str
    for_each: bool  # FOR EACH: the tool picks the entities; FOR <key> = / IN (...): the query names them
    entity_filter: str | None  # the WHERE after the entity, as written
    horizon: Horizon | None
    task_type: str | None  # when the target shows it; Kumo's result tells the rest


def parse(pql: str) -> Query:
    masked = _mask_literals(pql)
    start = PREDICT.match(masked)
    if start is None:
        raise PqlError(
            "A PQL query starts with PREDICT, e.g. PREDICT COUNT(orders.*, 0, 90, days) = 0 "
            "FOR EACH customers.customer_id."
        )
    entity = ENTITY.search(masked, start.end())
    if entity is None:
        raise PqlError("A PQL query names its entity: PREDICT <target> FOR EACH <table>.<primary key>.")
    target = masked[start.end() : entity.start()]
    if not target.strip():
        raise PqlError("PREDICT needs a target before FOR EACH, e.g. COUNT(orders.*, 0, 90, days) = 0.")

    entity_filter = None
    rest = masked[entity.end() :]
    if where := WHERE.match(rest):
        end = assuming.start() if (assuming := ASSUMING.search(rest, where.end())) else len(rest)
        entity_filter = pql[entity.end() + where.end() : entity.end() + end].strip() or None

    return Query(
        tables=frozenset(match.group(1).casefold() for match in REFERENCE.finditer(masked)),
        entity_table=entity.group(2).casefold(),
        entity_column=entity.group(3),
        for_each=entity.group(1) is not None,
        entity_filter=entity_filter,
        horizon=horizon(target),
        task_type=_task_type(target),
    )


def horizon(pql: str) -> Horizon | None:
    """The target's horizon, read from its window so it always matches the query that runs; None without one."""
    match = WINDOW.search(_mask_literals(pql))
    if match is None:
        return None
    start, end, unit = match.groups()
    return Horizon(value=int(end) - int(start), unit=unit.lower())


def _task_type(target: str) -> str | None:
    if RANK_TOP.search(target):
        return "temporal_link_prediction"
    outside = target
    while PARENTHESES.search(outside):  # drop arguments, so a filter inside an aggregate is not the target's
        outside = PARENTHESES.sub(" ", outside)
    return "binary_classification" if COMPARISON.search(outside) else None


def _mask_literals(pql: str) -> str:
    """The query with each string literal blanked, the same length, so nothing inside one is read as syntax."""
    return LITERAL.sub(lambda match: "'" + " " * (len(match.group()) - 2) + "'", pql)
