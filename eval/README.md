<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# eval

On-demand checks of a running deployment, which you run by hand and CI never does: the answer-quality eval
(`./scripts/demo.sh eval`) and the GPU performance guard (`./scripts/demo.sh test gpu --perf`). Both read their
cases from the active data pack, beside its oracles: `data/packs/<pack>/eval/answers.yaml` and `eval/perf.yaml`.
The third on-demand command, the live end-to-end test (`./scripts/demo.sh test live`), is in
[`ui/e2e-live/`](../ui/e2e-live/live.spec.ts); [operations](../docs/operations.md#on-demand-checks) covers all
three.

`demo-eval` is a uv project (Python 3.12, PyYAML, the standard library for HTTP). It reaches a deployment the way a
browser does, through its UI: `GET /api/v1/pack`, the job routes and `POST /api/v1/data_sources/<id>/query`, so the
same command works on the host (`http://127.0.0.1:3100`), through an SSH tunnel, or through a link to the UI.

## Answer-quality eval

```bash
./scripts/demo.sh eval                                   # every question with answer checks, once
./scripts/demo.sh eval --runs 2 --questions market-leaders,peer-network
./scripts/demo.sh eval --pack us-equities --url http://127.0.0.1:3100   # stops unless that pack is active
```

Each question runs as a fresh job with its own sources, as the UI's cards send it, one at a time, so every run
costs model calls; `--max-wait` (default 1,260 s) cancels a job that runs longer. By default the eval asks every
question the pack's `answers.yaml` checks (`synthetic-market`: 10, `us-equities`: 9), or the featured ones when
there is no `answers.yaml`. Then it scores each run:

| Check | What passes |
|---|---|
| `success` | the job succeeded |
| `cited` | the report cites at least one receipt of the run |
| `declared_tools` | the run used every tool pill the question declares in `questions.yaml` (the picker's pills) |
| `required_tools` | each tool group of the question's `answers.yaml` entry had one tool called |
| the question's own | in `answers.yaml`: the oracle's strongest and weakest named, a return shown as the right percentage, a caveat stated, the Form 8-K Item 1.05 deadline right or flagged as missing, and so on |

The oracles are the pack's `eval/oracles/*.sql`, run read-only on the deployment's own build through the API's
query route, so the reference values always come from the data the agent's tools read. The checks need no model.

It prints a summary table and writes everything to a run directory (default `eval/runs/<pack>-<UTC time>/`,
which git ignores): `meta.json`, `oracles.json`, `names.json`, `runs/<qid>.<n>.json` (each job's status, wall
time and export), `grades/` with the grader on, `scores.json` and `report.md`. `uv run --project eval demo-eval
report DIR` scores a run directory again after a change to the checks, without asking anything. A run directory
holds the deployment's answers and evidence: for a pack of real market data, keep it off the repository and delete
it when you are done.

Exit codes: 0 every run passed, 1 a run failed, 64 the request does not fit the deployment (another pack, an
unknown question, a half-configured grader), 69 the deployment did not answer.

### The optional grader

Off by default. It turns on when the environment holds all three of:

| Variable | Meaning |
|---|---|
| `GRADER_BASE_URL` | an OpenAI-compatible endpoint, e.g. `https://api.openai.com/v1` (it calls `/chat/completions`) |
| `GRADER_API_KEY` | its key |
| `GRADER_MODEL` | its model id |
| `GRADER_SAMPLES` | optional: samples per run, majority vote (default 3) |

The grader must be a frontier model, such as GPT-6 Sol or Claude Opus 5.5, the two graders of the bake-off in
[models and routing](../docs/models-and-routing.md). It has to check every number, window and unit in a report
against the receipts; smaller models miss unsupported claims and fractions shown as percentages, which is what it is
there to catch. It grades blind, with the bake-off judge's prompt: it sees whether the market data is synthetic or
real, the question, the reference facts (`answers.yaml` `facts`, filled from the oracles), a digest of the run's
receipts and the report, never the model or the route. A run then passes only when its deterministic checks pass
and the grader's majority says pass. It asks for JSON-schema output, and on an endpoint without it, for JSON in the
prompt.

The key is read from the environment only: never from `.env`, never from a command-line argument, and never
printed or written to the run directory. Keep it out of your shell history, for example:

```bash
read -rs GRADER_API_KEY && export GRADER_API_KEY GRADER_BASE_URL=https://api.openai.com/v1 GRADER_MODEL=<model id>
./scripts/demo.sh eval
```

### `answers.yaml`

One entry per question id:

```yaml
dataset: "synthetic: fictional issuers, prices and company news made for the demo"   # what the grader is told
names: SELECT asset_id, company_name FROM main.assets WHERE asset_id IN ({ids})      # for `named`
format: {signed_percent: [total_return], percent: [daily_volatility]}               # how facts show fractions
questions:
  market-leaders:
    oracles:
      market_leaders: {}                                   # eval/oracles/market_leaders.sql
      laggards: {sql: market_leaders, order: ORDER BY total_return ASC, limit: 5}
    tools: [[market_scan]]                                 # every group needs one of its tools called
    checks:
      - {id: strongest_named, named: "market_leaders[0].asset_id"}
      - {id: strongest_return_exact, percent: "market_leaders[0].total_return"}
    facts: "Strongest first: {market_leaders[:5]: asset_id, total_return}."
```

A check names oracle rows as `<oracle>[<rows>].<field>`, where rows is an index (`0`, `-1`), a slice (`:3`, `-3:`,
`:`) or `max(<field>)`. The check kinds:

| Kind | Passes when |
|---|---|
| `named: REF` | every referenced asset is named, by ticker or company name; `at_least: N` or `any: true` relaxes it |
| `percent: REF` | every referenced fraction appears as a percentage, within display rounding |
| `pattern: REGEX` (or a list: any) | the report matches, read without markdown emphasis (`**not**` as `not`); `(?i)` for case-insensitive |
| `item_105_deadline: true` | the four-business-day deadline is stated, or flagged as missing from the evidence, and no other deadline is given ([`deadline.py`](src/demo_eval/deadline.py)) |
| `retrieved_source: ID` | a retrieval call returned hits from that source |
| `retrieved_filing: true` | a retrieval call returned a passage of a filing that the pack's `eval/retrieval.yaml` lists for the question |
| `percent_grounding: SHARE` | at least that share of the report's percentages match a number in the receipts |
| `prediction_named: N` | the Kumo prediction's top N assets are named |
| `prediction_first: true` | its most probable asset is the first of them the report names |

`tests/test_spec.py` checks every pack's file against its questions, the tool registry and the oracles' columns.

## GPU performance guard

```bash
./scripts/demo.sh test gpu          # the CPU/GPU parity tests of market analytics
./scripts/demo.sh test gpu --perf   # then the speedups of the running analytics-gpu stack
```

On a host without an NVIDIA GPU both skip and succeed. Otherwise `test gpu` runs the market-analytics parity tests
(`pytest -m gpu` in a RAPIDS venv of about 9 GB, synced on the first run), and `--perf` then measures the running
stack, which needs `analytics-gpu` in `COMPOSE_PROFILES`:

- **Market tools.** Each case of the active pack's `eval/perf.yaml` goes to market analytics' `POST /benchmark`
  (the route behind the UI's Benchmark tab), which runs the call once untimed on each engine, then in five
  alternating CPU/GPU pairs, and compares the results as the parity tests do. A case fails when the results differ,
  when the GPU engine is not RAPIDS (cudf.pandas, cuml.accel, nx-cugraph), or when the speedup, the median CPU time
  over the median GPU time on the tools' own compute timers, stays below its floor in two measurements.
- **Milvus.** With the retrieval profile, the guard measures the index comparison again
  (`demo-retrieval benchmark --guard`, the CPU HNSW index against its `GPU_IVF_FLAT` copy, from the build's own query
  vectors) into `retrieval-benchmark-guard.json`, and fails a workload profile whose recall and agreement gates fail
  or whose search-time ratio is below its floor; each row shows the recall and overlap it measured. The Benchmark tab
  serves `retrieval-benchmark.json`, which only the one-shot of `up` and `data reindex` writes, so the guard never
  changes it. If measuring again fails, every profile fails: the guard's file then still holds an earlier
  measurement, which `--measured-since` (the time `demo.sh` started measuring) rejects. Why the recall gates no longer
  move between runs: [retrieval](../docs/retrieval.md#why-recall-moved-between-runs).

The cases are written for one build profile (`profile:` in `perf.yaml`); on another the market cases are skipped.
It prints one row per case (CPU and GPU milliseconds, the speedup, the floor, the recorded speedups) and exits 1 if
any case fails, 69 if the stack has no GPU service to measure.

### Thresholds

Every case lists the speedups recorded on a 40 GB A100 (12 vCPUs), and its floor is **half its lowest recorded
speedup, rounded down to 0.05** (`tests/test_spec.py` enforces the rule). Half leaves room for run-to-run noise on a
shared host: the same 2,000-issuer anomaly scan measured 1.16x, 1.51x and 2.43x through `POST /benchmark`. It still
catches the regressions seen so far: cudf.pandas falling back to pandas made the GPU 3 to 10 times slower than the
CPU, and a CAGRA index found none of the right neighbors (the recall gate). A case recorded slower on the GPU has no
floor and is only reported.

| Pack (profile) | Case | Recorded on the A100 | Floor |
|---|---|---|---|
| `synthetic-market` (standard) | `market_scan`, 2,000 issuers, 2024 to 2026 | 3.0x, 3.74x, 3.96x | 1.5x |
| | `market_scan`, 2,000 issuers, volume z-score | 2.8x | 1.4x |
| | `market_scan`, 12 issuers, 20 sessions | 1.5x, 1.61x, 1.80x | 0.75x |
| | `market_anomaly_scan`, 2,000 issuers | 2.5x, 2.43x, 1.51x, 1.16x | 0.55x |
| | `market_anomaly_scan`, 12 issuers | 1.8x | 0.9x |
| | `price_context`, 11 story issuers | 2.3x | 1.15x |
| | `analyze_market_relationships` | 1.0x, 1.23x, 1.24x | 0.5x |
| | `analyze_news_price_relationship`, every issuer | 8.1x (an earlier pack's larger news table) | reported only |
| | Milvus single, batch, concurrent | 1.18x, 1.15x, 1.12x | 0.55x each |
| `us-equities` (default) | `intraday_scan`, 50 stocks, 9 sessions, by range | 4.1x, 4.11x | 2.05x |
| | `intraday_scan`, 50 stocks, February, by drawdown | 4.23x | 2.1x |
| | `intraday_scan`, 50 stocks, 4 sessions, by volume | 3.87x | 1.9x |
| | `market_scan`, every stock, January 2025 to March 2026 | 2.69x, 2.65x, 2.66x, 2.49x | 1.2x |
| | `market_scan`, every stock, 20 sessions | 1.58x, 1.57x, 1.57x, 1.53x | 0.75x |
| | `market_anomaly_scan`, every stock | 1.81x, 1.65x, 1.9x, 1.79x | 0.8x |
| | `market_scan` and `market_anomaly_scan`, 50 stocks | 0.78x to 0.89x, 0.69x to 0.73x (the CPU wins at this size) | reported only |
| | Milvus single, batch, concurrent | 1.13x and 1.22x, 1.11x and 1.20x, 1.76x and 1.07x | 0.55x, 0.55x, 0.5x |

The speedups come from the isolated method of [operations](../docs/operations.md#brev-vm-mode) (step 7) and the
[market-analytics README](../tools/market-analytics/README.md#cpu-and-gpu-timings), on 2026-09-29 and 2026-09-30,
and from `POST /benchmark` on the running stack for the recorded sessions' calls, on 2026-10-01, and for
`us-equities`' daily `market_scan` and `market_anomaly_scan` cases, on 2026-10-02
([why the 50-stock cases are reported only](../tools/market-analytics/README.md#daily-tools-on-us-equities)). To add a case,
measure it on the GPU host a few times, list the speedups in `recorded`, and set `min_speedup` by the rule.

## Test

```bash
uv run --directory eval pytest   # offline: stand-in deployments, graders and analytics services
```
