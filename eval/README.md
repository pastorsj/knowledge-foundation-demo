<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# eval

The on-demand answer-quality eval of a running deployment (`./scripts/demo.sh eval`), which you run by hand and CI
never does. It asks a pack's questions, checks each cited answer against the evidence the agent's tools returned,
and reports. The other on-demand check, the live end-to-end test (`./scripts/demo.sh test live`), is in
[`ui/e2e-live/`](../ui/e2e-live/live.spec.ts); [operations](../docs/operations.md#on-demand-checks) covers both.

`demo-eval` is a uv project (Python 3.12, PyYAML, the standard library for HTTP). It reaches a deployment the way a
browser does, through its UI (`http://127.0.0.1:3300` by default): `GET /api/v1/packs`, `GET /api/v1/pack?id=`,
`GET /api/v1/data_sources?pack=`, the job routes and `POST /api/v1/data_sources/<id>/query`. So the same command
works on the host, through an SSH tunnel, or through a link to the UI.

## Answer-quality eval

```bash
./scripts/demo.sh eval --pack retail                      # every question with answer checks, once
./scripts/demo.sh eval --pack retail --runs 2 --questions return-window-fees,gold-churn-risk
./scripts/demo.sh eval --pack retail --url http://spark:3300
```

Without `--pack` the eval asks the first industry the deployment lists. A deployment serves every pack at once, so
there is no active pack to match.

Each question runs as a fresh job with its own sources, as the UI's cards send it, one at a time, so every run
costs model calls; `--max-wait` (default 1,260 s) cancels a job that runs longer. The job's body is `{input,
data_sources, pack_id}`. `data_sources` are the pack's catalog ids: a question's `sources` in `questions.yaml` are
pack-local ids (`sales`), the catalog's are `<pack>.<source>` (`retail.sales`). By default the eval asks every
question the pack's `answers.yaml` checks, or the featured ones when the pack has none. Then it scores each run:

| Check | What passes |
|---|---|
| `success` | the job succeeded |
| `cited` | the report cites at least one receipt of the run |
| `declared_tools` | the run used every tool pill the question declares in `questions.yaml` (`retrieval`, `duckdb`, `kumo`, `ontology`) |
| `required_tools` | each tool group of the question's `answers.yaml` entry had one tool called |
| the question's own | in `answers.yaml`: a value the oracle computed is named or shown as the right percentage, a phrase is stated, the prediction is available, and so on |

The oracles are the pack's `eval/oracles/*.sql`, run read-only on the deployment's own data through the API's query
route (`POST /v1/data_sources/<id>/query`), so the reference values always come from the data the agent's tools
read. The checks need no model.

It prints a summary table and writes everything to a run directory (default `eval/runs/<pack>-<UTC time>/`,
which git ignores): `meta.json`, `oracles.json`, `runs/<qid>.<n>.json` (each job's status, wall time and export),
`grades/` with the grader on, `scores.json` and `report.md`. `uv run --project eval demo-eval report DIR` scores a
run directory again after a change to the checks, without asking anything. A run directory holds the deployment's
answers and evidence: keep it off the repository and delete it when you are done.

Exit codes: 0 every run passed, 1 a run failed, 64 the request does not fit the deployment (an unknown pack or
question, an oracle the data refuses, a half-configured grader), 69 the deployment did not answer.

### The optional grader

Off by default. It turns on when the environment holds all three of:

| Variable | Meaning |
|---|---|
| `GRADER_BASE_URL` | an OpenAI-compatible endpoint, e.g. `https://api.openai.com/v1` (it calls `/chat/completions`) |
| `GRADER_API_KEY` | its key |
| `GRADER_MODEL` | its model id |
| `GRADER_SAMPLES` | optional: samples per run, majority vote (default 3) |

The grader must be a frontier model, such as GPT-6.1 Sol or Claude Opus 5.5, the two graders of the bake-off in
[models and routing](../docs/models-and-routing.md). It has to check every number, date and unit in a report
against the receipts; smaller models miss unsupported claims and fractions shown as percentages, which is what it is
there to catch. It grades blind, with the bake-off judge's prompt: it sees what the data is (the `answers.yaml`
`dataset` line), the question, the reference facts (`answers.yaml` `facts`, filled from the oracles), a digest of the
run's receipts and the report, never the model or the route. A run then passes only when its deterministic checks
pass and the grader's majority says pass. It asks for JSON-schema output, and on an endpoint without it, for JSON in
the prompt.

The key is read from the environment only: never from `.env`, never from a command-line argument, and never
printed or written to the run directory. Keep it out of your shell history, for example:

```bash
read -rs GRADER_API_KEY && export GRADER_API_KEY GRADER_BASE_URL=https://api.openai.com/v1 GRADER_MODEL=<model id>
./scripts/demo.sh eval --pack retail
```

### `answers.yaml`

Optional, per pack: `data/packs/<id>/eval/answers.yaml`, beside `eval/oracles/*.sql`. Neither is part of the files
the pack ingests, so changing them never re-ingests it. One entry per question id:

```yaml
dataset: "synthetic: a fictional retailer made for the demo"   # what the grader is told
source: sales                                     # the structured source the oracles query; optional when the pack has one
format: {signed_percent: [growth], percent: [return_rate]}     # how facts show fractions
questions:
  top-tier:
    oracles:
      tiers: {}                                   # eval/oracles/tiers.sql
      weakest: {sql: tiers, order: ORDER BY net_revenue ASC, limit: 1}
    tools: [[query_tables]]                       # every group needs one of its tools called
    checks:
      - {id: top_tier_named, mentions: "tiers[0].tier"}
      - {id: revenue_exact, contains: ["395.00", "gold"]}
    facts: "Highest first: {tiers[:3]: tier, net_revenue}."
```

A check names oracle rows as `<oracle>[<rows>].<field>`, where rows is an index (`0`, `-1`), a slice (`:3`, `-3:`,
`:`) or `max(<field>)`. The text checks read the report without markdown emphasis (`**not**` as `not`). The check
kinds:

| Kind | Passes when |
|---|---|
| `mentions: REF` | every referenced value (a name or an id) is in the report as a whole word, ignoring case; `at_least: N` or `any: true` relaxes it |
| `contains: [TEXT, ...]` | every substring is in the report, ignoring case (a string is a list of one) |
| `percent: REF` | every referenced fraction appears as a percentage, within display rounding |
| `pattern: REGEX` (or a list: any) | the report matches; `(?i)` for case-insensitive |
| `retrieved_source: ID` | a retrieval call returned hits from that source: the pack's own id (`policies`) or its catalog id |
| `percent_grounding: SHARE` | at least that share of the report's percentages match a number in the receipts |
| `prediction_available: true` | a Kumo prediction receipt completed with scored entities, not an unavailable result |

`tests/test_spec.py` checks every pack's file against its `questions.yaml`, `pack.yaml`, the tool registry and the
oracles' columns.

## Test

```bash
uv run --directory eval pytest   # offline: stand-in deployments and graders
```
