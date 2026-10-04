---
name: querying-auto-ontology
description: Answers historical database questions through Auto Ontology
license: Apache-2.0
compatibility: Requires the auto_ontology MCP server (ask_question tool)
metadata:
  author: NVIDIA
  version: "2.0"
  hermes:
    tags:
      - auto-ontology
      - text-to-sql
      - structured-data
    related_skills:
      - querying-tables
      - predicting-with-kumo
---

# Querying Auto Ontology

NVIDIA Auto Ontology maps business terms to database columns. It turns a
plain-language question into SQL, runs it, and returns the rows. You describe
the result you need; Auto Ontology writes the query.

## When to Use

- The selected sources include the `structured_retrieval` capability, and
- the question needs exact rows, counts, totals, filters, groupings, or a custom
  calculation over historical data, phrased in business terms ("active members",
  "on-time deliveries") that do not map plainly to the catalog's columns.

When the catalog's columns make the SQL clear, `querying-tables` is faster and
exact: prefer it. Use `predicting-with-kumo` for future outcomes.

## Tool

`ask_question` (Hermes tool ID `mcp__auto_ontology__ask_question`)

| Argument | Value |
| --- | --- |
| `question` | One complete, self-contained question |

Auto Ontology answers over the one database it was set up for. Pass only
`question`: never `source_ids`, `target_db`, `prediction`, `conversation_id`,
or `evidence`.

## Procedure

1. Write one complete question that keeps every requested entity, measure,
   filter, grouping, date window, and sort order. A call can take tens of
   seconds, so one well-formed question beats several narrow ones.
2. Call `ask_question` with only `question`.
3. Read `rows`, `row_count`, `truncated`, and `sql`. Base claims on `rows`; treat
   `answer` as a summary. `resolution_lineage` shows which tables and columns
   each phrase of the question resolved to: check that they are the ones the
   question means, in the selected source.
4. If `truncated` is true, say the result is partial, or ask a narrower
   question.
5. If the result is empty, has the wrong grain, or answers a different
   question, retry once with a clearer question. Then report the gap.

## Pitfalls

- Do not write SQL yourself or paste SQL into the question. Describe the result.
- Auto Ontology's descriptions mention `search_terms` and `check_answerable`.
  Those tools are not enabled here, so go straight to `ask_question`.
- Define every measure in the question: what is counted or summed, over which
  rows, and how a ratio's numerator and denominator are formed. "Average order
  value" can mean per order or per customer; say which.
- Keep measures at their natural grain. Ask for totals before a one-to-many join
  can multiply them.
- State date windows as explicit dates, and give the end as the first day
  after the window: "on or after 2026-07-01 and before 2026-10-01". Timestamp
  columns hold times of day, so an inclusive end date ("to 2026-09-30") can
  drop the last day.
- Say whether returns, refunds or cancellations reduce a sales measure. Auto
  Ontology may subtract them from "net sales" unless told not to.
- Ask separate questions for different time grains, such as daily and monthly.
- A correlation in the rows is not a cause.

## Example

Question: "How many orders did gold-tier customers place, and what was their
average order value?"

```
ask_question(
    question="Count the orders placed by customers in the gold loyalty tier, and give the "
             "average net amount per order for those orders.",
)
```

Cite the rows with the result's `evidence_id`.
