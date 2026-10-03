<!-- SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
---
title: Regional Performance Report, Q3 2026
subtitle: Net sales, orders, returns and store results for July 1 to September 30, 2026
doc_id: RPT-REG-2026Q3
version: 1.0
effective: Issued October 1, 2026
owner: Regional Operations and Finance
classification: Regional Operations Directors, store managers, merchants, executive team
department: Regional Operations and Finance
footer: Internal use only. Synthetic document for a software demonstration.
---

# 1. Executive summary

Lumen Retail Group's loyalty members placed {{q3_orders}} orders in the third quarter of 2026, worth {{q3_net_sales}}
in net sales, {{q3_yoy}} against the third quarter of 2025. The average order value was {{q3_aov}}.

- **Growth.** {{top_region}} grew fastest, at {{top_region_yoy}} year over year, to {{top_region_sales}}.
  {{low_region}} was the slowest region at {{low_region_yoy}}.
- **Largest region.** {{largest_region}} had the highest net sales of any region at {{largest_region_sales}}, which is
  {{largest_region_share}} of the chain.
- **Returns.** Returned value was {{q3_returned}}, a return rate of {{q3_return_rate}} of net sales. {{hr_region}} had
  the highest regional return rate at {{hr_region_rate}}.
- **Top physical store.** {{top_store_name}} ({{top_store_id}}) led all physical stores with {{top_store_sales}} in
  net sales.
- **Plan.** {{n_missed}} of 7 categories finished below their Q3 net sales plan; {{n_beat}} finished above it.

> How to read this report: figures come from Lumen's loyalty-member order tables (stores, customers, orders,
> order_items, returns) as of September 30, 2026. Definitions are in section 7. Dollar amounts are US dollars.

# 2. Net sales by region

{{table:region_summary}}

{{chart:region_sales}}

{{top_region}} was the growth leader at {{top_region_yoy}}. {{second_region}} followed at {{second_region_yoy}}, and
{{third_region}} at {{third_region_yoy}}. E-commerce, which is reported as its own region, accounted for {{ecom_share}}
of net sales. {{low_region}} fell short of the chain at {{low_region_yoy}}; regional operations will review its
promotion calendar and Q4 labor plan before the holiday season.

# 3. Store results

The five physical stores with the highest Q3 net sales are shown below. E-commerce is excluded from the ranking because
it is not a store. The four flagship stores (S01, S07, S13 and S21) together produced {{flagship_share}} of physical
store net sales.

{{table:top_stores}}

The newest store, Denver Cherry Creek (S24), opened on August 15, 2025. It delivered {{s24_sales}} in net sales in Q3
2026, against {{s24_sales_py}} in Q3 2025, when it traded for only six weeks.

# 4. Category results against plan

Plan targets come from the merchandising plan workbook (category targets sheet). Variance is actual net sales against the
Q3 2026 target; a negative number means the category missed its plan.

{{table:category_summary}}

{{missed_summary}}

# 5. Returns

Returned value in Q3 2026 was {{q3_returned}} on {{q3_return_count}} return records, against {{q3_returned_py}} in Q3 2025.
Three observations matter for the Q4 plan:

- **Electronics.** Electronics returned value was {{elec_return_rate}} of Electronics net sales, the highest of any
  category. {{elec_spike_name}} ({{elec_spike_id}}) processed {{elec_spike_count}} Electronics returns in the quarter, the
  most of any physical store and {{elec_spike_multiple}} times the next highest. {{elec_spike_opened}} of them were
  returned opened, which is the condition that carries a restocking fee.
- **Enhanced monitoring stores.** The three stores that Asset Protection placed on enhanced return monitoring in August
  ({{lp_stores}}) processed {{lp_q3_count}} returns worth {{lp_q3_value}}, which is {{lp_q3_share}} of the chain's
  returned value.
- **Online.** Returns processed through Lumen.com were {{web_return_share}} of returned value.

{{table:condition_mix}}

# 6. Outlook and actions

1. Regional Operations Directors review store-level return rates monthly and report stores above 1.5 times the chain
   rate to Asset Protection.
2. Merchandising reviews the Electronics range at the Chicago Loop flagship ahead of the holiday gift guide and
   considers a shorter display-model rotation.
3. Planning rebases the Q4 category targets once the Fall Home Refresh results are in (see the Q3 merchandising
   review).
4. The West and Northeast regional teams share the practices behind their Q3 results with the other regions.

# 7. Methodology and definitions

- **Source and scope.** The sales database's loyalty-member order tables, as of September 30, 2026. Orders placed by
  customers who are not Lumen Rewards members are not in the extract, so absolute dollars understate chain totals.
- **Quarters.** Fiscal quarters follow the calendar year. Q3 2026 is July 1 to September 30, 2026; Q3 2025 is
  July 1 to September 30, 2025.
- **Net sales.** The sum of order net amounts (after discounts, before returns). Orders are assigned to the region of the
  store that took the order; orders placed on Lumen.com are assigned to E-commerce.
- **Returned value.** The value of returned items before any restocking fee, by return date, and assigned to the region
  of the store that processed the return. Online-order returns made in a store belong to that store.
- **Return rate.** Returned value divided by net sales for the same region and quarter. It is a flow ratio: returns in a
  quarter include items bought in the previous quarter.
- **Average order value.** Net sales divided by the number of orders.
