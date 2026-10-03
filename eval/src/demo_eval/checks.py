# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deterministic checks of one report: its text against the oracle rows and against the run's own evidence.

A run is the job's export (`GET /v1/jobs/async/job/{id}/export`): the report, the `execution.v2` events and the
receipts. None of this needs a grader. The checks look for what a correct answer must contain (the value an oracle
computed, a share as a percentage, a required caveat) and never judge style.
"""

from __future__ import annotations

import re
from typing import Any

from .spec import Check
from .spec import RowRef

PERCENT = re.compile(r"([+\-]?\d+(?:\.\d+)?)\s?%")
# A word for a fall just before or after an unsigned percentage: "fell 1.23%", "a decline of 1.23%", "1.23% lower"
FALL = re.compile(
    r"\b(?:down|fell|fall(?:s|ing)?|dropp?(?:ed|ing|s)?|declin\w*|decreas\w*|lower|negative|loss(?:es)?|lost|slid"
    r"|slipp?\w*)\b",
    re.IGNORECASE,
)
DASHES = re.compile("[‐-–−]")
SOURCES = re.compile(r"\n#+\s*Sources")
# Markdown emphasis: a text check matches the words, so "**not** covered" reads as "not covered"
EMPHASIS = re.compile(r"\*+")

Oracles = dict[str, list[dict[str, Any]]]


def report_text(turn: dict[str, Any]) -> str:
    """The report's markdown, with typographic dashes and minus signs as "-"."""
    return DASHES.sub("-", ((turn or {}).get("report") or {}).get("markdown") or "")


def body(text: str) -> str:
    """The report before its Sources list."""
    return SOURCES.split(text)[0]


def has_percent(text: str, fraction: Any, tolerance: float = 0.0005) -> bool:
    """The report shows ``fraction`` as a percentage, within display rounding.

    A negative value also counts written without its sign next to a word for a fall ("fell 1.23%", "a 1.23%
    decline"); a signed figure must carry the right sign.
    """
    if not isinstance(fraction, int | float) or isinstance(fraction, bool):
        return False
    target, limit = fraction * 100, max(tolerance * 100, 0.051)
    for match in PERCENT.finditer(text):
        value = float(match[1])
        if abs(value - target) <= limit:
            return True
        unsigned = match[1][0] not in "+-"
        if target < 0 and unsigned and abs(value + target) <= limit:
            before, after = text[max(0, match.start() - 25) : match.start()], text[match.end() : match.end() + 12]
            if FALL.search(before) or FALL.search(after):
                return True
    return False


def receipt_numbers(turn: dict[str, Any]) -> list[float]:
    """Every number in the receipts' content, also times 100 (fractions shown as percentages)."""
    found: list[float] = []

    def walk(value: Any) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, int | float):
            found.extend((float(value), float(value) * 100))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for receipt in (turn or {}).get("receipts", []):
        walk(receipt.get("content"))
    return found


def grounding(text: str, turn: dict[str, Any]) -> float | None:
    """The share of the report's percentages (before Sources) that match a number in the evidence."""
    reported = [float(p) for p in PERCENT.findall(body(text))]
    if not reported:
        return None
    numbers = receipt_numbers(turn)
    hits = sum(any(abs(abs(n) - abs(p)) <= max(0.051, 0.005 * abs(p)) for n in numbers) for p in reported)
    return hits / len(reported)


def prediction_available(turn: dict[str, Any]) -> bool:
    """Kumo scored the entities: a prediction receipt completed with its content available."""
    return any(
        receipt.get("artifactKind") == "structured_prediction"
        and receipt.get("status") == "completed"
        and (receipt.get("content") or {}).get("available") is True
        for receipt in (turn or {}).get("receipts", [])
    )


def retrieved_sources(turn: dict[str, Any]) -> set[str]:
    return {
        hit.get("sourceId")
        for receipt in (turn or {}).get("receipts", [])
        if receipt.get("artifactKind") == "retrieval_evidence"
        for hit in (receipt.get("content") or {}).get("hits", [])
    }


def mentioned(value: Any, text: str) -> bool:
    """The value (a name or an id) appears in the text as a whole word or phrase, ignoring case."""
    if value is None or isinstance(value, bool) or not str(value).strip():
        return False
    return bool(re.search(rf"(?<!\w){re.escape(str(value).strip())}(?!\w)", text, re.IGNORECASE))


def evaluate(check: Check, text: str, turn: dict[str, Any], oracles: Oracles) -> bool:
    """One check of one report: True when the report passes it."""
    plain = EMPHASIS.sub("", text)  # the text checks read the words, not the markdown emphasis
    if check.kind == "mentions":
        values = RowRef.parse(check.value).values(oracles)
        hits = sum(mentioned(value, plain) for value in values)
        if not values:
            return False
        if check.any:
            return hits >= 1
        return hits >= (check.at_least if check.at_least is not None else len(values))
    if check.kind == "contains":
        return all(word.lower() in plain.lower() for word in check.value)
    if check.kind == "percent":
        values = RowRef.parse(check.value).values(oracles)
        return bool(values) and all(has_percent(text, value) for value in values)
    if check.kind == "pattern":
        patterns = check.value if isinstance(check.value, list) else [check.value]
        return any(re.search(pattern, plain) for pattern in patterns)
    if check.kind == "retrieved_source":
        return str(check.value) in retrieved_sources(turn)
    if check.kind == "percent_grounding":
        share = grounding(text, turn)
        return share is not None and share >= float(check.value)
    if check.kind == "prediction_available":
        return prediction_available(turn)
    raise ValueError(f"unknown check kind {check.kind}")
