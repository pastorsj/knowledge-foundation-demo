<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Data packs

A data pack is one directory under `data/packs/` that describes a whole demo world: its tables, its document
corpora, the sources the UI and the agent see, the analytics and prediction settings, the demo questions and
the recorded sessions. The `demo-data` builder turns the selected pack into files under `/data` and points
`/data/active` at the result. Services read only `/data/active`, so swapping the data means adding a pack, not
changing code.

The format, the runtime layout and the builder's commands are specified in
[`data/README.md`](../data/README.md). How external data is fetched, imported and scaled is in
[data platform](data-platform.md). This page is the guide.

## The packs

The repository ships two packs in one format: the same tables, built by the same importer from a raw market
dataset. In both, SEC EDGAR filings are a separate document source, searched by retrieval. They are never
converted into news events or written to a news table: the demo shows the agent combining a price database and
a document corpus, each through its own tool.

| | [`synthetic-market`](../data/packs/synthetic-market/README.md) (default) | [`us-equities`](../data/packs/us-equities/README.md) (optional; the demo deployment's pack) |
|---|---|---|
| Prices | Fictional issuers (2,000 at the default `standard` profile), seeded prices; one-minute bars in the `ci` and `intraday` profiles | About 1,600 real US stocks, January 2025 to March 2026, rolled up from real one-minute bars that stay outside the repository |
| Company data | Names and profiles written by Nemotron with NeMo Data Designer, checked against SEC's ticker lists | SEC names, CIKs and SIC codes |
| Ticker-linked news | `company_news`: seeded events with Nemotron headlines, and 12 planted stories | None: `sentiment_timeline` and `analyze_news_price_relationship` report that they are unavailable |
| `sec_filings` | 1,000 real 8-K and 6-K filings from 2026 Q2, by any filer (the issuers are fictional) | 1,074 8-Ks filed by the pack's own companies over its price window |
| Other documents | `market_regulations`: eCFR Title 17 and the SEC's 2023 cybersecurity disclosure rule (Form 8-K Item 1.05 and its deadline) | `market_regulations`, and `world_news`: 8,192 GDELT headlines (opt-in) |
| Needs | Nothing for the structured part; `SEC_USER_AGENT` for `sec_filings` | `demo.sh data fetch` first; `SEC_USER_AGENT` for company data and `sec_filings` |

The synthetic market has planted facts, so answers can be checked: `eval/oracles/*.sql` computes them from a
build. The downloaded documents are fetched at build time from pinned URLs and checked against their SHA-256;
they are never committed. `us-equities`' data reaches a machine through a fetch step:

```bash
# .env: DATA_PACK=us-equities, and DATA_SOURCE_MINUTE_BARS=<a directory, host:/path or URL>
./scripts/demo.sh data fetch          # into $DATA_SOURCE_DIR/minute-bars, verified against the pinned manifest
./scripts/demo.sh up                  # builds the pack; on a running stack, also switches everything to it
```

The hosted demo deployment runs `us-equities` with every corpus
(`DATA_CORPORA=sec_filings,market_regulations,world_news`), so its visitors see real prices. `synthetic-market` stays the default in `.env.example` and CI because
`us-equities`' data cannot be committed: a fresh clone builds and replays the synthetic pack with nothing to fetch.

## How a pack is built

```text
./scripts/demo.sh up                  # runs the data one-shots as dependencies
./scripts/demo.sh data prepare        # rebuild the active pack, e.g. after changing its files or DATA_CORPORA
```

| Step | Service | Writes |
|---|---|---|
| Structured part | `data` (core) | `tables/*.parquet`, `structured/<database>.duckdb`, `ontology/model.yaml`, `prediction/`, `pack.json` |
| Corpus | `data-corpus` (retrieval) | `corpus/documents.jsonl` |
| Index | `retrieval-index` (retrieval) | the Milvus collection and `collection-manifest.json` |

Each build lives in `/data/builds/<pack>@<version>+<profile>+<digest>`. The digest covers the pack's files
(not its `README.md`, `eval/`, `recordings/` or `tests/`), the profile, the selected corpora and the builder
itself, so preparing an unchanged pack is a no-op and any change starts a new build. `/data/active` switches
atomically. `data prepare` also restarts market analytics and retrieval, which keep the build they resolved at
startup, and recreates the sandbox when the new build changes the tools' schemas (Hermes lists them once, when
the sandbox starts). After changing `DATA_PACK` or `DATA_PACK_PROFILE` on a running stack, run
`./scripts/demo.sh up`, which does all of that and also recreates what names the pack's database.

`pack.json` in the build is what services read: the sources and questions this build can serve, the database
name and paths, the analytics and prediction settings, the minute bars' location, and the digests of every file.

Settings (`.env`, [configuration](configuration.md#4-data-pack)): `DATA_PACK`, `DATA_PACK_PROFILE`,
`DATA_CORPORA` and `SEC_USER_AGENT`. An empty `DATA_CORPORA` builds every corpus the pack does not mark `opt_in`:
`sec_filings` and `market_regulations` in both packs. Add `world_news` to build the GDELT headlines of
`us-equities`.

Other commands, all run in the data image:

```bash
./scripts/demo.sh data validate       # the pack's schema, cross-references and tool contracts
./scripts/demo.sh data verify         # the active build still matches the digests in its pack.json
./scripts/demo.sh data list           # packs and builds
./scripts/demo.sh data clean [--all]  # remove inactive builds and unused caches (--all: every cache)
./scripts/demo.sh data fetch          # fetch and verify the pack's external datasets
./scripts/demo.sh data generate       # write synthetic-market's Nemotron text (rarely; needs a key)
./scripts/demo.sh data reindex        # rebuild only the retrieval index
```

## Sources, capabilities and questions

Each source in `pack.yaml` declares its `capabilities`, which are tool families from
`contracts/tool-registry.json`: `unstructured_retrieval`, `market_analytics`, `structured_retrieval`,
`structured_prediction`. The API offers a source with only the capabilities the running tools provide, and a
job's selected sources decide which tools the agent gets. For example, `market_data` offers
`structured_retrieval` only when the ontology profile runs.

A tool whose data a pack lacks stays registered, so the registry, the sandbox policy and the UI never change with
the pack. Its MCP description starts with "Unavailable in the active data pack", and a call fails at once with
`news_unavailable` (no ticker-linked news table) or `minute_bars_unavailable` (no minute bars, as in
`synthetic-market`'s daily-bar profiles). The market skill tells the agent not to call it.

`questions.yaml` holds each pack's demo questions. A question is offered only when every source it names is in
the build and, if it lists `profiles`, the build uses one of them. Each declares the `tools` it is expected to use
as technology pills, which the composer's demo scenario picker shows beside its label: `cudf` for any market tool,
plus `cuml` for `market_anomaly_scan` and `cugraph` for `analyze_market_relationships`; `kumo`
(`predict_asset_outcomes`), `retrieval` (`retrieve_evidence`, one pill whatever the sources) and `ontology`
(`ask_question`). The names are the tool registry's `pills` ([contracts](../contracts/README.md#tool-registry)), and
a test fails when a recorded answer did not use every tool its question declares (questions without a recording are
skipped). `tag` (ANALYTICS, RETRIEVAL, ...) only groups questions in the docs. Six are `featured` in each pack: they need
only the default profiles, fit the landing page on one screen, and are what `record` asks by default. Every
analytics question has an oracle in `eval/oracles/`, and `eval/retrieval.yaml` names the documents a retrieval
answer should cite. `eval/answers.yaml` holds the answer checks of `demo.sh eval`, and `eval/perf.yaml` the GPU
guard's cases ([eval](../eval/README.md)).

`examples` in `questions.yaml` lists, in order, the questions the composer's "Choose an example" picker offers:
at most 12, every featured question among them, and together declaring every tool the pack's questions declare
(`data validate` checks all three). The picker shows five rows and scrolls for the rest, so the first five
should show the widest mix of tools. A build offers the examples it serves (their sources built, their profiles
matching), and `GET /v1/pack` those whose sources the running tools serve. A pack without the list offers its
featured questions, then the others, up to 12. Every question stays in the pack for the landing page links, `record`
and `eval`, whether or not it is an example. `us-equities` lists its six featured questions and one question for
each tool and source they leave out (Kumo, Auto Ontology, the whole-market scan, regulations, NVIDIA's prices beside
its filings, world headlines); `synthetic-market` lists all eleven.

`questions.yaml` can also hold `conversations`: two to six turns asked in order in one conversation, so a later
turn can refer to an earlier answer ("For those same two examples, ..."). They follow the same source and profile
rules, share the questions' ids, are not listed in the UI, and are recorded by `record --all`, each as one replay
session. `us-equities` has 30 questions and 15 conversations, 45 sessions in all.

`documents.benchmark_queries` in `pack.yaml` lists held-out queries for the Benchmark tab's CPU/GPU Milvus
comparison on a GPU host, each with the document sources it searches; they are never demo questions. A build keeps
those whose sources it indexed ([retrieval](retrieval.md#cpugpu-index-comparison-analytics-gpu)). Each pack has 15.

## Recordings

The replay bundle lives with its pack, in `data/packs/<pack>/recordings/`, and is committed. It is written by:

```bash
./scripts/demo.sh record                          # the featured questions
./scripts/demo.sh record --all                    # every question and conversation the build offers
./scripts/demo.sh record --question market-leaders --question peer-network
```

`record` asks each question on the running stack, one at a time, and each conversation's turns in order in one
conversation. A whole set (the featured questions, or `--all`) replaces the bundle; `--question` re-records only
the named questions or conversations and keeps the bundle's other sessions. A question or conversation that does
not succeed is left out (a re-recorded one keeps its earlier session), and the command exits 1. Review the files before committing: they hold questions, answers, evidence
excerpts and model names. `./scripts/demo.sh replay` then serves the UI on the bundle alone, and stops with a
hint when the pack has none. The format is in [`api/README.md`](../api/README.md#recordings).

The replays list shows, under each recorded session, pills for the tools its runs actually used (the union over
its turns). `record` writes them into `index.json`; for a bundle recorded before, the UI derives them from the
recorded events. A market tool that ran on the CPU shows its CPU library (pandas, scikit-learn or NetworkX) in the
same RAPIDS color, not the GPU name.

Both packs are recorded: `synthetic-market`'s ten questions of its default (`standard`) profile, and every
`us-equities` session, its 30 questions and 15 two-turn conversations (45 sessions, 60 answers). The
synthetic pack's eleventh question, `intraday-ranges`, needs a minute-bar profile (`ci` or `intraday`), so its
bundle leaves it out.

```bash
./scripts/demo.sh replay                          # synthetic-market, the default
DATA_PACK=us-equities ./scripts/demo.sh replay
```

Both were recorded on 2026-10-01, and some of their sessions again on 2026-10-02 (each pack's README names
them), on a Brev A100 VM with every profile, including `ontology`, the local Kumo
NIM and the GPU Milvus, so every answer that called the market tools carries its CPU/GPU comparison for the
Benchmark tab, and every answer that searched documents carries the Milvus CPU/GPU index comparison
(`retrievalBenchmark`). `synthetic-market` was recorded with the `.env.example` models and corpora (Nemotron 3
Ultra alone, on build.nvidia.com), as a public user runs it. `us-equities`, the hosted demo's pack, was recorded
as that deployment runs: `DATA_CORPORA=sec_filings,market_regulations,world_news` and Nemotron 3 Ultra escalating
to GPT-6.1 Sol (`escalation.nemotron-gpt`) on an OpenAI-compatible endpoint serving both. The recorder writes
served model ids without a gateway's provider prefix, keeping the last segment of the id the endpoint served. Every answer was reviewed against
the pack's oracles and evidence; the ones that were wrong were asked again, and the bundles keep the faults that
remained rather than hiding them (each pack's README lists them).
The `us-equities` bundle holds results derived from its external dataset: the answers' figures and, in
`database.json`, the first rows of each table (a few dozen rows of daily prices), never the minute bars.

## Adding or swapping a pack

1. Copy `data/packs/us-equities` (bars you fetch) or `data/packs/synthetic-market` (a generator) to
   `data/packs/<new-id>`. For your own bars, pin their dataset under `external` and describe their layout under
   `market.bars` ([data platform](data-platform.md)).
2. Edit `pack.yaml` (identity, licenses, provenance, sources, disclaimer, analytics universes, prediction),
   `schema.sql`, `ontology.yaml` and `questions.yaml`. Only the columns in the tool contracts are mandatory.
3. Set `DATA_PACK=<new-id>` in `.env`, then run `./scripts/demo.sh data validate` and
   `./scripts/demo.sh up`. Record its sessions with `./scripts/demo.sh record`.

Auto Ontology answers from the descriptions in `ontology.yaml`. After you edit them, the next `up` rebuilds the
model and seeds the pack's catalog again ([seeding](../tools/auto-ontology/README.md#seeding)).

No code changes are needed while the pack satisfies the contracts its tools own. Market analytics requires what
`tools/market-analytics/contract/market-analytics.v1.json` lists (a pack declares
`analytics.contract: market-analytics/v1`); `validate` checks the declared schema and `prepare` checks the
built tables. The database name is the pack id in snake case.

## Licenses

- The synthetic market data and its Nemotron-written text are Apache-2.0.
- `us-equities`' minute bars and GDELT headlines are a private dataset, used as provided and never committed or
  redistributed here.
- eCFR is United States government public information. The eCFR is authoritative but is not the official legal
  edition of the CFR. The Federal Register rule is United States government public information too.
- SEC EDGAR content falls under the SEC's website reuse terms, and issuer-authored content may carry its own
  rights; its redistribution terms are unknown. The filings are fetched at build time and never committed.
