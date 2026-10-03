# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The optional LLM grader: off unless GRADER_BASE_URL, GRADER_API_KEY and GRADER_MODEL are all set.

It grades each report blind, as the bake-off's answer judge did (docs/models-and-routing.md): it sees what the
data is (synthetic or not), the question, the reference facts (answers.yaml, filled from the oracles), a
digest of the run's receipts and the report, never the model or the route. GRADER_SAMPLES (default 3) independent
samples per run, majority vote. Any OpenAI-compatible chat completions endpoint works; use a frontier model, since a
smaller one misses unsupported claims and wrong units (eval/README.md). The key is read from the environment only and
never printed or written.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from collections.abc import Mapping
from typing import Any

from .client import HttpError
from .client import request_json

EVIDENCE_CHARS, RECEIPT_CHARS = 400_000, 40_000
CRITERIA = ("correctness", "grounding", "completeness", "honesty", "overall")

SYSTEM = """You grade reports written by an enterprise-knowledge assistant in a software demo. The agent answers \
one user question by calling read-only tools (retrieval over parsed documents such as policies, reports and \
slides; read-only SQL over tables in DuckDB; a Kumo prediction model that scores entities; and, optionally, Auto \
Ontology over the same tables) and then writes a cited report. The DATASET line says what the data is, for \
example that it is synthetic.

You receive the question, reference facts written by the evaluator, a digest of the tool evidence the agent \
actually received, and the agent's final report. Grade against the evidence and the reference facts, not your \
own knowledge. Do not penalize the report for omitting something the evidence does not contain, but do penalize \
any claim, number, date or entity that the evidence does not support. Kumo returns probabilities as fractions \
(0.22 is 22%) and the tables hold whatever units their columns say: a fraction shown as a percentage without \
scaling, or a number shown with the wrong unit, is a wrong number. The evidence is the receipt the agent's tools \
recorded: retrieved passages are cut at 1,500 characters there, while the agent read them in full. A detail \
attributed to a retrieved document whose snippet is cut off is unverifiable rather than fabricated: lower \
grounding by at most one point for it. Fail a report for claims the evidence contradicts or that no evidence \
could plausibly support.

Score each criterion from 1 (very poor) to 5 (excellent):
- correctness: agrees with the reference facts and the evidence; numbers, dates, units and rankings are right.
- grounding: every factual claim is traceable to the evidence; fabrication scores 1.
- completeness: answers every part of the question that the evidence can support.
- honesty: states real limits (missing data, truncated results, synthetic data, an unavailable prediction, no \
causality or forecasting where relevant) without refusing what the evidence supports.
Then give overall (1-5) and pass: true only if you would show this report to a customer at a product demo \
(no fabricated facts, the main parts of the question answered or honestly declared unavailable).

Reply with one JSON object and nothing else: {"correctness": 1-5, "grounding": 1-5, "completeness": 1-5, \
"honesty": 1-5, "overall": 1-5, "pass": true or false, "notes": "one or two sentences"}."""

SCHEMA = {
    "type": "object",
    "properties": {name: {"type": "integer"} for name in CRITERIA}
    | {"pass": {"type": "boolean"}, "notes": {"type": "string"}},
    "required": [*CRITERIA, "pass", "notes"],
    "additionalProperties": False,
}
ENV = ("GRADER_BASE_URL", "GRADER_API_KEY", "GRADER_MODEL")


class GraderConfigError(ValueError):
    """The grader is half configured."""


def evidence(turn: dict[str, Any]) -> str:
    """The run's receipts as the grader reads them: each cut at 40,000 characters, 400,000 in all."""
    parts, used = [], 0
    for receipt in (turn or {}).get("receipts", []):
        chunk = json.dumps(
            {
                "evidence_id": receipt.get("receiptId"),
                "tool": receipt.get("toolName"),
                "status": receipt.get("status"),
                "error": receipt.get("errorSummary"),
                "content": receipt.get("content"),
            },
            default=str,
        )[:RECEIPT_CHARS]
        if used + len(chunk) > EVIDENCE_CHARS:
            parts.append("[further evidence omitted]")
            break
        parts.append(chunk)
        used += len(chunk)
    return "\n".join(parts) or "(no tool evidence)"


def prompt(*, dataset: str, question: str, facts: str, run: dict[str, Any]) -> str:
    turn = run.get("turn") or {}
    report = (turn.get("report") or {}).get("markdown") or "(no report)"
    return (
        f"DATASET: {dataset or 'unknown'}\n\nQUESTION:\n{question}\n\nREFERENCE FACTS:\n{facts or '(none)'}\n\n"
        f"JOB STATUS: {(run.get('status') or {}).get('status')}\n\nTOOL EVIDENCE:\n{evidence(turn)}\n\n"
        f"REPORT:\n{report}"
    )


def parse_verdict(content: str) -> dict[str, Any] | None:
    """The grader's JSON object, checked; None when the reply is not one."""
    match = re.search(r"\{.*\}", content or "", re.S)
    if not match:
        return None
    try:
        verdict = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(verdict, dict) or not isinstance(verdict.get("pass"), bool):
        return None
    if not all(isinstance(verdict.get(name), int) and 1 <= verdict[name] <= 5 for name in CRITERIA):
        return None
    return {**{name: verdict[name] for name in CRITERIA}, "pass": verdict["pass"], "notes": str(verdict.get("notes"))}


class Grader:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        samples: int = 3,
        request: Callable[..., Any] = request_json,
    ) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self.model = model
        self.samples = max(1, samples)
        self._request = request
        self._structured = True  # json_schema output, until the endpoint refuses it
        self.errors: list[str] = []

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ, **options: Any) -> Grader | None:
        values = {name: (env.get(name) or "").strip() for name in ENV}
        if not any(values.values()):
            return None
        if missing := [name for name, value in values.items() if not value]:
            raise GraderConfigError(f"the grader needs {', '.join(ENV)}; missing {', '.join(missing)}")
        samples = int(env.get("GRADER_SAMPLES") or 3)
        return cls(
            values["GRADER_BASE_URL"], values["GRADER_API_KEY"], values["GRADER_MODEL"], samples=samples, **options
        )

    def _redact(self, text: str) -> str:
        return text.replace(self._key, "[redacted]") if self._key else text

    def _sample(self, user: str) -> dict[str, Any] | None:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        }
        headers = {"authorization": f"Bearer {self._key}"}
        for _ in range(2):
            if self._structured:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "grade", "schema": SCHEMA, "strict": True},
                }
            else:
                body.pop("response_format", None)
            try:
                answer = self._request("POST", self.url, body, headers=headers, timeout=600, attempts=4)
            except HttpError as error:
                if error.status == 400 and self._structured:
                    self._structured = False  # the endpoint has no structured output: ask in the prompt alone
                    continue
                self.errors.append(self._redact(str(error))[:300])
                return None
            try:
                content = answer["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                content = ""
            if verdict := parse_verdict(content if isinstance(content, str) else json.dumps(content)):
                return verdict
            self.errors.append("the grader's reply was not the requested JSON object")
        return None

    def grade(self, *, dataset: str, question: str, facts: str, run: dict[str, Any]) -> dict[str, Any] | None:
        """Majority vote of the samples; None when fewer than all samples came back."""
        user = prompt(dataset=dataset, question=question, facts=facts, run=run)
        samples = [verdict for verdict in (self._sample(user) for _ in range(self.samples)) if verdict]
        if len(samples) < self.samples:
            return None
        return {
            "model": self.model,
            "overall": round(sum(v["overall"] for v in samples) / len(samples), 2),
            "pass": sum(v["pass"] for v in samples) * 2 > len(samples),
            "samples": samples,
        }
