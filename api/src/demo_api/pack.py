# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The pack contracts: ``PackView`` (``GET /v1/pack``, ``contracts/schemas/pack.schema.json``) and ``PackList``
(``GET /v1/packs``, ``contracts/schemas/packs.schema.json``).

``catalog.py`` builds them from the pack manifests the ingest service writes: a pack offers the questions whose
sources and tools the running stack serves, and among them the examples of the composer's picker.
"""

from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

# The most examples the composer's picker offers
MAX_EXAMPLES = 12


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_serialization_defaults_required=True)


class PackQuestionView(_View):
    """A demo question whose sources this stack serves."""

    id: str
    label: str
    tag: str | None = None
    description: str | None = None
    question: str
    sources: list[str]
    tools: list[str] = Field(
        default_factory=list,
        description="The tools it is expected to use, as pills (Pill in contracts/tool-registry.schema.json).",
    )
    featured: bool = Field(default=False, description="Shown on the landing page.")


class PackConversationView(_View):
    """A multi-turn conversation that `demo-api record` asks; not listed in the UI."""

    id: str
    label: str
    tag: str | None = None
    description: str | None = None
    sources: list[str]
    turns: list[str]


PackKind = Literal["industry", "workspace"]
PackStatus = Literal["ready", "ingesting", "failed", "empty"]


class PackSummary(_View):
    """One pack a user can pick: an industry, or the workspace of their own uploads (``kind: workspace``)."""

    id: str
    kind: PackKind
    title: str
    description: str | None = None
    icon: str | None = Field(default=None, description="An icon name from ui/src/adapters/ui/icons.tsx.")
    status: PackStatus = Field(
        description="ingesting while some of its files are in the pipeline; empty before it has any source."
    )


class PackList(_View):
    """``GET /v1/packs``: the packs a user can pick, the industries by title, then the workspace."""

    packs: list[PackSummary]


class PackView(PackSummary):
    """``GET /v1/pack``: a pack's summary, version, disclaimer, questions, examples and conversations."""

    version: str | None = None
    as_of: str | None = None
    disclaimer: str | None = None
    questions: list[PackQuestionView]
    examples: list[str] = Field(
        max_length=MAX_EXAMPLES,
        description=(
            "The ids of the questions the composer's example picker offers, in order: the pack's `examples` "
            "(questions.yaml), or else its featured questions and then the others, at most 12, all among `questions`."
        ),
    )
    conversations: list[PackConversationView]


def picker_examples(questions: list[dict[str, Any]], declared: list[str] | None) -> list[str]:
    """The ids of the picker's examples among ``questions``, at most ``MAX_EXAMPLES``.

    The declared ones in their order, or without a list the featured questions and then the others.
    """
    offered = [question["id"] for question in questions]
    if declared is None:
        featured = [question["id"] for question in questions if question.get("featured")]
        declared = featured + [question_id for question_id in offered if question_id not in featured]
    return [question_id for question_id in declared if question_id in offered][:MAX_EXAMPLES]
