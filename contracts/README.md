<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Contracts

The JSON that services share. Services never import each other's code; they agree on these files.

| Path | Source | Used by |
| --- | --- | --- |
| `tool-registry.json` | Hand-written | api, agent plugin, UI (as `TOOL_REGISTRY`), wiring tests |
| `tool-registry.schema.json` | Hand-written | Validates the registry |
| `catalog/source-manifest.schema.json` | Hand-written | ingest (validates every manifest it writes), api, retrieval, tables, prediction |
| `catalog/pack-manifest.schema.json` | Hand-written | ingest, api |
| `schemas/execution-event.schema.json` | Generated from `api/src/demo_api/events/` | UI types, replay bundles |
| `schemas/receipt.schema.json` | Generated from `api/src/demo_api/receipts/` | UI types, agent plugin tests |
| `schemas/pack.schema.json` | Generated from `api/src/demo_api/pack.py` (`PackView`, `GET /v1/pack`) | UI types (the landing page and the composer's example picker) |
| `schemas/packs.schema.json` | Generated (`PackList`, `GET /v1/packs`) | UI types (the industry selector) |
| `fixtures/*.json` | Hand-curated, canonicalized by the generator | api and UI tests |
| `fixtures/catalog/` | Hand-curated: a `retail` pack, the `workspace` pack, a documents and a structured source, and the CSVs of its tables | the tests of api, retrieval, tables and prediction |

TypeScript for the generated schemas and the registry is generated into `ui/src/generated/`. The industry pack
format (`pack.yaml`, `questions.yaml`) is not a service contract: its schemas are in `data/schemas/`.

## Regenerate

```bash
scripts/gen-contracts.sh           # after changing a model, the registry or a fixture
scripts/gen-contracts.sh --check   # fails if anything is out of date
```

The script exports the Pydantic models as JSON Schema, validates every fixture and rewrites it in canonical form,
then runs `json-schema-to-typescript` and Prettier (both pinned). Commit what it writes. Never edit `schemas/` or
`ui/src/generated/` by hand. The catalog schemas are edited by hand; ingest's image copies `contracts/catalog/` in
(a named build context), so rebuild it after a change.

## Tool registry

One entry per MCP tool. Adding a tool starts here ([customize](../docs/customize.md)). Four tools:

| `id` | `server` | `family` | `receipt_kind` | `explorer` | `profile` | `pills` |
| --- | --- | --- | --- | --- | --- | --- |
| `retrieve_evidence` | `retrieval` | `unstructured_retrieval` | `retrieval_evidence` | `retrieval` | `retrieval` | `retrieval` |
| `query_tables` | `tables` | `structured_retrieval` | `structured_query` | `sql` | `tables` | `duckdb` |
| `predict` | `prediction` | `structured_prediction` | `structured_prediction` | `pql` | `kumo` | `kumo` |
| `ask_question` | `auto_ontology` | `structured_retrieval` | `structured_query` | `ontology` | `ontology` | `ontology` |

| Field | Meaning |
| --- | --- |
| `id` | MCP tool name |
| `server` | Hermes MCP server key (also its toolset): `retrieval`, `tables`, `prediction`, `auto_ontology` |
| `hermes_name` | `mcp__<server>__<id>`, the name Hermes and the receipts use |
| `family` | The capability a data source must grant: `unstructured_retrieval`, `structured_retrieval`, `structured_prediction` |
| `label`, `description` | Display text for the UI |
| `explorer` | UI explorer: `retrieval`, `sql`, `pql`, `ontology` |
| `receipt_kind` | The `artifactKind` of the tool's receipts |
| `profile` | The agent feature that bakes the tool into the image (`AGENT_FEATURES`): `retrieval`, `tables`, `kumo`, `ontology` |
| `pills` | The technology pills the UI shows for a run that used the tool, in display order `retrieval`, `duckdb`, `kumo`, `ontology`. A pack's `questions.yaml` `tools` uses the same names |

The API narrows each source's capabilities to the families of the features this stack runs, and offers a pack's
question only when its pills are served.

## Catalog manifests

The knowledge catalog (`/knowledge/catalog/`) is written by ingest only and read on every request by the API and the
tools ([architecture](../docs/architecture.md#the-knowledge-catalog)).

**SourceManifest** (`catalog/sources/<source_id>.json`, `schema_version: "1"`):

| Field | Meaning |
| --- | --- |
| `id`, `pack_id` (`null` for the workspace), `name`, `description`, `agent_description` | Identity and copy. Ids are `<pack>.<name>`; the pattern also keeps an id from naming another path |
| `kind` | `documents` or `structured` |
| `capabilities` | `unstructured_retrieval`; or `structured_retrieval` and `structured_prediction` |
| `synthetic`, `default_enabled`, `example_questions` | Flags and examples |
| `status` | `ready`, `ingesting`, `empty`, `failed` |
| `updated_at` | ISO 8601 UTC |
| `files` | One entry per file: `file_id`, `file_name`, `sha256`, `size_bytes`, `status`, `parser`, `document_id`, `pages`, `chunks`, `tables`, `warnings`, `error_message` |
| `documents` | Documents kind: `{count, chunks, collection, embed_model}` |
| `database` | Structured kind: `{path, alias, tables: [TableInfo]}`; `path` is relative to `/knowledge` |
| `prediction` | Optional: `{templates: [{id, name, description, pql, anchor_time}]}` |

`TableInfo`: `name`, `description`, `row_count`, `primary_key` (or null), `time_column` (or null), `origin_file`,
`columns: [{name, type, description, nullable}]`, `foreign_keys: [{column, references_table, references_column}]`.
The `alias` is the snake-cased source id (`retail_sales`); agents address tables as `<alias>.<table>`.

**PackManifest** (`catalog/packs/<pack_id>.json`): `id`, `kind` (`industry` or `workspace`), `title`,
`description`, `icon`, `version`, `as_of`, `disclaimer`, `sources` (ids), `questions`, `examples`,
`conversations`, `digest`, `status` (`ready`, `ingesting`, `failed`, `empty`), `updated_at`.

## Execution events (`execution.v2`)

The API emits every execution observation as one `ExecutionEventV2`. The event store gives each row a monotonic
per-job cursor. `GET /v1/jobs/async/job/{job_id}/stream` sends each event as an SSE frame with
`event: execution.v2`, `id: <cursor>` and the event (without `cursor`) as data. Clients resume with
`/v1/jobs/async/job/{job_id}/stream/{cursor}` or `Last-Event-ID`.

The `tool.*` and `artifact.*` events of a registered tool carry the tool's registry `family` as `capabilityId`. The
tool's `server` decides `componentId` (`COMPONENT_BY_SERVER` in `api/src/demo_api/events/execution.py`), so two
servers that share a family are still two components of the execution graph:

| `server` | `componentId` |
| --- | --- |
| `retrieval` | `milvus.retrieval` |
| `tables` | `duckdb.tables` |
| `prediction` | `nvidia.kumo` |
| `auto_ontology` | `nvidia.ontology` |

`display.attributes` is open JSON with snake_case keys. Model calls are `llm.call` events whose attributes carry
`served_model` (the model Switchyard served) and `tier` (`efficient` or `capable`), so the UI can show when a run
escalates. Token counts are `input_tokens` and `output_tokens`; `prompt_tokens` and `completion_tokens` are
rejected (see [the limits](#display-safe-json-limits)).

When a run succeeds, the API records its publication as three last events, in this order:

| `eventKind` | Label | `display.attributes` |
| --- | --- | --- |
| `report.completed` | Response formatted | – |
| `report.reference_resolution` | Citations resolved | `status` (`reference_ids_resolved`, `partial`, `evidence_uncited` or `no_evidence`), `total_citations`, `uncited_evidence_count`, `invalid_evidence_count` |
| `report.metrics` | Run metrics available | `runtime_profile` (the model the run asked Hermes for), `wall_duration_ms`, `tool_call_count`, `known_tool_duration_ms`, token counts |

## Receipts (`ReceiptV2`)

The agent plugin posts one receipt per registered tool call to `POST /internal/hermes/jobs/{job_id}/tool-receipts`.
`receiptId` is the evidence id the agent cites; events reference it in `artifactRefs`. The union is discriminated
by `artifactKind`, one variant per `receipt_kind` in the registry:

| `artifactKind` | Tools | Content |
| --- | --- | --- |
| `retrieval_evidence` | `retrieve_evidence` | The tool's result: hits with their snippets, citations and metadata, models, index and timings |
| `structured_query` | `query_tables`, `ask_question` | `query` (the question, or the SQL), `database_name`, `answer` (Auto Ontology), `sql`, up to 25 `rows` of at most 40 columns, `source_row_count`, `truncated`, `resolution_lineage` (Auto Ontology) |
| `structured_prediction` | `predict` | The tool's result: `available`, `reason`, `source_id`, `template_id`, `pql`, `task_type`, `anchor_time`, `horizon`, `entity_table`, `rows`, `model`, `warnings`. A completed one has scored rows |

The API accepts field names in snake_case or camelCase and serves camelCase. Keys inside open JSON (`payload`,
`rows`, `metadata`) are kept as sent. A completed receipt always has content. A failed one may carry `errorType`,
`errorSummary` and any bounded content the tool returned.

### From tool result to receipt

The plugin never makes up a value. When a tool does not report a fact, the receipt does not carry it.

| Receipt field | Source |
| --- | --- |
| `toolName` | The registry `hermes_name` |
| `durationMs` | Hermes' `post_tool_call` `duration_ms`: the tool's own execution time |
| `occurredAt` | The plugin's clock at `post_tool_call` |
| `status` | `failed` when the call raised or the result reports a failure (a prediction with `available: false`); otherwise `completed` |
| `errorType` | `evidence_unavailable` for an unavailable prediction, or the Hermes error category when the call raised |
| `errorSummary` | The prediction's `reason`, or the Hermes error |
| `content` | The tool's result, fitted to the limits below |

## Display-safe JSON limits

The API runs every receipt `content` and every event's `display.attributes` through
`validate_bounded_display_json` in `api/src/demo_api/events/models.py`. JSON Schema cannot
express these rules, so `receipt.schema.json` does not show them. One violation rejects the whole
receipt or event.

| Rule | Limit |
| --- | --- |
| `MAX_JSON_DEPTH` | Values sit at most 6 levels below `content` or `attributes` |
| `MAX_JSON_ITEMS` | 100 items per array, 100 fields per object |
| `MAX_JSON_NODES` | 2,000 nodes in total, counting objects, arrays and values |
| `MAX_JSON_TEXT_CHARS` | 32,000 characters per string |
| Keys | 1 to 128 characters |
| Numbers | Finite |
| Text | No control characters except tab, line feed and carriage return |

An object key is rejected, at any depth, when this case-insensitive pattern matches anywhere in it:

```text
thought|reasoning|prompt|embedding|password|passwd|secret|token|credential|api[_-]?key|
authorization|cookie|connection(?:_string)?|private[_-]?key|access[_-]?key|
filesystem|file[_-]?path|storage[_-]?uri|dsn
```

So `system_prompt`, `token_count` and `prompt_tokens` are all rejected. The exceptions are the
usage keys `input_tokens`, `output_tokens`, `total_tokens`, `reasoning_tokens`,
`cached_input_tokens`, `cache_write_tokens`, `max_output_tokens` and `token_usage`, and
`query_embedding_ms` when it is a finite, non-negative number.

The plugin fits each result before it posts it, as the prototype's projection did:

1. Drop every key that matches the pattern, such as a SQL column named `token_count`.
2. Cut each list to the schema's `maxItems`, or to 100 where the schema has none. SQL rows keep
   at most 40 columns, so 25 rows fit in the 2,000-node budget.
3. Record every cut: SQL rows set `truncated` and keep `source_row_count`.
4. Cut each string to the schema's `maxLength`, for example a hit's `snippet` to 1,500 characters.
5. Replace each control character the Text rule refuses with a space.

## Fixtures

`fixtures/execution-events.json` is one sanitized run with a table query and a document retrieval, with the agent,
Switchyard, Milvus and DuckDB components and two illustrative `llm.call` events. `fixtures/receipts.json` holds one
completed receipt per tool (`query_tables`, `retrieve_evidence`, `ask_question`, `predict`), a second retrieval
receipt, and a failed prediction that follows the tool's path for an unreachable Kumo NIM. `fixtures/catalog/` is
the catalog the tools' and the API's tests read: the `retail` and `workspace` pack manifests, the
`retail.policies` and `retail.sales` source manifests, and the CSVs from which the tests build `tables.duckdb`.
