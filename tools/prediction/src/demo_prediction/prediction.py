# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""predict: run a PQL query on NVIDIA Kumo Relational over one structured source of the knowledge catalog.

Ported from tools/market-analytics (`Predictor`, the window parser, one attempt within 60 s). The source is the one
selected source whose tables include every table the query names. Its graph is built on each call from its DuckDB
file, read-only, with the catalog's primary keys, time columns and foreign keys (`TableInfo`). Kumo scores up to
`KUMO_MAX_ENTITIES` entities of the entity table (its per-request limit), in primary-key order, filtered by the
query's entity filter when that filter is a plain condition on the entity table (entities.py); the result lists the
`MAX_ROWS` highest, within the 30,000-character result budget.

Without KUMO_RELATIONAL_URL, or when the endpoint fails, the result says so (`available: false` and a written
reason) rather than raising: the agent reports it instead of retrying.
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd
import pydantic_core
from kumo_relational_client import NimRequestError
from kumo_relational_client import RelationalClient
from kumo_relational_client import RelationalError
from kumo_relational_client import relational
from openinference.semconv.trace import OpenInferenceSpanKindValues as SpanKind
from openinference.semconv.trace import SpanAttributes
from opentelemetry import trace
from pydantic import BaseModel
from pydantic import Field

from . import catalog
from . import entities
from .pql import Horizon
from .pql import PqlError
from .pql import Query
from .pql import parse
from .settings import Settings

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

MODEL = "kumo-relational"
# One attempt and no retries, so a slow or warming NIM yields `available=false` well inside the agent's MCP
# timeout rather than a transport error with no receipt.
TIMEOUT_SECONDS = 60.0
KUMO_MAX_ENTITIES = 1000  # Kumo Relational's documented limit of entities per prediction request
MAX_ROWS = 25
MAX_TEXT_CHARS = 200  # entity ids and class labels
MAX_PQL_CHARS = 2000  # the tool's own limit, and a template's (contracts/catalog/source-manifest.schema.json)
MAX_WARNINGS = 10
MAX_WARNING_CHARS = 500
# 60% of Hermes' 50,000-character limit for one MCP result, as the agent reads it (tools/retrieval/budget.py).
MAX_RESULT_CHARS = 30_000
EVIDENCE_ID = "hermes-receipt:" + "0" * 64
TEMPLATE_PREFIX = "template:"
NO_ENDPOINT = "No Kumo endpoint is configured (KUMO_RELATIONAL_URL)."
UNREACHABLE = "The Kumo endpoint could not be reached (KUMO_RELATIONAL_URL); it may be down or still starting."
TIMED_OUT = f"The Kumo endpoint did not answer within {TIMEOUT_SECONDS:g} seconds."
REFUSED_KEY = "The Kumo endpoint refused the credentials (KUMO_API_KEY)."
UNEXPECTED = "The prediction failed unexpectedly; the prediction service's log has the details."
URL = re.compile(r"https?://\S+")
PATH = re.compile(r"(?<![\w.])/(?:[\w.-]+/)+[\w.-]*")
# An aggregate over a window, which only Kumo evaluates (it is relative to the anchor time).
TEMPORAL = re.compile(r"\(\s*[^()]*,\s*-?\d+\s*,\s*-?\d+\s*,\s*[a-z]+\s*\)", re.IGNORECASE)


class PredictionError(ValueError):
    """A request the tool refuses. The message is written here and safe to show the agent."""


class EntityPrediction(BaseModel):
    entity_id: str
    probability: float | None = Field(
        default=None, description="Binary: the outcome's probability. Multiclass and ranking: the label's score"
    )
    value: float | None = Field(default=None, description="Regression: the predicted value")
    label: str | None = Field(default=None, description="Multiclass and ranking: the predicted class or item")


class PredictionResult(BaseModel):
    available: bool = Field(description="False when the prediction could not run; `reason` says why")
    reason: str | None = None
    source_id: str
    template_id: str | None = None
    pql: str = Field(description="The query that ran (a template's own query when one was named)")
    task_type: str | None = Field(
        default=None, description="binary_classification, regression, multiclass_classification, ..."
    )
    anchor_time: str | None = Field(
        default=None, description="Where the prediction starts (UTC); null: the latest timestamp in the data"
    )
    horizon: Horizon | None = Field(default=None, description="How far past the anchor the outcome is counted")
    entity_table: str | None = None
    rows: list[EntityPrediction] = Field(default_factory=list, description="Highest probability or value first")
    model: str = MODEL
    elapsed_ms: float = 0.0
    warnings: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class Plan:
    source: catalog.Source
    template_id: str | None
    pql: str
    query: Query
    entity: catalog.TableInfo
    anchor: pd.Timestamp | None  # naive UTC, as the sources' timestamps


class Predictor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def predict(self, pql: str, source_ids: list[str], anchor_time: str | None = None) -> PredictionResult:
        """Raises PredictionError for a request it refuses; every other failure is an unavailable result."""
        started = time.perf_counter()
        plan = self.plan(pql, source_ids, anchor_time)
        warnings: list[str] = []

        def result(
            rows: list[EntityPrediction] | None = None, reason: str | None = None, task_type: str | None = None
        ) -> PredictionResult:
            return PredictionResult(
                available=reason is None,
                reason=reason,
                source_id=plan.source.id,
                template_id=plan.template_id,
                pql=plan.pql,
                task_type=task_type or plan.query.task_type,
                anchor_time=None if plan.anchor is None else plan.anchor.isoformat() + "Z",
                horizon=plan.query.horizon,
                entity_table=plan.entity.name,
                rows=rows or [],
                elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                warnings=[_clip(warning, MAX_WARNING_CHARS) for warning in warnings[:MAX_WARNINGS]],
            )

        if not self.settings.kumo_url:
            return result(reason=NO_ENDPOINT)
        attributes = {SpanAttributes.OPENINFERENCE_SPAN_KIND: SpanKind.TOOL.value, SpanAttributes.INPUT_VALUE: plan.pql}
        with tracer.start_as_current_span("kumo", attributes=attributes) as span:
            try:
                ids = None
                if plan.query.for_each:
                    ids, notes = entity_ids(plan.source, plan.entity, plan.query.entity_filter)
                    warnings += notes
                graph, notes = build_graph(plan.source)
                warnings += notes
            except Exception as error:  # the source's file or catalog entry does not make a graph
                logger.warning("the graph of %s could not be built", plan.source.id, exc_info=True)
                span.set_attribute("error.type", type(error).__name__)
                reason = f"The tables of {plan.source.id} could not be read as a Kumo graph: {_redact(error)}"
                return result(reason=reason)
            try:
                with RelationalClient(
                    url=self.settings.kumo_url,
                    api_key=self.settings.kumo_api_key,
                    timeout=TIMEOUT_SECONDS,
                    max_retries=0,
                ) as client:
                    frame = client.relational(graph).predict(
                        plan.pql, ids, anchor_time=plan.anchor, run_mode="fast", num_retries=0, verbose=False
                    )
                rows, task_type = to_rows(frame)
            except Exception as error:  # the NIM is down, still warming up, or rejected the query
                logger.warning("Kumo prediction over %s failed", plan.source.id, exc_info=True)
                span.set_attribute("error.type", type(error).__name__)
                return result(reason=kumo_reason(error))
            span.set_attribute("output.row_count", len(rows))
        if len(rows) > MAX_ROWS:
            warnings.append(f"Showing the {MAX_ROWS} highest of {len(rows)} predictions.")
        return fit(result(rows=rows[:MAX_ROWS], task_type=task_type))

    def plan(self, pql: str, source_ids: list[str], anchor_time: str | None) -> Plan:
        try:
            sources = catalog.structured_sources(self.settings.knowledge_dir, source_ids)
        except catalog.CatalogError as error:
            raise PredictionError(str(error)) from error
        template_id = template_anchor = None
        text = pql.strip()
        try:
            if text.startswith(TEMPLATE_PREFIX):
                template_id = text.removeprefix(TEMPLATE_PREFIX).strip()
                source, template = _template(sources, template_id)
                text, template_anchor = template.pql, template.anchor_time
                if len(text) > MAX_PQL_CHARS:
                    raise PredictionError(f"The template {template_id!r} is longer than {MAX_PQL_CHARS} characters.")
                query = parse(text)
                _require_tables(source, query)
            else:
                query = parse(text)
                source = _resolve(sources, query)
        except PqlError as error:
            raise PredictionError(str(error)) from error
        entity = source.table(query.entity_table)
        assert entity is not None  # every table the query names is one of the source's
        if entity.primary_key is None or entity.primary_key.casefold() != query.entity_column.casefold():
            key = "has no primary key" if entity.primary_key is None else f"has the primary key {entity.primary_key}"
            raise PredictionError(
                f"Kumo predicts per primary key, and {entity.name} {key}: write FOR EACH <table>.<its primary key>."
            )
        return Plan(
            source=source,
            template_id=template_id,
            pql=text,
            query=query,
            entity=entity,
            anchor=_timestamp(anchor_time or template_anchor),
        )


def build_graph(source: catalog.Source) -> tuple[relational.Graph, list[str]]:
    """The source's graph: every table with its catalog keys and time column, linked by the catalog's foreign keys."""
    graph = relational.Graph.from_duckdb(
        connection={"uri": str(source.path), "kwargs": {"read_only": True}},
        tables=[
            {"name": table.name, "primary_key": table.primary_key, "time_column": table.time_column}
            for table in source.tables
        ],
        edges=[],  # the catalog's foreign keys are the links, and only they: no inferred ones
        verbose=False,
    )
    # Metadata inference also guesses keys and time columns (customers.joined_at as a time column, say), which
    # changes what the model sees; the catalog's own say, null included, is the one that holds.
    for table in source.tables:
        node = graph[table.name]
        node.time_column = node.end_time_column = None
        node.primary_key = table.primary_key
        node.time_column = table.time_column
    warnings = []
    for table in source.tables:
        for key in table.foreign_keys:
            target = source.table(key.references_table)
            if target is None or target.primary_key != key.references_column:
                warnings.append(
                    f"The link {table.name}.{key.column} -> {key.references_table}.{key.references_column} is left "
                    "out of the graph: Kumo links a foreign key to its table's primary key."
                )
                continue
            graph.link(table.name, key.column, target.name)
    graph.validate()
    return graph, warnings


def entity_ids(source: catalog.Source, entity: catalog.TableInfo, condition: str | None) -> tuple[list[Any], list[str]]:
    """Up to KUMO_MAX_ENTITIES primary keys of the entity table, in order, that satisfy the query's entity filter.

    Kumo scores every id it is given, whatever the filter says, so a plain condition on the entity table is applied
    here (entities.py). A filter over time (an aggregate with a window) only Kumo can evaluate, and a filter the tool
    cannot apply is reported, not run.
    """
    warnings = []
    key = entity.primary_key or ""
    where = None
    if condition and TEMPORAL.search(condition):
        warnings.append(_unfiltered(condition, "it aggregates over time, which only Kumo evaluates"))
    elif condition:
        try:
            where = entities.condition_sql(condition, entity.name, [column.name for column in entity.columns])
        except entities.FilterRejected as error:
            warnings.append(_unfiltered(condition, str(error)))
    try:
        ids, population = entities.select(source.path, entity.name, key, where, KUMO_MAX_ENTITIES)
    except entities.SelectionFailed as error:
        if where is None:
            raise
        warnings.append(_unfiltered(condition or "", _redact(error)))
        ids, population = entities.select(source.path, entity.name, key, None, KUMO_MAX_ENTITIES)
        where = None
    if population > KUMO_MAX_ENTITIES:
        passing = " that pass the filter" if where else ""
        warnings.insert(
            0,
            f"{entity.name} has {population:,} entities{passing}, and Kumo scores at most {KUMO_MAX_ENTITIES:,} per "
            f"request: these predictions cover the first {KUMO_MAX_ENTITIES:,} by {key}. Narrow the population with "
            f"FOR EACH {entity.name}.{key} WHERE <a condition on {entity.name}>.",
        )
    return ids, warnings


def kumo_reason(error: Exception) -> str:
    """A written reason for a failed Kumo call: what happened and what to check, without URLs or paths."""
    message = getattr(error, "message", str(error))
    if isinstance(error, NimRequestError):
        if error.status_code in (401, 403) or error.code == "AUTHENTICATION_FAILED":
            return REFUSED_KEY
        if 400 <= error.status_code < 500 and error.status_code not in (408, 429):
            return f"Kumo rejected the query: {_redact(message)}"
        if error.status_code == 408:
            return TIMED_OUT
        return f"The Kumo endpoint failed to answer (HTTP {error.status_code}); try again later."
    if isinstance(error, RelationalError):
        if error.code == "TRANSPORT_ERROR":
            return TIMED_OUT if re.search(r"timed? ?out", message, re.IGNORECASE) else UNREACHABLE
        if error.code == "AUTHENTICATION_FAILED":
            return REFUSED_KEY
        if error.code == "INVALID_REQUEST":
            return f"Kumo rejected the query: {_redact(message)}"
        return f"Kumo could not make the prediction ({error.code or 'no code'})."
    if isinstance(error, TimeoutError):
        return TIMED_OUT
    if isinstance(error, ConnectionError):
        return UNREACHABLE
    return UNEXPECTED


def agent_chars(result: PredictionResult) -> int:
    """The result's length as the agent reads it: indented JSON text, in a JSON string, after the evidence id."""
    text = pydantic_core.to_json(result, fallback=str, indent=2).decode()
    return len(json.dumps({"evidence_id": EVIDENCE_ID, "result": text}, ensure_ascii=False))


def fit(result: PredictionResult) -> PredictionResult:
    """The result without its lowest rows while it is longer than MAX_RESULT_CHARS as the agent reads it."""
    if agent_chars(result) <= MAX_RESULT_CHARS:
        return result
    total = len(result.rows)
    for count in range(total - 1, -1, -1):  # at most MAX_ROWS steps
        warning = f"Only the {count} highest of {total} rows fit in {MAX_RESULT_CHARS:,} characters."
        fitted = result.model_copy(update={"rows": result.rows[:count], "warnings": [*result.warnings, warning]})
        if agent_chars(fitted) <= MAX_RESULT_CHARS:
            return fitted
    return fitted


def to_rows(frame: pd.DataFrame) -> tuple[list[EntityPrediction], str]:
    """Kumo's predictions as rows, highest probability or value first, and the task type their columns show."""
    columns = set(frame.columns)
    if "TRUE_PROB" in columns:
        task_type = "binary_classification"
        rows = [
            EntityPrediction(entity_id=_text(entity), probability=_number(probability))
            for entity, probability in zip(frame["ENTITY"], frame["TRUE_PROB"], strict=True)
        ]
    elif {"CLASS", "SCORE"} <= columns:
        if "PREDICTED" in columns:  # one row per entity and class: keep each entity's best class
            task_type = "multiclass_classification"
            frame = frame.sort_values("SCORE", ascending=False, kind="stable").drop_duplicates("ENTITY")
        else:  # RANK TOP k: k rows per entity, all kept
            task_type = "temporal_link_prediction"
        rows = [
            EntityPrediction(entity_id=_text(entity), probability=_number(score), label=_text(label))
            for entity, label, score in zip(frame["ENTITY"], frame["CLASS"], frame["SCORE"], strict=True)
        ]
    elif "PREDICTION" in columns:
        task_type = "regression"
        rows = [
            EntityPrediction(entity_id=_text(entity), value=_number(value))
            for entity, value in zip(frame["ENTITY"], frame["PREDICTION"], strict=True)
        ]
    else:
        raise ValueError(f"Kumo returned columns this tool does not read: {sorted(columns)}")
    rows.sort(key=_rank, reverse=True)
    return rows, task_type


def _template(sources: list[catalog.Source], template_id: str) -> tuple[catalog.Source, catalog.Template]:
    owners = [(source, template) for source in sources for template in source.templates if template.id == template_id]
    if not owners:
        offered = [f"template:{template.id}" for source in sources for template in source.templates]
        raise PredictionError(
            f"No selected source has the template {template_id!r}. Templates: {', '.join(offered) or 'none'}."
        )
    if len(owners) > 1:
        raise PredictionError(
            f"Several selected sources have the template {template_id!r}: "
            f"{', '.join(source.id for source, _ in owners)}. Write its PQL instead."
        )
    return owners[0]


def _resolve(sources: list[catalog.Source], query: Query) -> catalog.Source:
    matches = [source for source in sources if _has_tables(source, query)]
    if len(matches) == 1:
        return matches[0]
    named = ", ".join(sorted(query.tables))
    if not matches:
        offered = "; ".join(f"{source.id}: {', '.join(table.name for table in source.tables)}" for source in sources)
        raise PredictionError(
            f"No selected source has every table the PQL names ({named}). Name tables without the alias. "
            f"Selected: {offered}."
        )
    raise PredictionError(
        f"Several selected sources have every table the PQL names ({named}): "
        f"{', '.join(source.id for source in matches)}. Kumo predicts over one source; ask about one of them."
    )


def _require_tables(source: catalog.Source, query: Query) -> None:
    if not _has_tables(source, query):
        raise PredictionError(f"The template's PQL names tables {source.id} does not have: {sorted(query.tables)}.")


def _has_tables(source: catalog.Source, query: Query) -> bool:
    return query.tables <= {table.name.casefold() for table in source.tables}


def _timestamp(value: str | None) -> pd.Timestamp | None:
    """An ISO 8601 time as a naive UTC timestamp, the form of the sources' own timestamps."""
    if value is None:
        return None
    try:
        moment = datetime.datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise PredictionError(f"anchor_time {value!r} is not an ISO 8601 date or time") from error
    if moment.tzinfo is not None:
        moment = moment.astimezone(datetime.UTC).replace(tzinfo=None)
    return pd.Timestamp(moment)


def _unfiltered(condition: str, why: str) -> str:
    return (
        f"The entity filter (WHERE {condition[:300]}) could not be applied to the entities, so they may include ones "
        f"it excludes: {why}."
    )


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _redact(error: Exception | str) -> str:
    """The first line of a message, without URLs or file paths, at most 300 characters."""
    lines = str(error).strip().splitlines()
    text = PATH.sub("<path>", URL.sub("<url>", lines[0] if lines else type(error).__name__))
    return _clip(text, 300)


def _text(value: Any) -> str:
    return str(value)[:MAX_TEXT_CHARS]


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rank(row: EntityPrediction) -> float:
    for number in (row.probability, row.value):
        if number is not None:
            return number
    return -math.inf
