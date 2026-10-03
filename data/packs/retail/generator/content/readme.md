<!-- SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- This file is the template of ../README.md: generator/build.py fills the {{placeholders}} from the generated tables, so the answer key cannot drift from the data. Edit this file, not README.md. -->
# retail pack

**Lumen Retail Group**, a fictional omnichannel home-and-lifestyle retailer: 24 stores in four US regions (Northeast,
Southeast, Midwest, West) plus the online store Lumen.com. The data is current to **2026-09-30**.

**Everything in this pack is synthetic.** The company, its stores, products, customers, suppliers, documents and every
figure are invented by `generator/build.py`; any resemblance to a real company or person is coincidental. It is a
demonstration dataset, not retail advice.

## What is in it

| Source | Kind | Files | What it holds |
|---|---|---|---|
| `sales` (Sales & Customers) | structured, DuckDB + Kumo | 6 CSV, 1 XLSX | Stores, products, customers, orders, order lines and returns for April 2025 to September 2026, and the merchandising plan workbook (category targets, promo calendar) |
| `policies` (Policies & Procedures) | documents | 2 PDF, 1 DOCX, 1 PNG | Return and refund policy, store operations SOP, supplier terms, and a scanned loss-prevention memo |
| `reports` (Merchandising & Performance Reports) | documents | 1 PDF, 1 PPTX | The Q3 2026 regional performance report and merchandising review |

## Data card

- **Origin.** Generated, synthetic, Apache-2.0. `uv run data/packs/retail/generator/build.py` rewrites everything under
  `files/` (and this README) from seed **{{seed}}** (`numpy.random.default_rng` streams spawned from one `SeedSequence`).
  The run is deterministic: two runs give byte-identical files (reportlab `invariant=1`, fixed Office core properties
  and zip timestamps, seeded scan noise).
- **Tables.** {{total_rows}} rows in total, order history from {{first_order}} to {{last_order}} (18 months, so Kumo has
  history to learn from). The tables are a loyalty-member sample of Lumen's transactions, which is why absolute dollars
  are modest.
- **Time.** Fiscal quarters are calendar quarters. Q3 2026 is July 1 to September 30, 2026. The Kumo anchor time is
  `2026-09-30T00:00:00Z`.
- **Planted stories** (so the questions have clear answers): the Chicago Loop flagship (S13) sold electronics that were
  returned at an unusual rate after late June 2026; three stores ({{lp_store_ids}}) have return rates more than twice the
  chain rate and are named in the scanned memo; three categories missed their Q3 plan; one region clearly led year-over-
  year growth. `build.py` asserts each of these, so a change to the generator that breaks one fails the build.

### Tables

| Table (DuckDB name) | Rows | Key | Time column | Foreign keys |
|---|---|---|---|---|
| `stores` | {{rows_stores}} | `store_id` | | |
| `products` | {{rows_products}} | `product_id` | | |
| `customers` | {{rows_customers}} | `customer_id` | `joined_at` | `home_store_id` -> stores |
| `orders` | {{rows_orders}} | `order_id` | `ordered_at` | `customer_id` -> customers, `store_id` -> stores, `promo_id` -> `merchandising_plan_promo_calendar` |
| `order_items` | {{rows_order_items}} | `order_item_id` | | `order_id` -> orders, `product_id` -> products |
| `returns` | {{rows_returns}} | `return_id` | `returned_at` | `order_item_id` -> order_items, `order_id` -> orders, `customer_id` -> customers, `product_id` -> products, `store_id` -> stores |
| `merchandising_plan_category_targets` | {{rows_category_targets}} | `target_id` | `quarter_start` | |
| `merchandising_plan_promo_calendar` | {{rows_promo_calendar}} | `promo_id` | `start_date` | |

The last two tables are the two sheets of `merchandising_plan.xlsx` (`category_targets`, `promo_calendar`); ingest names a
workbook's tables `<file stem>_<sheet>`. Keys, time columns, foreign keys and column descriptions are declared in
`pack.yaml`. Net sales is `SUM(orders.net_amount)` (after discounts, before returns); an order line's net value is
`order_items.line_amount`. `returns.returned_amount` is the value of the returned units before any restocking fee, and
`returns.condition` is `opened`, `unopened` or `defective`. The online store is `store_id = 'WEB'` (region `E-commerce`).

### Files

| File | Shows |
|---|---|
| `files/sales/stores.csv`, `products.csv`, `customers.csv`, `orders.csv`, `order_items.csv`, `returns.csv` | The relational model; Kumo reads `customers`, `orders` and `returns` |
| `files/sales/merchandising_plan.xlsx` | A multi-sheet workbook: every sheet becomes a table (`category_targets`, `promo_calendar`) |
| `files/policies/return-refund-policy.pdf` | 3-page policy with a **restocking fee table** (category, window, fee by condition) |
| `files/policies/store-operations-sop.pdf` | Multi-page SOP with tables, numbered procedures and the point-of-sale returns steps |
| `files/policies/supplier-terms.docx` | DOCX contract summary with a payment terms and rebate table for ten suppliers |
| `files/policies/loss-prevention-memo.png` | A **scanned memo** (grayscale, rotated about 1.4 degrees, noise and dust): only an OCR/VLM parser such as Nemotron Parse can read it |
| `files/reports/regional-performance-q3-2026.pdf` | 4-page report with tables and a chart whose figures are computed from the tables |
| `files/reports/q3-2026-merchandising-review.pptx` | 9-slide deck with KPI cards, tables, a bar chart and speaker notes |

### Prediction templates

All three are binary Kumo PQL over `customers`, `orders` and `returns`, anchored at `2026-09-30T00:00:00Z`:

| Template | PQL |
|---|---|
| `churn_90d` | `PREDICT COUNT(orders.*, 0, 90, days) = 0 FOR EACH customers.customer_id` |
| `return_risk_30d` | `PREDICT COUNT(returns.*, 0, 30, days) > 0 FOR EACH customers.customer_id` |
| `high_value_30d` | `PREDICT SUM(orders.net_amount, 0, 30, days) > 300 FOR EACH customers.customer_id` |

## Questions

Ten questions in `questions.yaml`; the six featured ones are listed first in `examples`.

| Id | Tag | Pills | Featured |
|---|---|---|---|
| `return-window-fees` | DOCUMENTS | retrieval | yes |
| `no-receipt-limits` | DOCUMENTS | retrieval | yes |
| `regional-growth-report` | DOCUMENTS | retrieval | |
| `top-stores-q3` | SQL | duckdb | yes |
| `category-vs-plan` | SQL | duckdb | |
| `category-return-rate` | SQL | duckdb | |
| `electronics-returns-fee` | HYBRID | retrieval, duckdb | yes |
| `monitored-store-returns` | HYBRID | retrieval, duckdb | yes |
| `gold-churn-risk` | PREDICTION | kumo | yes |
| `return-risk-30d` | PREDICTION | kumo | |

## Answer key

Computed by `build.py` from the generated files. Money is in US dollars.

1. **`return-window-fees`** (return-refund-policy.pdf, section 2 table). Electronics: **30 days**, **15% restocking
   fee** on opened or used items. Furniture: **45 days**, **20% restocking fee** on opened or used items. Defective or
   damaged items are **never charged** the fee (furniture pickup is also free). Lumen Rewards Gold members get 15 extra
   days.
2. **`no-receipt-limits`** (loss-prevention-memo.png, scanned memo AP-2026-031 of August 12, 2026). A no-receipt return is
   limited to **$75.00 per transaction**, refunded as store credit only; a customer may make no more than **3** no-receipt
   returns in any rolling **90 days**; at stores on enhanced monitoring ({{lp_store_ids}}) the limit is **$50.00**.
3. **`regional-growth-report`** (regional-performance-q3-2026.pdf, Table 1). {{a_region_growth}}. Year-over-year ranking:
   {{a_region_rank}}; {{a_region_note}}.
4. **`top-stores-q3`** (SQL over `orders` joined to `stores`, `store_type <> 'ecommerce'`, `ordered_at` from 2026-07-01 to
   2026-09-30). {{a_top5}}. The online store WEB would be first with {{a_web_rank_note}} if it were counted; the gap from
   fifth to sixth is large.
5. **`category-vs-plan`** (SQL: Q3 2026 `order_items.line_amount` by `products.category`, against
   `merchandising_plan_category_targets` for `quarter = '2026-Q3'`). Missed plan: {{a_missed}}. Beat plan: {{a_beat}}.
6. **`category-return-rate`** (SQL: `returns.returned_amount` with `returned_at` in Q3 2026 divided by Q3 2026 net sales,
   by category). Highest: {{a_cat_top}}. Full ranking: {{a_cat_rate_rank}}.
7. **`electronics-returns-fee`** (hybrid: return policy section 2 plus SQL on `returns`, `products` and `stores`, physical
   stores only, `returned_at` in Q3 2026). {{a_elec}}. For reference: returns at the top stores {{a_elec_runner}}; the
   top store's condition mix is {{a_elec_cond}}; the online store processed {{a_elec_web}} electronics returns and is
   excluded because it is not a physical store. The policy fee is 15% on opened or used electronics, nothing on
   unopened or defective items.
8. **`monitored-store-returns`** (hybrid: the memo names {{lp_stores}}; SQL on `returns`, `returned_at` in Q3 2026).
   {{a_lp_q3}}. The memo's basis: {{a_lp_rates}} (January to June 2026, returned value divided by net sales).
9. **`gold-churn-risk`** (Kumo, template `churn_90d` with `WHERE customers.tier = 'gold'`). Kumo returns a probability per
   customer, so there is no fixed answer; check the shape. There are {{a_n_gold}} gold customers ({{a_n_tier}} in all
   tiers). As a backtest at anchor 2026-07-01, the share of customers with no order in the next 90 days was
   {{a_churn_gold}} for gold, {{a_churn_silver}} for silver and {{a_churn_bronze}} for bronze ({{a_churn_all}} overall),
   so gold customers should score well below bronze on average. {{a_gold_lapsed}} gold customers have placed no order in
   the 90 days before the anchor and should be expected near the top of the ranking.
10. **`return-risk-30d`** (Kumo, template `return_risk_30d`). Probabilities per customer; the highest should be customers who
    ordered recently (returns follow orders by 2 to 5 weeks) and customers with earlier returns ({{a_return_repeat}}
    customers have three or more). Backtest at anchor 2026-08-30: {{a_return_backtest}} returned an item in the next 30
    days. The `high_value_30d` template has a backtest base rate of {{a_hv_backtest}} (more than $300 in 30 days).

## Regenerating and checking

```bash
uv run data/packs/retail/generator/build.py          # rewrites files/ and this README
uv run --with jsonschema --with pyyaml python -c "import json,yaml,jsonschema; \
jsonschema.validate(yaml.safe_load(open('data/packs/retail/pack.yaml')), json.load(open('data/schemas/pack.schema.json'))); \
jsonschema.validate(yaml.safe_load(open('data/packs/retail/questions.yaml')), json.load(open('data/schemas/questions.schema.json'))); print('valid')"
```

Authored text lives in `generator/content/` (Markdown with double-brace placeholders); `generator/datagen.py` makes the
tables and `generator/render.py` renders PDF, DOCX, PPTX and the scan. This README is generated from
`generator/content/readme.md` by `build.py`, so the answer key always matches the data; edit the template, not this file.
