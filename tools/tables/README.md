<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# tables

The `query_tables` MCP tool: one read-only SQL `SELECT` (DuckDB dialect) over the structured sources of the
knowledge catalog. `demo-tables serve` (the image's only command) serves streamable HTTP at `:8321/mcp` and
`GET /health`.

## How it fits

The `ingest` service loads every structured source (CSV, XLSX, Parquet, JSON) into one DuckDB file,
`/knowledge/sources/<source_id>/tables.duckdb`, and describes it in the source's manifest,
`/knowledge/catalog/sources/<source_id>.json` (`contracts/catalog/source-manifest.schema.json`): its `alias` (the
source id in snake case, `retail.sales` -> `retail_sales`) and its tables, columns and keys. The API puts those in the
run instructions, so the agent writes SQL without a schema tool. This service never writes the knowledge volume.

Hermes calls `mcp__tables__query_tables` at `http://host.openshell.internal:8321/mcp`. The agent plugin's
`pre_tool_call` hook sets `source_ids` to the run's selected structured sources; the model never chooses them. The
plugin turns each result into a `structured_query` receipt.

Each call:

1. Reads the manifest of each requested source (on every call, never cached). Every id must exist, be
   `kind: structured`, have status `ready` or `ingesting`, and have its DuckDB file; otherwise a `ToolError` names
   each refused source.
2. Checks the SQL with sqlglot (`guard.py`): exactly one `SELECT` (CTEs, joins, subqueries, `UNION`/`INTERSECT`/
   `EXCEPT` and window functions are fine) whose tables are `<alias>.<table>` (or `<alias>.main.<table>`) of the
   selected sources, the query's own CTEs, or the generators `range`, `generate_series` and `unnest`. Refused before
   anything runs: other statements (`ATTACH`, `COPY`, `PRAGMA`, `SET`, `INSTALL`, `LOAD`, `EXPORT`, DML, DDL), a
   second statement, `INTO`, file and URL reads (`read_*`, `glob`, `parquet_scan`, a quoted path as a table),
   `query`/`query_table`, DuckDB's system and pragma functions, and tables of other databases.
3. Runs it in a worker process (`python -I -m demo_tables.query`, at most 4 at once): a fresh in-memory DuckDB,
   each source `ATTACH '<path>' AS <alias> (READ_ONLY)`, then `SET enable_external_access = false`,
   `SET autoload_known_extensions = false`, `SET lock_configuration = true`. DuckDB must also see one `SELECT`.
   The worker fetches one row at a time, at most 201, and stops adding rows before its output passes 1 MB; each
   cell stays within 2,000 characters of JSON and 100 list or struct items. It has 1 GB of DuckDB memory, 2
   threads, no spilling and a 4 GiB address space, and is killed after 10 seconds. The server reads at most 1 MB
   plus 64 KiB from it, and kills a worker that writes more.
4. Keeps the result under 30,000 characters as the agent reads it (as `tools/retrieval` does), by dropping its last
   rows (off the event loop); if even no rows fit, the echoed SQL, question and column names are shortened.

The two locks are independent: a query the guard misreads still cannot reach a file, change a setting or write to a
source (`tests/test_server.py` runs the escapes against the worker alone).

Spans: `guard` (GUARDRAIL) and `duckdb` (TOOL), under the MCP SDK's `tools/call query_tables`.

## Tool result

`query_tables(question, sql, source_ids)` returns:

| Field | Meaning |
|---|---|
| `question`, `sql` | as sent |
| `database_name` | the alias when one source is attached, else `knowledge` |
| `databases` | `[{source_id, alias}]`, sorted by source id |
| `columns` | the result's column names; a repeated name gets a suffix (`customer_id`, `customer_id_2`) |
| `rows` | one object per row, keyed by `columns` |
| `row_count` | rows in this result |
| `truncated` | the query had more than 200 rows, or rows were dropped to fit 30,000 characters |
| `elapsed_ms` | the whole call |
| `warnings` | why rows are missing, or how many values were cut |

Values are JSON-safe: dates, times and timestamps in ISO 8601 (timestamps with a time zone in UTC), decimals as
numbers, integers past 2^53 and `NaN`/`Infinity` as strings, intervals and UUIDs as text, binary as
`[binary: <n> bytes]`, lists and structs as arrays and objects. Text longer than 2,000 characters is cut and ends
in an ellipsis. A result has at most 100 columns.

## Environment

| Variable | Default | Notes |
|---|---|---|
| `KNOWLEDGE_DIR` | `/knowledge` | The knowledge volume, mounted read-only |
| `TABLES_PORT` | `8321` | |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | unset | e.g. `http://phoenix:6006/v1/traces`; no spans are exported when unset |

The image runs as uid 10001. The source files must be readable by it.

## Test

```bash
uv run pytest   # the shared catalog fixture, with tables.duckdb built from contracts/fixtures/catalog/tables/*.csv
```
