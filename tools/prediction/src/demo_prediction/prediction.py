# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""predict: run a PQL query on NVIDIA Kumo Relational over one structured source of the knowledge catalog.

Ported from tools/market-analytics (`Predictor`, the window parser, one attempt within 60 s). The source is the one
selected source whose tables include every table the query names. Its graph is built on each call from its DuckDB
file, read-only, with the catalog's primary keys, time columns and foreign keys (`TableInfo`). Kumo scores up to
`MAX_ENTITIES` entities of the entity table, in primary-key order, filtered by the query's entity filter when that
filter is a plain condition on the entity table; the result lists the `MAX_ROWS` highest.

Without KUMO_RELATIONAL_URL, or when the endpoint fails, the result says so (`available: false` and the reason)
rather than raising: the agent reports it instead of retrying.
"""

from __future__ import annotations

import datetime
import logging
import math
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from kumo_relational_client import RelationalClient
from kumo_relational_client import relational
from openinference.semconv.trace import OpenInferenceSpanKindValues as SpanKind
from openinference.semconv.trace import SpanAttributes
from opentelemetry import trace
from pydantic import BaseModel
from pydantic import Field

from . import catalog
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
MAX_ENTITIES = 100
MAX_ROWS = 25
MAX_TEXT_CHARS = 200  # entity ids and class labels, so 25 rows always fit the result budget
FILTER_TIMEOUT_SECONDS = 10.0
TEMPLATE_PREFIX = "template:"
NO_ENDPOINT = "No Kumo endpoint is configured (KUMO_RELATIONAL_URL)."
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
                warnings=warnings,
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
            except Exception as error:  # the NIM is down, still warming up, or rejected the query or the graph
                logger.warning("Kumo prediction over %s failed: %r", plan.source.id, error)
                span.set_attribute("error.type", type(error).__name__)
                return result(reason=f"{type(error).__name__}: {error}"[:500])
            span.set_attribute("output.row_count", len(rows))
        if len(rows) > MAX_ROWS:
            warnings.append(f"Showing the {MAX_ROWS} highest of {len(rows)} predictions.")
        return result(rows=rows[:MAX_ROWS], task_type=task_type)

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
    """Up to MAX_ENTITIES primary keys of the entity table, in order, that satisfy the query's entity filter.

    Kumo scores every id it is given, whatever the filter says, so a plain condition on the entity table is applied
    here. A filter over time (an aggregate with a window) only Kumo can evaluate; it is reported instead.
    """
    warnings = []
    if condition and TEMPORAL.search(condition):
        warnings.append(_unfiltered(condition, "it aggregates over time, which only Kumo evaluates"))
        condition = None
    table, key = _identifier(entity.name), _identifier(entity.primary_key or "")
    select = f"SELECT {key} FROM source.main.{table} AS {table}"
    order = f" ORDER BY 1 LIMIT {MAX_ENTITIES + 1}"
    try:
        ids = _select(source.path, select + (f" WHERE ({condition})" if condition else "") + order)
    except duckdb.Error as error:
        if condition is None:
            raise
        warnings.append(_unfiltered(condition, str(error).strip().splitlines()[0][:300]))
        ids = _select(source.path, select + order)
    if len(ids) > MAX_ENTITIES:
        warnings.insert(
            0,
            f"Predictions cover the first {MAX_ENTITIES} entities of {entity.name} by {entity.primary_key}; it has "
            "more. Narrow them with a WHERE on the entity table.",
        )
    return ids[:MAX_ENTITIES], warnings


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


def _select(path: Path, sql: str) -> list[Any]:
    """One query over the source's file in a locked-down DuckDB: it can read that file and nothing else."""
    with (
        tempfile.TemporaryDirectory(prefix="demo-prediction-") as temp_directory,
        duckdb.connect(
            ":memory:",
            config={
                "autoinstall_known_extensions": "false",
                "autoload_known_extensions": "false",
                "python_enable_replacements": "false",
                "threads": "1",
                "memory_limit": "512MB",
                "temp_directory": temp_directory,
                "max_temp_directory_size": "0B",
            },
        ) as connection,
    ):
        location = str(path).replace("'", "''")
        connection.execute(f"ATTACH '{location}' AS source (READ_ONLY)")
        connection.execute("SET enable_external_access = false")
        connection.execute("SET lock_configuration = true")
        timer = threading.Timer(FILTER_TIMEOUT_SECONDS, connection.interrupt)
        timer.start()
        try:
            return [row[0] for row in connection.execute(sql).fetchall()]
        finally:
            timer.cancel()


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


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


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
