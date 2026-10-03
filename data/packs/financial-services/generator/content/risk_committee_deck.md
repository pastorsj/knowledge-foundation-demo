<!-- SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
---
title: Credit Risk Committee, Q3 2026
subtitle: Harborview Community Bank | Data as of 30 September 2026 | Meeting of 14 October 2026
author: Credit Risk Management
---

## Portfolio at a glance

[[table:portfolio]]

- {{active_loans}} consumer loans were active at 30 September 2026, with {{balance_total}} outstanding.
- By count, {{share_personal}} of active loans are personal loans, {{share_auto}} are auto loans and {{share_mortgage}} are mortgages.
- {{originations_q3}} new loans were originated in the quarter ({{originations_q3_personal}} personal, {{originations_q3_auto}} auto, {{originations_q3_mortgage}} mortgage).
> Active means not paid off and not charged off at the reporting date. Balances are scheduled principal after the installments received.

## Delinquency: loans 30 or more days past due

[[table:delinquency30]]

- Across all products {{rate30_all_now}} of active loans were 30 or more days past due at 30 September, against {{rate30_all_q2}} at 30 June and {{rate30_all_py}} a year earlier.
- The highest rate is on {{top30_product_lower}} at {{top30_rate}}; the lowest is on {{low30_product_lower}} at {{low30_rate}}.
- {{delinq_loans_now}} loans are in the 30-day bucket or worse.

[[bars:delinquency30]]

> Delinquency rate = active loans with at least one installment unpaid for 30 or more days, divided by active loans at the reporting date. Charged-off loans are excluded. Source: loan_payments and loans tables; the monthly series is in credit_risk_report.xlsx.

## Default: loans 90 or more days past due

[[table:delinquency90]]

- {{loans90_now}} active loans were 90 or more days past due at 30 September, {{rate90_all_now}} of the portfolio.
- {{co_q3_count}} loans were charged off in the quarter with net charge-offs of {{co_q3_net}} after recoveries.

> Default is defined as 90 or more days past due. Personal and auto loans are charged off at 120 days, mortgages evaluated at 180 days.

## Allowance and provisions

[[table:provisions]]

- The allowance for loan losses is {{allowance_now}} at 30 September, {{coverage_now}} of outstanding balances.
- Provision expense for the quarter was {{provision_q3}}, against {{provision_q2}} in the second quarter.

## Early warning indicators

- Average credit card utilization on open cards: {{util_now}} in September 2026, against {{util_py}} in September 2025.
- Checking accounts with at least one overdraft in the month: {{od_share_now}} in September 2026, against {{od_share_py}} a year earlier.
- {{stressed_cards}} cardholders are above 70 percent utilization, and {{stressed_cards_borrowers}} of them also hold a Harborview loan.
- Customers who miss one installment are far more likely to miss the next: {{roll_rate}} of loans with an installment 30 or more days past due at 30 June were still unpaid or worse at 30 September.

## Where delinquency is concentrated

[[table:regions]]

- {{worst_region}} has the highest delinquency rate at {{worst_region_rate}}; {{best_region}} has the lowest at {{best_region_rate}}.
- {{share_low_band}} of the loans that are 30 or more days past due were underwritten in the Fair or Poor credit band, which hold {{share_low_band_book}} of the active book.

## Decisions and next steps

- Note that 30-day delinquency is {{rate30_all_now}}, {{delinq_direction}} {{rate30_all_py}} a year earlier, and keep the version 5.1 maximum debt-to-income limits, in force since 1 July 2026, under review.
- Instruct Loan Servicing to contact every borrower that is 30 to 59 days past due within 5 business days, as Table 4 of the Consumer Credit Policy requires.
- Ask Credit Risk to bring the first results of the monitoring of recent originations required by section 7 of the policy to the next meeting.
- Approve the allowance for the quarter as presented.
