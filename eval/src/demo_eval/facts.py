# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The reference facts the optional grader reads: a question's `facts` template with its oracle rows filled in.

`{top_tiers[:3]: tier, net_revenue}` becomes "tier=gold, net_revenue=395.000; tier=...". A fraction in a
`format.signed_percent` field shows as a signed percentage, one in `format.percent` as a percentage, and any other
float with three decimals.
"""

from __future__ import annotations

from typing import Any

from .spec import FACT
from .spec import AnswerSpec
from .spec import QuestionSpec
from .spec import select_rows


def value(spec: AnswerSpec, field: str, x: Any) -> Any:
    if isinstance(x, int | float) and not isinstance(x, bool):
        if field in spec.signed_percent:
            return f"{x * 100:+.2f}%"
        if field in spec.percent:
            return f"{x * 100:.2f}%"
        if isinstance(x, float):
            return f"{x:.3f}"
    return x


def render(spec: AnswerSpec, question: QuestionSpec, oracles: dict[str, list[dict[str, Any]]]) -> str:
    def rows(match: Any) -> str:
        fields = [field.strip() for field in match["fields"].split(",")]
        selected = select_rows(oracles.get(match["oracle"]) or [], match["rows"] or "")
        return "; ".join(
            ", ".join(f"{field}={value(spec, field, row[field])}" for field in fields if field in row)
            for row in selected
        )

    return FACT.sub(rows, question.facts)
