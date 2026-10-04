<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Data packs

An industry pack is raw files plus metadata, ingested by the same pipeline as an upload. Each pack is one directory
of [`data/packs/`](../data/packs); the UI's top-right selector lists every pack the catalog holds, plus "Your data".
All packs are co-resident in the knowledge catalog, so switching industry is instant and rebuilds nothing.

## The industries

Four packs, each a fictional company with its own tables, documents and questions. Everything in them is
synthetic and labeled so.

| Pack (`id`) | Company | Structured source (DuckDB alias) | Document sources | Kumo templates |
|---|---|---|---|---|
| Retail (`retail`) | Lumen Retail Group, an omnichannel home-and-lifestyle retailer | `sales` (`retail_sales`): stores, products, customers, orders, order items, returns, a merchandising plan workbook | `policies` (2 PDF, DOCX, scanned PNG), `reports` (PDF with tables, PPTX) | `churn_90d`, `return_risk_30d`, `high_value_30d` |
| Manufacturing (`manufacturing`) | Atlas Precision Components, a maker of machined parts | `operations` (`manufacturing_operations`): plants, machines, sensor readings, maintenance events, work orders, quality defects, a production plan workbook | `procedures` (2 PDF, scanned PNG), `reports` (8D report PDF, DOCX, PPTX) | `unplanned_failure_30d`, `quality_defect_14d`, `high_alarm_count_30d` |
| Healthcare (`healthcare`) | Riverside Health Network, 3 hospitals and 12 clinics | `clinical` (`healthcare_clinical`): facilities, providers, patients, encounters, diagnoses, claims, a quality scorecard workbook | `guidelines` (2 PDF, scanned PNG), `operations` (PDF, DOCX, PPTX) | `readmission_30d`, `ed_visit_30d`, `claim_denial_60d` |
| Financial Services (`financial-services`) | Harborview Community Bank, a regional bank with 18 branches (retail banking, not markets) | `banking` (`financial_services_banking`): branches, customers, accounts, monthly balances, loans, loan payments, card disputes, a credit risk workbook | `policies` (2 PDF, DOCX, scanned PNG), `disclosures` (PDF, PPTX) | `loan_delinquency_90d`, `account_closure_90d`, `card_dispute_30d` |

Each pack has 10 questions: 3 over documents, 3 over SQL, 2 hybrid (documents and tables) and 2 predictions, 6 of
them featured. Every pack holds at least one scanned page that only Nemotron Parse can read, and figures in its
reports that agree with its tables. Each pack's README is its data card: provenance, seed, tables, planted
stories and an answer key.

`DEFAULT_PACK` (default `retail`) picks the industry the UI opens on.

## The pack format (schema version 3)

```text
data/packs/<id>/
  pack.yaml          identity, licenses, provenance, sources (each: kind, file globs, capabilities, descriptions,
                     optional declared keys and prediction templates)
  questions.yaml     questions with tool pills, the picker's examples (at most 12), conversations
  files/             the committed synthetic files: PDFs (some scanned, as images), DOCX, PPTX, PNG, CSV, XLSX
  generator/         the seeded build.py and the authored text (content/) that produced files/
  recordings/        replay bundle v2, written by `demo.sh record --pack <id>`
  README.md          data card, written by the generator
```

The schemas are [`data/schemas/pack.schema.json`](../data/schemas/pack.schema.json) and
[`data/schemas/questions.schema.json`](../data/schemas/questions.schema.json).

### `pack.yaml`

| Field | Meaning |
|---|---|
| `schema_version` | `"3"` |
| `id` | The directory name (`^[a-z][a-z0-9-]*$`) |
| `version`, `as_of` | Pack version and the data's as-of date |
| `title`, `description`, `icon` | The selector's text; `icon` is a name from `ui/src/adapters/ui/icons.tsx` |
| `disclaimer` | Shown with the pack's answers |
| `licenses`, `provenance` | Where every file came from and under which terms |
| `sources` | The pack's sources (below) |
| `questions` | Always `questions.yaml` |

Each source:

| Field | Meaning |
|---|---|
| `id` | Pack-local id; the catalog id is `<pack>.<id>` (`retail.sales`) |
| `name`, `description` | The UI's text |
| `agent_description` | What the agent reads about the source in its run instructions |
| `kind` | `documents` or `structured` |
| `files` | Globs relative to the pack (`files/policies/*.pdf`); each must match a file |
| `capabilities` | `[unstructured_retrieval]` for documents; `[structured_retrieval, structured_prediction]` for tables |
| `synthetic`, `default_enabled`, `example_questions` | Flags and the source's own examples |
| `tables` | Structured only, optional: per table `description`, `primary_key`, `time_column`, `foreign_keys` (`column`, `references: <table>.<column>`) and column descriptions. Declarations win over profiling |
| `prediction.templates` | Structured only, optional: `{id, name, description, pql, anchor_time}`; the agent can call `predict` with `pql: "template:<id>"` |

Table names in `tables` are the names ingest gives (`<file stem>`, plus `_<sheet>` for a workbook of several
sheets: `merchandising_plan_promo_calendar`).

### `questions.yaml`

| Field | Meaning |
|---|---|
| `examples` | The composer's example picker, in order: every featured question first, at most 12 |
| `questions[]` | `id`, `label`, `tag` (`DOCUMENTS`, `SQL`, `HYBRID`, `PREDICTION`, `ONTOLOGY`), `description`, `question`, `sources` (pack-local ids), `tools` (pills: `retrieval`, `duckdb`, `kumo`, `ontology`), `featured` |
| `conversations[]` | Multi-turn sessions: `id`, `label`, `tag`, `sources`, `turns`; each is recorded as one replay session |

The pills name the tools a recorded answer must use; the live test and the eval check them. The API offers a
question only when this stack serves its sources and its pills (`kumo` needs the prediction tool).

### Validation

```bash
./scripts/demo.sh data validate     # every pack, with uv on the host
```

[`scripts/validate_packs.py`](../scripts/validate_packs.py) checks both files against the schemas, then the rules
the schemas cannot state: the id is the directory name, source ids are unique, every file glob matches a file,
question and conversation ids are unique, every question names known sources, the examples name known questions
and include every featured one, and at least one question is featured. Ingest validates the same schemas when it
syncs a pack.

## How a pack reaches the catalog

`./data/packs` is mounted read-only at `/packs` in ingest. At startup, and on `./scripts/demo.sh data sync`, ingest
syncs every pack whose digest changed ([ingestion](ingestion.md#pack-sync)): its files are copied into the
catalog sources `<pack>.<source>`, documents are parsed, chunked, embedded and indexed, tables are loaded into the
source's DuckDB file and profiled with the pack's declarations. `./scripts/demo.sh data status` shows each pack's
progress; a pack's questions are offered once its sources are `ready` or `ingesting`.

## Generators

Every file under `files/` is produced by the pack's `generator/build.py`, a PEP 723 script (its dependencies are in
its header), from a fixed seed and the authored text in `generator/content/`. Runs are deterministic: two runs
give byte-identical files. The generator also asserts the pack's planted stories, so a change that breaks an
answer fails the build, and writes the pack's README.

```bash
./scripts/demo.sh data generate retail    # uv run data/packs/retail/generator/build.py
./scripts/demo.sh data validate
./scripts/demo.sh data sync               # on a running stack: re-ingests the changed pack
```

Commit the regenerated `files/` and README with the generator change. Scanned pages are PNGs rendered from text
with seeded noise, so only a vision parser reads them.

## Adding an industry

1. Create `data/packs/<id>/` with `generator/build.py` and `generator/content/`, following an existing pack: 4 to 6
   related tables with keys and timestamps (so Kumo can predict something meaningful), 5 to 8 documents (policies,
   SOPs, reports with tables, a slide deck, at least one scanned page), and figures in the documents computed from
   the tables.
2. Write `pack.yaml` (sources, declared keys, prediction templates) and `questions.yaml` (8 to 10 questions over
   documents, SQL, both and prediction; the featured ones in `examples`).
3. `./scripts/demo.sh data generate <id>`, then `./scripts/demo.sh data validate`.
4. On a running stack, `./scripts/demo.sh data sync`; the new pack appears in the selector once ingested.
5. Ask the featured questions, check the answers against the README's answer key, then record them (below).

No service changes: the tools' schemas are data-independent, and the API and UI list whatever the catalog holds.

## Recordings

```bash
./scripts/demo.sh record --pack retail              # the featured questions
./scripts/demo.sh record --pack retail --all        # every question and conversation
./scripts/demo.sh record --pack retail --question gold-churn-risk
./scripts/demo.sh replay                            # the UI alone on every pack's recordings
```

`record` runs `demo-api record` against the running stack and writes the pack's bundle (v2) to
`data/packs/<id>/recordings/`: `index.json`, `pack.json`, `sources.json`, `sessions/<id>.json` and
`database.json` (the data viewer's schema, previews and query results, so it works in replay too). The format is
in [`api/README.md`](../api/README.md#recordings). The UI serves each pack's bundle at `/api/recordings/<pack>/...`
and replays a session without the API.

`replay` needs at least one pack with recordings; until a pack is recorded, it has nothing to show. A bundle holds
questions, answers, evidence excerpts and model names: review it before committing, and record again after a
change to the pack's files or questions.

## Licenses

Every pack is synthetic, generated by its own script, and licensed Apache-2.0 like the repository. Each pack's
`licenses` and `provenance` say so file by file.
