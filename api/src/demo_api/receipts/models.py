# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Typed, display-safe receipts for observed tool results.

The Hermes plugin builds one receipt per registered tool call and posts it to
``POST /internal/hermes/jobs/{job_id}/tool-receipts``. The API stores it and serves
it to the UI unchanged. ``receiptId`` is also the evidence id the agent cites, and
execution events point at it through ``artifactRefs``.

``ReceiptV2`` is a union discriminated by ``artifactKind``. Every tool in
``contracts/tool-registry.json`` names exactly one kind as its ``receipt_kind``, so
the plugin knows the kind before the call returns. Each content model mirrors the
result its tool returns, bounded for display, so the plugin copies rather than
derives. A completed receipt always carries content. A failed receipt carries
whatever bounded content the tool returned, or none.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from typing import Literal

from pydantic import AwareDatetime
from pydantic import Field
from pydantic import NonNegativeInt
from pydantic import StringConstraints
from pydantic import field_validator
from pydantic import model_validator

from demo_api.events.models import MAX_JSON_ITEMS
from demo_api.events.models import ContractModel
from demo_api.events.models import CorrelationIdentifier
from demo_api.events.models import JsonValue
from demo_api.events.models import OpenIdentifier
from demo_api.events.models import normalize_aware_datetime
from demo_api.events.models import validate_bounded_display_json
from demo_api.events.models import validate_display_text

ReceiptStatus = Literal["completed", "failed"]
ArtifactKind = Literal["retrieval_evidence", "structured_query", "structured_prediction"]

TraceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
SpanId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")]
CollectionName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,254}$")]
Row = dict[str, JsonValue]

MAX_ROWS = 25
MAX_HITS = 25  # retrieve_evidence's largest top_k


class RetrievalHit(ContractModel):
    """One passage from `retrieve_evidence`, ordered by `score`: its rerank logit, or its vector score (cosine)
    when the retrieval ran without a rerank model."""

    rank: int = Field(ge=1)
    score: float
    vector_score: float
    source_id: OpenIdentifier
    document_id: str = Field(min_length=1, max_length=512)
    chunk_id: str = Field(min_length=1, max_length=512)
    title: str = Field(min_length=1, max_length=1_000)
    url: str | None = Field(default=None, max_length=2_048)
    published_at: AwareDatetime | None = None
    snippet: str = Field(max_length=1_500)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class RetrievalModels(ContractModel):
    """The models behind a retrieval; `rerank` is null when none is configured and the hits keep vector order."""

    embed: str = Field(min_length=1, max_length=256)
    rerank: str | None = Field(min_length=1, max_length=256)


class RetrievalIndex(ContractModel):
    type: str = Field(min_length=1, max_length=64)
    metric: str = Field(min_length=1, max_length=64)
    params: dict[str, int]
    search_params: dict[str, int]


class RetrievalTimings(ContractModel):
    """Wall-clock stages of one retrieval, in milliseconds; `rerank_ms` is 0 when it ran without a rerank model."""

    embed_ms: float = Field(ge=0)
    search_ms: float = Field(ge=0)
    rerank_ms: float = Field(ge=0)
    total_ms: float = Field(ge=0)


class RetrievalEvidence(ContractModel):
    """A `retrieve_evidence` result: per-source vector search, one merged rerank (when a rerank model is
    configured), the best hits.

    `collection` is the alias the tool searched; the collection version is the index build behind it.
    """

    query: str = Field(min_length=1, max_length=4_000)
    source_ids: tuple[OpenIdentifier, ...] = Field(min_length=1, max_length=32)
    collection: CollectionName
    collection_version: CollectionName
    hits: tuple[RetrievalHit, ...] = Field(max_length=MAX_HITS)
    candidate_counts: dict[str, NonNegativeInt]
    models: RetrievalModels
    index: RetrievalIndex
    timings: RetrievalTimings

    @model_validator(mode="after")
    def _validate_hits(self) -> RetrievalEvidence:
        if any(hit.source_id not in self.source_ids for hit in self.hits):
            raise ValueError("every hit must come from a selected source")
        if len(self.hits) > sum(self.candidate_counts.values()):
            raise ValueError("hits cannot outnumber the candidates they were ranked from")
        return self


class LineageBinding(ContractModel):
    """How Auto Ontology bound one phrase of the question to a column."""

    phrase: str = Field(min_length=1, max_length=500)
    ontology_object: str = Field(min_length=1, max_length=256)
    table: str = Field(min_length=1, max_length=256)
    column: str = Field(min_length=1, max_length=256)


class StructuredQuery(ContractModel):
    """A SQL result over structured sources: `query_tables` (DuckDB) or Auto Ontology `ask_question`.

    `query` is the question the SQL answers, and `database_name` the database it ran on: the DuckDB alias of the
    one structured source `query_tables` attached (`knowledge` when it attached several), or Auto Ontology's
    database. `answer` and `resolution_lineage` come from Auto Ontology only. `sql` is null when Auto Ontology
    resolved the question's terms but could not construct a query.
    """

    query: str = Field(min_length=1, max_length=1_000)
    database_name: OpenIdentifier
    answer: str | None = Field(default=None, max_length=4_000)
    sql: str | None = Field(default=None, max_length=12_000)
    rows: tuple[Row, ...] = Field(default=(), max_length=MAX_ROWS)
    source_row_count: int = Field(ge=0)
    truncated: bool
    resolution_lineage: tuple[LineageBinding, ...] = Field(default=(), max_length=40)


class PredictionHorizon(ContractModel):
    """How far past the anchor time the outcome is counted, read from the PQL window."""

    value: int
    unit: str = Field(min_length=1, max_length=32)


class EntityPrediction(ContractModel):
    """One entity's predicted outcome: `probability` for a binary task (or the score of a multiclass `label`),
    `value` for a regression."""

    entity_id: OpenIdentifier
    probability: float | None = Field(default=None, ge=0, le=1)
    value: float | None = None
    label: str | None = Field(default=None, max_length=256)


class StructuredPrediction(ContractModel):
    """A PQL query scored per entity by NVIDIA Kumo (`predict`) over one structured source.

    `template_id` names the source's prediction template when the PQL came from one. `task_type` is the task Kumo
    ran (binary, regression, multiclass), `anchor_time` the time predictions are made from, `horizon` the PQL
    window, and `entity_table` the table of the `FOR EACH` entities, when known. `available` is false, with a
    `reason`, when the prediction could not run.
    """

    available: bool
    reason: str | None = Field(default=None, max_length=500)
    source_id: OpenIdentifier
    template_id: OpenIdentifier | None = None
    pql: str = Field(min_length=1, max_length=8_000)
    task_type: str | None = Field(default=None, max_length=64)
    anchor_time: AwareDatetime | None = None
    horizon: PredictionHorizon | None = None
    entity_table: str | None = Field(default=None, max_length=256)
    rows: tuple[EntityPrediction, ...] = Field(default=(), max_length=MAX_JSON_ITEMS)
    model: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _validate_availability(self) -> StructuredPrediction:
        if self.available == (self.reason is not None):
            raise ValueError("a reason is required exactly when the prediction is unavailable")
        if not self.available and self.rows:
            raise ValueError("an unavailable prediction has no rows")
        return self


class ReceiptBase(ContractModel):
    """Fields every receipt shares; each variant narrows ``artifact_kind`` and ``content``."""

    schema_version: Literal["2"] = "2"
    artifact_kind: ArtifactKind
    receipt_id: CorrelationIdentifier
    job_id: CorrelationIdentifier
    invocation_id: CorrelationIdentifier
    turn_id: CorrelationIdentifier | None = None
    tool_name: OpenIdentifier
    status: ReceiptStatus
    error_type: OpenIdentifier | None = None
    error_summary: str | None = Field(default=None, max_length=600)
    duration_ms: int = Field(ge=0, le=86_400_000)
    trace_id: TraceId | None = None
    span_id: SpanId | None = None
    occurred_at: datetime
    content: ContractModel | None = None

    @field_validator("error_summary")
    @classmethod
    def _validate_error_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_display_text(value, field_name="error_summary", max_chars=600)

    @field_validator("occurred_at")
    @classmethod
    def _validate_occurred_at(cls, value: datetime) -> datetime:
        return normalize_aware_datetime(value, field_name="occurred_at")

    @model_validator(mode="after")
    def _validate_receipt(self) -> ReceiptBase:
        if self.status == "completed" and (self.content is None or self.error_summary is not None):
            raise ValueError("a completed receipt requires content and no error summary")
        if (self.trace_id is None) != (self.span_id is None):
            raise ValueError("trace_id and span_id must be provided together")
        if self.trace_id == "0" * 32 or self.span_id == "0" * 16:
            raise ValueError("trace_id and span_id must be non-zero")
        if self.content is not None:
            validate_bounded_display_json(self.content.model_dump(mode="json"), field_name="content")
        return self


class RetrievalEvidenceReceipt(ReceiptBase):
    artifact_kind: Literal["retrieval_evidence"]
    content: RetrievalEvidence | None = None


class StructuredQueryReceipt(ReceiptBase):
    artifact_kind: Literal["structured_query"]
    content: StructuredQuery | None = None


class StructuredPredictionReceipt(ReceiptBase):
    artifact_kind: Literal["structured_prediction"]
    content: StructuredPrediction | None = None

    @model_validator(mode="after")
    def _validate_prediction(self) -> StructuredPredictionReceipt:
        if self.status == "completed" and self.content is not None and not self.content.rows:
            raise ValueError("a completed prediction requires scored rows")
        return self


ReceiptV2 = Annotated[
    RetrievalEvidenceReceipt | StructuredQueryReceipt | StructuredPredictionReceipt,
    Field(discriminator="artifact_kind"),
]

__all__ = [
    "ArtifactKind",
    "EntityPrediction",
    "LineageBinding",
    "PredictionHorizon",
    "ReceiptBase",
    "ReceiptStatus",
    "ReceiptV2",
    "RetrievalEvidence",
    "RetrievalEvidenceReceipt",
    "RetrievalHit",
    "RetrievalIndex",
    "RetrievalModels",
    "RetrievalTimings",
    "StructuredPrediction",
    "StructuredPredictionReceipt",
    "StructuredQuery",
    "StructuredQueryReceipt",
]
