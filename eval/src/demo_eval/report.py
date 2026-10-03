# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The eval's report: a summary per question, then one row per run, as markdown (report.md and the console).

A run passes when its deterministic checks pass and, with the grader on, the grader's majority says pass. A run the
grader could not grade (an error, or fewer samples than asked) does not pass.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections import defaultdict
from typing import Any


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def passed(row: dict[str, Any]) -> bool:
    grade = row.get("grade")
    if row.get("graded"):
        return bool(row["det_pass"] and grade and grade.get("pass"))
    return bool(row["det_pass"])


def _grader_cell(rows: list[dict[str, Any]]) -> str:
    graded = [row for row in rows if row.get("graded")]
    if not graded:
        return "off"
    ok = sum(bool(row.get("grade") and row["grade"].get("pass")) for row in graded)
    means = [row["grade"]["overall"] for row in graded if row.get("grade")]
    mean = f", mean {statistics.mean(means):.1f}" if means else ""
    return f"{ok}/{len(graded)}{mean}"


def _seconds(rows: list[dict[str, Any]]) -> tuple[str, str]:
    walls = sorted(row["wall_seconds"] for row in rows if row.get("wall_seconds") is not None)
    if not walls:
        return "-", "-"
    return f"{statistics.median(walls):.0f}", f"{walls[-1]:.0f}"


def summary(rows: list[dict[str, Any]]) -> str:
    by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_question[row["qid"]].append(row)
    lines = []
    for qid, group in [*by_question.items(), ("all", rows)]:
        failed = Counter(name for row in group for name in row["failed_checks"])
        p50, worst = _seconds(group)
        lines.append(
            [
                f"**{qid}**" if qid == "all" else qid,
                len(group),
                f"{sum(passed(row) for row in group)}/{len(group)}",
                f"{sum(row['det_pass'] for row in group)}/{len(group)}",
                _grader_cell(group),
                f"{sum(row['citations'] >= 1 for row in group)}/{len(group)}",
                f"{sum(row['tool_calls'] for row in group)} / {sum(row['tool_errors'] for row in group)}"
                f" / {sum(row['duplicate_calls'] for row in group)}",
                p50,
                worst,
                ", ".join(f"{name} ({count})" for name, count in sorted(failed.items())) or "-",
            ]
        )
    headers = ["Question", "Runs", "Pass", "Deterministic", "Grader", "Cited", "Tool calls / errors / dups"]
    return table([*headers, "p50 s", "max s", "Failed checks"], lines)


def runs_table(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        grade = row.get("grade")
        grader = "-" if not row.get("graded") else "error" if not grade else ("pass" if grade["pass"] else "fail")
        if grade:
            grader += f" ({grade['overall']})"
        lines.append(
            [
                row["qid"],
                row["run"],
                f"`{row['job_id']}`" if row.get("job_id") else "-",
                row["status"],
                f"{row['wall_seconds']:.0f}" if row.get("wall_seconds") is not None else "-",
                "PASS" if passed(row) else "FAIL",
                "pass" if row["det_pass"] else "fail",
                grader,
                ", ".join(row["failed_checks"]) or "-",
                ", ".join(f"{model} x{n}" for model, n in sorted(row["served_models"].items())) or "-",
            ]
        )
    headers = ["Question", "Run", "Job", "Status", "Wall s", "Result", "Deterministic", "Grader", "Failed checks"]
    return table([*headers, "Served models"], lines)


def markdown(rows: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    total = sum(passed(row) for row in rows)
    lines = [
        f"# Answer-quality eval: {meta.get('pack')}",
        "",
        f"- Deployment: {meta.get('deployment')}",
        f"- Started: {meta.get('started_at')}",
        f"- Questions: {', '.join(meta.get('questions') or [])}; {meta.get('runs')} run(s) each",
        f"- Grader: {meta.get('grader') or 'off (deterministic checks only)'}",
        f"- Result: {total} of {len(rows)} runs pass",
        "",
        "## Summary",
        "",
        summary(rows),
        "",
        "## Runs",
        "",
        runs_table(rows),
    ]
    notes = [
        (row["qid"], row["run"], note)
        for row in rows
        if row.get("grade")
        for note in [s.get("notes") for s in row["grade"]["samples"]][:1]
    ]
    if notes:
        lines += ["", "## Grader notes (first sample)", ""]
        lines += [f"- {qid}.{run}: {note}" for qid, run, note in notes]
    return "\n".join(lines) + "\n"
