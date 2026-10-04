<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# prediction

The `predict` MCP tool: per-entity predictions with NVIDIA Kumo Relational (`kumo-relational-client`
1.0.2 against a Kumo Relational NIM) over any structured source of the knowledge catalog. `demo-prediction serve`
(the image's only command) serves streamable HTTP at `:8322/mcp` and `GET /health`.

The Kumo Relational NIM ships for x86_64 only. On the DGX Spark, point `KUMO_RELATIONAL_URL` at a remote NIM;
without one the tool stays registered and every call answers `available: false` with the reason. The client and
its engine (`kumo-relational-engine`, which builds the graph locally) have aarch64 wheels, so this image builds on
both architectures.

## How it fits

The `ingest` service writes each structured source's DuckDB file (`/knowledge/sources/<id>/tables.duckdb`) and
describes it in the source manifest (`/knowledge/catalog/sources/<id>.json`): tables with primary keys, time
columns and foreign keys, and optional prediction templates. The API puts those in the run instructions, so the
agent writes PQL without a schema tool. This service never writes the knowledge volume.

Hermes calls `mcp__prediction__predict` at `http://host.openshell.internal:8322/mcp`. The agent plugin's
`pre_tool_call` hook sets `source_ids` to the run's selected structured sources; the model never chooses them. The
plugin turns each result into a `structured_prediction` receipt.

Each call:

1. Reads the manifest of each requested source (on every call). Every id must exist, be `kind: structured`,
   have status `ready` or `ingesting` and have its DuckDB file; otherwise a `ToolError`.
2. Expands `pql: "template:<id>"` to the template's PQL and anchor time (an explicit `anchor_time` wins).
3. Reads the PQL (`pql.py`): the tables it names (the entity, the target and every aggregate's arguments), its
   entity `FOR EACH <table>.<primary key>`, its entity filter (`WHERE` after the entity), and its horizon (the
   target's window `(start, end, unit)`). The source is the one selected source whose tables include every named
   table; none or several is a `ToolError`, as is an entity column that is not its table's primary key.
4. Without `KUMO_RELATIONAL_URL`, returns `available: false`,
   `reason: "No Kumo endpoint is configured (KUMO_RELATIONAL_URL)."`.
5. Picks the entities: up to 1,000 primary keys of the entity table (400 for `RANK TOP k`), in key order. Past
   that, the first 1,000 (400) are scored and a warning names the population's size and says to narrow it with
   `FOR EACH ... WHERE`. Kumo predicts for every id it is given whatever the filter says, so a plain entity
   filter (such as `customers.tier = 'gold'`) is applied here (`entities.py`). sqlglot parses it as one condition
   over the entity table's own columns, with no subquery, table, generator, aggregate or file function, and with no
   backslash or dollar sign (escape strings and dollar quoting are where sqlglot's reading and DuckDB's can differ).
   The SQL regenerated from that parse must parse back to the same tree. It runs in a worker process (at most 2 at
   once): a fresh DuckDB with the source attached read-only, external access off, the configuration locked, a 2 GiB
   address space and a 10 s kill. DuckDB's own error text goes to the log, never into the result. A filter it cannot apply, including
   one over time (`COUNT(orders.*, -90, 0, days) > 0`), never runs; the result warns that the entities may include
   ones it excludes. `FOR <key> = ...` and `FOR <key> IN (...)` leave the ids to the query.
6. Builds the graph: `relational.Graph.from_duckdb(connection={"uri": <file>, "kwargs": {"read_only": True}},
   tables=[{name, primary_key, time_column}], edges=[])`, then sets every table's keys and time column to the
   catalog's (metadata inference would otherwise add its own guesses, such as `customers.joined_at` as a time
   column), then `graph.link(table, column, references_table)` for each catalog foreign key that points at its
   table's primary key, then `graph.validate()`.
7. Predicts: `RelationalClient(url=..., api_key=..., timeout=60, max_retries=0).relational(graph).predict(pql, ids,
   anchor_time=..., run_mode="fast", batch_size=500, num_retries=0, verbose=False)` (`batch_size=200` for
   `RANK TOP k`, Kumo's cap). Kumo checks each request's size client-side against its 30 MB limit: 1,000 retail
   customers with their orders and returns are over it, 500 are not (`template:return_risk_30d` took 73 s in two
   requests, measured live on the Spark). So at most two requests, one attempt each within 60 s, and a slow or
   warming NIM yields `available: false` before the agent's 180 s MCP timeout. A request still over 30 MB fails at
   once with the reason "Kumo's request for N <table> entities (500 per request) is larger than its 30 MB limit:
   narrow the population with FOR EACH <table>.<key> WHERE <a condition on <table>'s own columns> to a few hundred
   entities." A failure is `available: false` with a written
   reason, and no URL or file path in it:
   - the endpoint could not be reached, or did not answer within 60 s;
   - it refused the credentials (HTTP 401);
   - it, or a proxy in front of it such as a Cloudflare challenge, blocked the request (HTTP 403): not a key problem;
   - it rejected the query (with its first line);
   - it answered with a server error;
   - the tables do not make a graph;
   - otherwise, a generic reason, with the details in the log.

   The result is kept under 30,000 characters as the agent reads it, by dropping its lowest rows. Warnings are
   capped, and a template's PQL may be at most 2,000 characters.

Spans: `kumo` (TOOL), under the MCP SDK's `tools/call predict`.

Never install the client's `[explain]` extra: it sends raw cell values to a third-party LLM.

## Tool result

`predict(question, pql, source_ids, anchor_time=None)` returns:

| Field | Meaning |
|---|---|
| `available`, `reason` | whether the prediction ran; why not |
| `source_id`, `template_id` | the source predicted over; the template, when one was named |
| `pql` | the query that ran (the template's own when one was named) |
| `task_type` | `binary_classification`, `regression`, `multiclass_classification` or `temporal_link_prediction`: from Kumo's result columns, or from the target when the prediction did not run (`null` when the target does not show it) |
| `anchor_time` | where the prediction starts, ISO 8601 UTC; `null` means the latest timestamp in the data |
| `horizon` | `{value, unit}` from the target's window, or `null` |
| `entity_table` | the `FOR EACH` table |
| `rows` | `[{entity_id, probability, value, label}]`, at most 25, highest probability or value first |
| `model` | `kumo-relational` |
| `elapsed_ms`, `warnings` | the whole call; entities capped, filters not applied, links left out, rows cut |

Rows by task: binary, `probability` (`TRUE_PROB`); regression, `value` (`PREDICTION`); multiclass, each entity's
best `label` (`CLASS`) with its `SCORE` as `probability`; `RANK TOP k`, every `(entity, label, score)` row with the
score as `value` (a ranking score is any number, and receipts bound `probability` to [0, 1]).

Timestamps: an anchor with a time zone is converted to UTC and passed without one, as the sources' DuckDB
`TIMESTAMP` columns are.

## Environment

| Variable | Default | Notes |
|---|---|---|
| `KNOWLEDGE_DIR` | `/knowledge` | The knowledge volume, mounted read-only |
| `PREDICTION_PORT` | `8322` | |
| `KUMO_RELATIONAL_URL` | unset | The NIM's base URL, e.g. `http://kumo-relational:8000` or a remote one; unset: `available: false` |
| `KUMO_API_KEY` | unset | Or `/run/secrets/kumo_api_key`. Only for an authenticating gateway in front of the NIM (sent as `X-API-Key`) |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | unset | e.g. `http://phoenix:6006/v1/traces`; no spans are exported when unset |

The image runs as uid 10001. The source files must be readable by it.

## Test

```bash
uv run pytest                                    # the shared catalog fixture and a stubbed Kumo client
KUMO_RELATIONAL_URL=... KUMO_API_KEY=... uv run pytest -m live   # the fixture's churn template, for real
```

The offline suite builds real graphs and, once, sends a real request to a closed port, so the client's call shape is
checked without an endpoint.
