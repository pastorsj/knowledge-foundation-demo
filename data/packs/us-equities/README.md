# us-equities pack

Real US equities: daily prices for about 1,600 US-listed stocks, rolled up from real split-adjusted one-minute
bars, with SEC company names, CIKs and SIC codes; `intraday_scan` reads the minute bars themselves. The companies'
own SEC EDGAR 8-K filings, eCFR Title 17 and, opt-in, GDELT world headlines sit beside them as separate document
sources, searched by retrieval. It is real market data for a software demonstration and is not investment advice.

The minute bars are an **external dataset**: they are never committed. This directory holds only the pack
definition and the dataset's pinned fingerprint. Fetch the data first ([data platform](../../../docs/data-platform.md)):

```bash
# in .env: DATA_PACK=us-equities, and where the bars come from, e.g. a local copy or a bucket
DATA_SOURCE_MINUTE_BARS=/mnt/datasets/minute-bars
./scripts/demo.sh data fetch      # into $DATA_SOURCE_DIR/minute-bars, verified file by file
./scripts/demo.sh up              # builds the pack; on a running stack, also switches everything to it
```

## What is in it

| Part | Origin | Contents |
|---|---|---|
| `market_data` | external dataset `minute-bars`, plus SEC company data | 5 DuckDB tables and 3 prediction views; the raw minute bars, read in place |
| `sec_filings` | downloaded, real | 1,074 SEC EDGAR 8-Ks filed by the pack's companies from 2025-01-02 to 2026-03-12 (1,887 documents) |
| `market_regulations` | downloaded, real | eCFR Title 17 as of 2026-08-17 (3,525 sections), and the SEC's 2023 cybersecurity disclosure rule (88 FR 51896): its summary and amended form text, Form 8-K Item 1.05 and its deadline among them (5 documents) |
| `world_news` (opt-in) | external dataset `minute-bars`, read in place | 8,192 GDELT headlines, 2025-01-01 to 2026-02-02 |

Built from the pinned minute-bar dataset (fingerprint `d8b40510…`, 1.8 GB, 117 million bars for 2,200 symbols
from 2025-01-02 to 2026-03-12):

| Table | Rows | Built from |
|---|---|---|
| `trading_sessions` | 298 | dates on which at least half the symbols trading at the time have a regular-session bar |
| `assets` | 1,601 | the symbols left after the exclusions, with SEC data and a liquidity rank |
| `ticker_history` | 1,601 | one row per asset |
| `daily_prices` | 452,837 | the daily rollup (09:30 through the 16:00 closing auction) on sessions |
| `asset_relationships` | 8,862 | up to 8 most liquid peers with the same four-digit SIC code |

Of the 2,200 symbols, the importer drops 110 warrants, 50 units, 27 rights and 182 preferred shares, depositary
shares and notes (other securities of an issuer whose stock is in the data), 221 symbols SEC does not list (ETFs,
funds, and issuers delisted or renamed since: BK is BNY now), and 9 with fewer than 20 sessions. `pack.json`
records the counts under `parts.structured.import`.

The pack has **no ticker-linked news**: `analytics.news_table` is null, so `sentiment_timeline` and
`analyze_news_price_relationship` report that they are unavailable. SEC filings are not news and are never
turned into it: they stay a document source, and each passage carries its filer's `ticker` as citation metadata,
so the agent can connect a filing to a stock across the two sources.

**The filings** are pinned in `corpus/sec-edgar-issuers.manifest.json`, which `corpus/select_filings.py` wrote
from each issuer's EDGAR submissions (they list the items each 8-K reports): every 8-K in the price window that
reports Item 1.05, a material cybersecurity incident, by any company in the pack (6 filings, by Conduent,
Coinbase, Data I/O, BayFirst and Coupang), plus the latest 12 8-Ks of each of the 100 most liquid stocks. The
first `prepare --corpus` downloads them from EDGAR (1.4 GB with `SEC_USER_AGENT`); later builds reuse the cache.
Rerun the script only to change the selection; its manifest is reviewed and committed like code.

**GDELT headlines** (`world_news`) are world news, linked to no stock, and many are topic summaries rather than
article titles. They are read in place from the dataset and built only when `DATA_CORPORA` names them, e.g.
`DATA_CORPORA=sec_filings,market_regulations,world_news`.

The prediction population is the 50 most liquid stocks at the anchor (2026-03-05 21:00 UTC), ranked on sessions up
to the anchor only; the build writes their ids into `pack.json`.

## Questions

`questions.yaml` holds 30 questions; six are featured: market leaders among the 50 most liquid stocks, the
widest intraday swings from the minute bars (`intraday_scan`), unusual sessions among the 50 most liquid, the
peer network, the Form 8-K Item 1.05 rule with the filings that report an incident, and February's biggest movers
beside their own 8-Ks. Each declares the `tools` it is expected to use, as the demo scenario picker's pills
([data packs](../../../docs/data-packs.md#sources-capabilities-and-questions)). The picker offers twelve of them
(`examples`): the featured six, the five-session Kumo outlook, the SIC divisions through Auto Ontology, the
500-stock scan, Schedules 13D and 13G, NVIDIA's prices and 8-Ks, and the cyber attack headlines. Its first five
rows show every tool. The others cover every tool and source:

| Kind | Questions |
|---|---|
| Prices and returns (`market_scan`) | volatility ranking, peer-relative returns, the second half of 2025, the 500-stock scan |
| Intraday, from the minute bars (`intraday_scan`) | drawdowns from the session high, the heaviest sessions and when their volume came |
| Unusual sessions (`market_anomaly_scan`, PCA) | April 2025 against the first quarter, and the fourth quarter of 2025 across the 500 most liquid |
| Peer network (`analyze_market_relationships`, PageRank) | the most central stocks' industries |
| Kumo prediction (`predict_asset_outcomes`) | the five-session outlook, downside of more than 5%, five named stocks |
| SQL with Auto Ontology (`ask_question`) | SIC divisions, listing exchanges, days with a move of more than 10% |
| SEC filings | executive changes (Item 5.02), material agreements (1.01, 1.02), restructuring costs (2.05) |
| Regulations (eCFR Title 17) | Schedules 13D and 13G, Rule 14a-8 shareholder proposals |
| World headlines (GDELT, opt-in) | central banks, cyber attacks |
| Two sources, kept apart | NVIDIA's prices and 8-Ks, five banks' January moves and results |

Fifteen two-turn `conversations` follow up on an answer: price context then weekly with a third stock; four
large stocks then the previous quarter, monthly; early-April 2025's steepest declines then the week after; the
peer network then its central stocks' returns; the strongest peer links then the top pair; the leaders then their
unusual sessions; minute-level volatility then inside the top sessions; a Kumo outlook then the returns that
followed; SIC divisions then industries; 8-K incident filings then the Title 17 requirements; results 8-Ks then
their figures; Regulation FD; Rule 10b5-1 trading plans; headlines that mention Nvidia then NVDA's prices; and
Item 1.05 filers then their prices. With `DATA_CORPORA=sec_filings,market_regulations,world_news` and every
profile, all 45 sessions can be recorded (`demo.sh record --all`). `eval/oracles/` computes the analytics answers
from a build and `eval/retrieval.yaml` names the documents a retrieval answer should cite; the tests run the
oracles on the fixture's schema only, since the data is never in the repository.

### Recorded answers

`recordings/` holds all 45 sessions, recorded on 2026-10-01 as the hosted demo runs (every profile,
`DATA_CORPORA=sec_filings,market_regulations,world_news`, Nemotron 3 Ultra escalating to GPT-6.1 Sol) and
reviewed against `eval/oracles/` and the evidence. Eleven wrong answers were asked again, twice at most, and one
question that failed outright (it ran out of Hermes' tool-call budget) succeeded the second time.

On 2026-10-02 nine of them were recorded again the same way, once every tool result was short enough for Hermes to
read whole (it had cut long retrieval results to a preview) and the regulations source held the SEC's 2023
cybersecurity rule: `cyber-disclosure-rules`, `large-universe-scan`, `sector-sql`, `executive-changes`,
`filings-to-regulations`, `incidents-and-prices`, `material-agreements`, `nvidia-results-and-prices` and
`restructuring-costs`. Four of them were asked again, twice at most, and the bundle keeps the best attempt.
`trading-plans-follow-up` was asked three more times to check its follow-up citations live: none left a raw
evidence id, but each new first turn cited the director and officer certification as (c)(1)(i)(C) rather than
(c)(1)(ii)(C), so it keeps its earlier recording.

Later on 2026-10-02 twelve were asked again the same way, once Auto Ontology's seed imported a changed model again
(so the return convention in `ontology.yaml` reached its catalog) and filing searches stopped naming the form. Nine
replaced their recordings: `sector-sql`, `peer-network-follow-up` (its second turn now answers through Auto
Ontology), `cyber-disclosure-rules`, `executive-changes`, `incidents-and-prices`, `moves-and-filings`,
`nvidia-results-and-prices`, `restructuring-costs` and `results-follow-up`. `bank-results`, `material-agreements`
and `filings-to-regulations` keep their earlier recordings, because each new attempt was worse: one read the
banks' results 8-Ks as Item 1.05 filings, one listed three agreements as the only ones, and one second turn on the
rules lost its citations and another called Item 1.05 part of Regulation S-K. What remains:

- `cyber-disclosure-rules` (featured) gives Item 1.05's content and its four-business-day deadline from Form 8-K
  General Instruction B.1 in the 2023 rule, and names five of the six Item 1.05 filings (Data I/O twice, Coupang,
  Coinbase, Conduent): it leaves out BayFirst while calling its list the corpus's Item 1.05 filings.
- `large-universe-scan` matches the oracle's returns and volume z-scores, but calls the z-score one of dollar
  volume; the volume is shares traded.
- Smaller slips, a figure or label a viewer is unlikely to notice: an open-to-close move called close-to-close
  (`intraday-ranges`), "9 of 10" for 8 of 10 (`second-half-2025`), CrowdStrike's results for the quarter ended
  January 31, 2026 called its fiscal third quarter rather than its fourth (`moves-and-filings`), and "which
  filings" lists that name only some of the in-window filings (`executive-changes`, eight companies;
  `filings-to-regulations`, the two it compares; `material-agreements`, eight, which it says came from "32
  candidate filings", the passages its search ranked).

## Known issues

- `session_close_utc` is 21:00, the eastern-standard-time close, so bar timestamps are an hour late while daylight
  saving time is in effect. `trading_sessions.close_at` has the exact close.
- On early-close days (2025-11-28, 2025-12-24), after-hours bars up to 16:00 count as regular.
- Nasdaq share classes cannot be told from notes by their symbol (CENTA and CMSA look alike), so a class share
  that is not its issuer's primary ticker is dropped as preferred. GOOGL, the primary ticker, stays beside GOOG.
- The other way round, about 20 notes and preferreds stay: those whose ticker does not extend their issuer's stock
  ticker (AOMN beside AOMR, CCZ beside CMCSA, DTB beside DTE) or whose issuer has no stock (the CHSC and BPYP
  series), one that SEC lists first for its issuer (BIPH before BIP), and a warrant whose stock has a new ticker
  (ABPWW).
- SEC's ticker list is today's, so issuers delisted or renamed after the price window are dropped as not listed.
- The smallest stocks trade a few hundred dollars a day, and single trades give them extreme daily returns
  (tens of thousands of percent). Whole-market scans need a liquidity floor, such as the `liquid_500` universe.
- Two corporate actions show in the prices as the dataset has them. ASST's reverse-split adjustment misses
  2026-02-05, its last session before the split, which stays unadjusted between two adjusted closes: a fall of
  almost all its price and a rise back, and ASST sessions then fill an anomaly scan of `liquid_500` in 2026. AZN
  jumps on 2026-02-02, when its listing moved from American depositary shares (half a share each) to ordinary
  shares. The featured anomaly question scans `top_50`, which holds neither.
- Prices are split-adjusted, not dividend-adjusted: `adjusted_close` equals `close`.

## Files

| File | Purpose |
|---|---|
| `pack.yaml` | the manifest: the external dataset's pin, the `market` import settings, sources and analytics |
| `schema.sql`, `views/prediction.sql` | the DuckDB schema and the leakage-safe prediction views |
| `ontology.yaml` | table and column descriptions for Auto Ontology |
| `questions.yaml` | the demo questions; `featured` ones are the landing page and replay set, `examples` the picker's |
| `corpus/*.manifest.json` | the pinned eCFR snapshot, Federal Register rule and EDGAR filings (URL and SHA-256 of every file) |
| `corpus/select_filings.py` | selects the filings from the issuers' EDGAR submissions and writes their manifest |
| `eval/` | SQL oracles for the analytics answers, the documents retrieval answers cite, the answer checks (`answers.yaml`) and the GPU guard's cases (`perf.yaml`) of the on-demand checks ([eval](../../../eval/README.md)); never read at runtime |
| `recordings/` | the replay bundle of all 45 sessions, 30 questions and 15 conversations (`demo.sh record --all`, [recordings](../../../docs/data-packs.md#recordings)); its figures and sample rows come from the external dataset, never the minute bars. Private: the public repository ships `synthetic-market`'s recordings only |
