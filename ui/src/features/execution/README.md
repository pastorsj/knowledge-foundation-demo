<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Execution feature

What the agent did for an answer: a graph of the run (one fixed topology of everything a Hermes
run can use, lit by what this run used), an explorer per tool call built from its receipt, a data
viewer, a Phoenix link and step-by-step replay, plus the Agent Activity panel (Thinking and
Timeline). It plugs into the base UI through the one `ExecutionFeature` slot
(`@/shared/context`), wired in `app/providers.tsx`.

## Data flow

```text
live:   SSE `execution.v2`, `job.status` ───┐
        GET /v1/jobs/async/job/{id}/export ─┤ (receipts, and the events of an earlier run)
replay: /api/recordings/sessions/<id>.json ─┘
                                            ▼
      store.ts (runs by job id: events, receipts, job status; nothing persisted)
                                            ▼
     projection.ts (events up to the replay cursor → tool calls, model calls, status)
          ▼                      ▼                                   ▼
 graph/graph-events.ts  activity/activity-model.ts          receipt-summary.ts, explorers/*
 graph/graph-model.ts    (Thinking items, Timeline)                  ▼
          ▼                      ▼                          explorers/*Inspector.tsx
 graph/ExecutionGraph.tsx activity/ActivityPanel.tsx
          ▼
 ExecutionWorkspace.tsx
```

The types are generated from the API's models (`@/generated`, never edited by hand) and every
tool name, label and explorer comes from the tool registry (`TOOL_REGISTRY`). The view-models are
pure functions. Everything takes the original demo UI's look: the workspace, replay bar, graph,
explorers and data viewer from `execution-workspace.module.css` and the explorers' CSS modules, and
the Agent Activity panel from `activity/execution-timeline.module.css`.

## Files

| Path | Role |
| --- | --- |
| `contract.ts` | Shape guard for events and receipts; anything else is dropped and counted |
| `registry.ts` | Tool lookup by MCP or Hermes name; the logos on tool and resource nodes |
| `store.ts`, `projection.ts` | Run state, and its reduction to what the views show |
| `graph/` | The execution graph: events as graph facts (a component per tool server), the fixed topology and its states, the renderer |
| `explorers/` | Per node: Auto Ontology (`OntologyLineageInspector`), the rest (`EvidenceInspector`, from `receipt-summary.ts`: retrieval passages, DuckDB SQL and rows, Kumo predictions) |
| `activity/` | The Agent Activity panel's tabs: Thinking (display-safe milestones) and Timeline (the finished run's spans, with each action's recorded result) |
| `replay/` | Recordings bundle and job export (`sources.ts`), the replay cursor and its controls |
| `data-viewer/` | The Structured Database browser: tables, previews and read-only SQL; in replay, the bundle's copy (`database.json`) |
| `trace-link.ts` | Phoenix trace and span links (shown when `PHOENIX_URL` is set) |

The graph has one node per technology a run can use: Documents (Retrieve Evidence, then Nemotron
Parse, Nemotron Embed, Milvus and, when the receipt names a rerank model, Nemotron Rerank; without
one the evidence keeps Milvus's order), Tables (Query Tables over DuckDB), Prediction (Predict with
NVIDIA Kumo) and Ontology (Auto Ontology's `ask_question`). `query_tables` and `ask_question` share a
capability family, so the graph reads each call's component from its tool's MCP server, as the API's
`componentId` does (`duckdb.tables`, `nvidia.ontology`).

The panel follows the running job, else the last answer of the open conversation, so a reopened
session, recorded or saved, shows its activity (`selectActivityJobId` in `features/chat`).

## API it reads

- `GET /v1/jobs/async/job/{id}/export`: one turn, `{jobId, question, …, events, receipts}`.
- `GET /v1/jobs/async/job/{id}/trace`: `{trace_id}`, linked as `<PHOENIX_URL>/redirects/traces/<id>`.
- `GET /v1/jobs/async/job/{id}/stream`: the `execution.v2` events, and `job.status`, whose final
  status (`success`, `failure`, `interrupted`) decides how the run ended.
- `GET /v1/data_sources/{id}/schema`, `…/preview?table=&limit=` and `POST …/query {sql}`.
- Replay mode reads only the selected pack's `/api/recordings/<pack>/index.json`,
  `…/sessions/<id>.json` and `…/database.json` (bundle v2, written by `scripts/demo.sh record`) and
  never calls the API. The data viewer then shows the copied schema and rows, and reruns only the
  queries the recording ran.

## Adding a tool

Add it to `contracts/tool-registry.json` and regenerate. A tool with an existing `receipt_kind`
needs no UI change: the graph draws it under its capability's nodes, or as Other Tools until it
gets a node of its own in `graph/graph-model.ts` (`hermesExecutionGraphNodes`, the edges into and
out of it, and `TOOL_NODE_BY_NAME`). A new kind adds a receipt model in the API, a `case` in `receipt-summary.ts`
and an entry in `ARTIFACT_KINDS` (`contract.ts`), which TypeScript then requires. To draw the logo of a library the tool is built on, as Retrieve Evidence draws
LangChain's, add the file to `public/ecosystem-logos` and an entry to `TOOL_LOGOS` (`registry.ts`).

## Tests

`npm test` runs the specs next to each module against the golden fixtures in
`contracts/fixtures/`. `npm run e2e` replays the synthetic bundles in
`e2e/fixtures/packs/<pack>/recordings/` in a browser.
