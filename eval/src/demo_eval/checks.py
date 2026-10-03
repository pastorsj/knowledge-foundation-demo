# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deterministic checks of one report: its text against the oracle rows and against the run's own evidence.

A run is the job's export (`GET /v1/jobs/async/job/{id}/export`): the report, the `execution.v2` events and the
receipts. None of this needs a grader. The checks look for what a correct answer must contain (the oracle's
strongest asset, its return as a percentage, a required caveat) and never judge style.
"""

from __future__ import annotations

import re
from typing import Any

from .deadline import deadline_ok
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
# Markdown emphasis: a pattern matches the words, so "**not** forecasts" reads as "not forecasts"
EMPHASIS = re.compile(r"\*+")

Oracles = dict[str, list[dict[str, Any]]]


def report_text(turn: dict[str, Any]) -> str:
    """The report's markdown, with typographic dashes and minus signs as "-"."""
    return DASHES.sub("-", ((turn or {}).get("report") or {}).get("markdown") or "")


def body(text: str) -> str:
    """The report before its Sources list."""
    return SOURCES.split(text)[0]


def named(asset_id: Any, text: str, names: dict[str, str]) -> bool:
    """The asset appears by its id (the ticker) or its company name."""
    if not asset_id:
        return False
    asset = str(asset_id)
    name = names.get(asset)
    return bool(re.search(rf"\b{re.escape(asset)}\b", text) or (name and name.lower() in text.lower()))


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


def prediction_top(turn: dict[str, Any], n: int) -> list[str]:
    """The ``n`` most probable assets of the run's first completed Kumo prediction."""
    for receipt in (turn or {}).get("receipts", []):
        if receipt.get("artifactKind") == "structured_prediction" and receipt.get("status") == "completed":
            rows = sorted((receipt.get("content") or {}).get("rows", []), key=lambda row: -row["probability"])
            return [row["assetId"] for row in rows[:n]]
    return []


def retrieved_sources(turn: dict[str, Any]) -> set[str]:
    return {
        hit.get("sourceId")
        for receipt in (turn or {}).get("receipts", [])
        if receipt.get("artifactKind") == "retrieval_evidence"
        for hit in (receipt.get("content") or {}).get("hits", [])
    }


def retrieved_filings(turn: dict[str, Any]) -> set[str]:
    """The filings (<cik>:<accession>) that the run's retrieval hits come from: edgar:<cik>:<accession>:<file>."""
    return {
        ":".join(parts[1:3])
        for receipt in (turn or {}).get("receipts", [])
        if receipt.get("artifactKind") == "retrieval_evidence"
        for hit in (receipt.get("content") or {}).get("hits", [])
        if (parts := str(hit.get("documentId") or "").split(":"))[0] == "edgar" and len(parts) >= 4
    }


def first_named(text: str, candidates: list[str]) -> str | None:
    """Which of ``candidates`` the report's body names first, by ticker."""
    positions = [(m.start(), c) for c in candidates for m in re.finditer(rf"\b{re.escape(c)}\b", body(text))]
    return min(positions)[1] if positions else None


def evaluate(check: Check, text: str, turn: dict[str, Any], oracles: Oracles, names: dict[str, str]) -> bool:
    """One check of one report: True when the report passes it."""
    if check.kind == "named":
        assets = RowRef.parse(check.value).values(oracles)
        hits = sum(named(asset, text, names) for asset in assets)
        if not assets:
            return False
        if check.any:
            return hits >= 1
        return hits >= (check.at_least if check.at_least is not None else len(assets))
    if check.kind == "percent":
        values = RowRef.parse(check.value).values(oracles)
        return bool(values) and all(has_percent(text, value) for value in values)
    if check.kind == "pattern":
        patterns = check.value if isinstance(check.value, list) else [check.value]
        plain = EMPHASIS.sub("", text)
        return any(re.search(pattern, plain) for pattern in patterns)
    if check.kind == "item_105_deadline":
        return deadline_ok(text)
    if check.kind == "retrieved_source":
        return str(check.value) in retrieved_sources(turn)
    if check.kind == "retrieved_filing":
        return not retrieved_filings(turn).isdisjoint(check.value)
    if check.kind == "percent_grounding":
        share = grounding(text, turn)
        return share is not None and share >= float(check.value)
    if check.kind == "prediction_named":
        top = prediction_top(turn, int(check.value))
        return bool(top) and all(named(asset, text, names) for asset in top)
    if check.kind == "prediction_first":
        top = prediction_top(turn, 3)
        return bool(top) and first_named(text, top) == top[0]
    raise ValueError(f"unknown check kind {check.kind}")
