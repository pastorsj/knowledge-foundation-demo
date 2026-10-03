---
name: predicting-with-kumo
description: Predicts future outcomes per entity with Kumo PQL
license: Apache-2.0
compatibility: Requires the prediction MCP server (predict tool) and an NVIDIA Kumo Relational endpoint (the kumo profile's local NIM or KUMO_RELATIONAL_URL)
metadata:
  author: NVIDIA
  version: "2.0"
  hermes:
    tags:
      - kumo
      - prediction
      - forecasting
      - pql
    related_skills:
      - querying-tables
      - searching-documents
---

# Predicting with Kumo

`predict` runs one predictive query, written in Kumo's Predictive Query
Language (PQL), on NVIDIA Kumo Relational, a relational foundation model. It
reads a selected structured source's tables as a graph (rows linked by the
catalog's keys) and predicts, for each entity, an outcome over a future window
from the history before the anchor time. Nothing is trained per question.

## When to Use

- The selected sources include the `structured_prediction` capability, and
- the question asks what is likely to happen next to each entity: which
  customers will stop ordering, which machines will fail in the next 30 days,
  which patients will be readmitted, which loans will go delinquent, how much
  each store or account will spend next month.

Questions about what already happened belong to `querying-tables`.

## Tool

`predict` (Hermes tool ID `mcp__prediction__predict`)

| Argument | Value |
| --- | --- |
| `question` | The prediction asked for, in one plain sentence |
| `pql` | A PQL query, or `template:<id>` to run one of the source's prediction templates |
| `anchor_time` | Optional. An ISO 8601 timestamp with a timezone to predict from. Leave it unset unless the question names that date: the template's anchor, or the data's latest time, is used |

The application sets `source_ids`; never pass it. The tool runs the query on
the one selected structured source whose tables include every table the PQL
names.

Result fields:

| Field | Meaning |
| --- | --- |
| `available` | `false` when the prediction could not run; `reason` says why |
| `source_id`, `template_id`, `pql` | The source, the template (if any) and the exact query that ran |
| `task_type` | The task Kumo ran, such as `binary_classification` or `regression` |
| `anchor_time` | The point in time the prediction starts from |
| `horizon` | The PQL window past the anchor, `{value, unit}` |
| `entity_table` | The table of the `FOR EACH` entities |
| `rows` | One row per entity, highest first: `entity_id`, then `probability`, `value` or `label` |
| `warnings` | Notes such as entities left out of the scoring |
| `evidence_id` | The citation ID the application adds to a successful result |

## Templates

A structured source's catalog entry may list `prediction_templates`, each with
an `id`, a `name`, a `description` and its `pql`. A template is a tested query
for an outcome that source supports. When a template's description matches the
question, call `predict` with `pql="template:<id>"`, for example
`pql="template:churn_90d"`. Write your own PQL only when no template fits.

## PQL

```
PREDICT <target> FOR EACH <entity_table>.<primary_key> [WHERE <entity filter>]
```

- `<entity_table>.<primary_key>`: the entities to score, from the catalog
  (`primary_key` of the table the question is about: customers, machines,
  patients, accounts).
- `<target>`: an aggregation of a related table over a window after the anchor,
  `<AGG>(<table>.<column> [WHERE <event filter>], <start>, <end>, <unit>)`, where `<AGG>` is `SUM`,
  `AVG`, `MIN`, `MAX` or `COUNT`. `COUNT(<table>.*, 0, 30, days)` counts rows.
  `0, 30, days` is the 30 days after the anchor. The table must have a
  `time_column` and link to the entity table through the catalog's foreign keys.
- Add a comparison to ask a yes/no question: `<AGG>(...) <op> <value>`, with
  `<op>` one of `=`, `!=`, `>`, `>=`, `<`, `<=`.
- Name tables as the catalog does, without the source's DuckDB alias:
  `orders.net_amount`, not `retail_sales.orders.net_amount`.

Two kinds of `WHERE` do different things:

- **Inside the aggregate, it filters the events counted**:
  `<AGG>(<table>.* WHERE <table>.<column> = '<value>', <start>, <end>, <unit>)`.
  Use it when the outcome is one kind of event: only unplanned maintenance
  events, only returned orders, only declined transactions. The condition is on
  a column of the aggregated table.
- **After `FOR EACH`, it filters the entities scored**:
  `FOR EACH <entity_table>.<primary_key> WHERE <condition>`. Use it when the
  question is about some entities only: a column of the entity table
  (`accounts.account_type = 'checking'`), or past activity with a window that
  ends at the anchor (`COUNT(orders.*, -90, 0, days) > 0`: active in the 90
  days before).

An event condition written after `FOR EACH` changes which entities are scored,
not what counts as the outcome. "Which machines are likely to fail unplanned"
filters events:

```
PREDICT COUNT(maintenance_events.* WHERE maintenance_events.event_type = 'unplanned', 0, 30, days) > 0 FOR EACH machines.machine_id
```

"Which checking accounts are likely to close" filters entities:

```
PREDICT COUNT(monthly_balances.*, 0, 90, days) = 0 FOR EACH accounts.account_id WHERE accounts.account_type = 'checking'
```

More examples (the names are illustrative; take yours from the catalog):

```
PREDICT COUNT(orders.*, 0, 90, days) = 0 FOR EACH customers.customer_id
PREDICT COUNT(orders.*, 0, 30, days) = 0 FOR EACH customers.customer_id WHERE COUNT(orders.*, -90, 0, days) > 0
PREDICT SUM(orders.net_amount, 0, 30, days) FOR EACH stores.store_id
PREDICT COUNT(admissions.*, 0, 30, days) > 0 FOR EACH patients.patient_id
PREDICT MAX(loan_payments.days_past_due, 0, 90, days) >= 30 FOR EACH loans.loan_id
```

## Reading the result

- Binary (the target has a comparison): each row's `probability` is the
  likelihood, from 0 to 1, that the condition holds over the horizon. Write
  it as a percentage: 0.81 is 81%.
- Regression (an aggregation with no comparison): each row's `value` is the
  predicted amount over the horizon, in the column's unit (a sum of USD order
  values is USD).
- Multiclass: each row's `label` is the predicted class and `probability` its
  score.
- The tool scores a bounded set of entities and returns the highest-ranked
  rows. When `warnings` say entities were left out, say the ranking covers the
  entities scored, or narrow them with `WHERE`.

## Procedure

1. Name the entity and the outcome: which table's rows to score, which related
   table and column measure the outcome, and over what window.
2. Use a matching template (`template:<id>`). Otherwise write the PQL from the
   catalog's tables, columns and keys.
3. Call `predict` once.
4. If `available` is `false`, do not retry and do not substitute anything.
   Say plainly that prediction is unavailable in this setup and give the
   `reason` (for example, no Kumo endpoint is configured). Answer the other
   work items, and note it under **Limitations**.
5. If the tool returns an error about the query, fix the one thing it names (a
   table or column missing from the catalog, a target table with no time
   column or no link to the entity table, an alias prefix) and retry once.
6. Report the entities in the order of the rows, with their probability or
   value, the result's `anchor_time` and `horizon`, and the `evidence_id`.

## Pitfalls

- Predictions are model estimates, never certainties: write "most likely",
  "an estimated 81% likelihood", never "will".
- Never replace an unavailable or failed prediction with historical data or
  your own estimate. History can be a separate work item, labeled as history.
- Use the horizon exactly as returned, with its unit. Calendar days are not
  business days. If the question asked for another window, say which window
  the prediction covers.
- Keep predicted values apart from observed ones. For "predicted versus
  actual", get the history with `querying-tables` and join the two results on
  the entity ID.
- Do not show the PQL in the answer unless the user asks for it.

## Example

Question: "Which customers are most likely to stop ordering in the next 90
days?" The selected source lists a `churn_90d` template:

```
predict(question="Customers most likely to place no order in the next 90 days", pql="template:churn_90d")
```

Without a template:

```
predict(question="Customers most likely to place no order in the next 90 days",
        pql="PREDICT COUNT(orders.*, 0, 90, days) = 0 FOR EACH customers.customer_id")
```

Answer with the ranked customers, each probability as a percentage, the anchor
and the 90-day horizon, and the token in every row:

| Customer | Likelihood of no order in 90 days | Evidence |
| --- | --- | --- |
| C2 | 81% | [evidence:<evidence_id of the call>] |
| C1 | 34% | [evidence:<evidence_id of the call>] |

When the result says `available: false`, the answer says so instead:
"Prediction is unavailable in this setup: No Kumo endpoint is configured
(KUMO_RELATIONAL_URL)."
