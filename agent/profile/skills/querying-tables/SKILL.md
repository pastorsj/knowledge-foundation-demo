---
name: querying-tables
description: Counts, totals and rankings from tables via DuckDB SQL
license: Apache-2.0
compatibility: Requires the tables MCP server (query_tables tool)
metadata:
  author: NVIDIA
  version: "1.0"
  hermes:
    tags:
      - sql
      - duckdb
      - tables
      - structured-data
    related_skills:
      - searching-documents
      - predicting-with-kumo
      - querying-auto-ontology
---

# Querying tables

`query_tables` runs one read-only SQL query in DuckDB over the structured
sources selected for this turn and returns the rows. You write the SQL from the
source catalog in the run instructions: there is no schema tool, and the
catalog already lists every table, column and key you may use.

## When to Use

- The selected sources include the `structured_retrieval` capability, and
- the answer is a number or a set of rows computed from tabular data: counts,
  totals, averages, rankings, trends over time, comparisons between groups, or
  the records that match a filter (orders, work orders, encounters, accounts).

Use `searching-documents` for what a policy, report or manual says, and
`predicting-with-kumo` for what is likely to happen next.

## Read the catalog first

Each structured source in the selected-source catalog has a `database` entry.
Everything the SQL names comes from it:

| Catalog field | Use it for |
| --- | --- |
| `database.alias` | The source's schema in DuckDB. Address every table as `<alias>.<table>` |
| `tables[].name`, `description`, `row_count` | Which table holds the facts you need, and how many rows it has |
| `columns[].name`, `type`, `description` | Exact column names, their types, and their units (in the description) |
| `primary_key`, `foreign_keys` | How tables join: join only on these keys |
| `time_column` | The column that dates each row: filter time windows on it |

Use only names the catalog lists, spelled exactly as listed. Never guess a
table or column from the question's wording. If the catalog has no column for
something the question needs, say so instead of approximating it.

## Tool

`query_tables` (Hermes tool ID `mcp__tables__query_tables`)

| Argument | Value |
| --- | --- |
| `question` | The work item in one plain sentence, such as "Net revenue by loyalty tier in Q3 2026". It labels the result and its receipt |
| `sql` | Exactly one DuckDB `SELECT` statement. `WITH` (CTEs), joins, window functions and `UNION ALL` are fine |

The application sets `source_ids` to the selected structured sources and
attaches each one read-only under its alias. Pass only `question` and `sql`.

Result fields:

| Field | Meaning |
| --- | --- |
| `columns`, `rows` | The result set, one object per row, at most 200 rows |
| `row_count`, `truncated` | Rows returned; `truncated` is true when the query matched more rows than the result holds |
| `database_name`, `databases` | The sources the query could read, each with its `alias` |
| `sql` | The query that ran |
| `warnings` | Notes such as rows dropped to fit the result |
| `evidence_id` | The citation ID the application adds to a successful result |

## Procedure

1. Find the table, columns and keys for each work item in the catalog, and
   note the source's `alias`.
2. Write one `SELECT` that computes the answer. Qualify every table with its
   source alias, `<alias>.<table>`, and give joined tables short names
   (`AS o`, `AS c`).
3. Aggregate in SQL. Use `GROUP BY` with `count`, `sum`, `avg`, `min`, `max`,
   window functions and `ORDER BY ... LIMIT` so the result holds the handful
   of rows the answer needs. Never fetch raw rows to count, sum or rank them
   yourself.
4. Filter time windows on the table's `time_column` with half-open ranges:
   `ordered_at >= TIMESTAMP '2026-07-01' AND ordered_at < TIMESTAMP '2026-10-01'`.
   When the question gives no dates ("last month", "this year"), anchor the
   window on the data's latest date (`max(<time_column>)`, in a CTE or a
   subquery) rather than today's date, and state the window you used.
5. Join only on the catalog's keys. Aggregate a many-side table before you
   join it to another many-side table, or the join multiplies the totals.
6. For a ranking that asks for both ends, return both in one query, for
   example with `rank() OVER (ORDER BY metric DESC)` and
   `rank() OVER (ORDER BY metric)` in a CTE, filtered to the top and bottom N.
   Use the number of rows the question asks for, and 3 when it gives none.
7. Call `query_tables` with `question` and `sql`: one `SELECT` per call. Run
   independent work items as parallel calls.
8. Read `rows`, `row_count`, `truncated` and `warnings`. Base every figure on
   `rows`. If `truncated` is true, the result is partial: aggregate further or
   say the answer covers only the rows returned.
9. If the tool returns an error, read it, fix the one thing it names (a name
   missing from the catalog, a table without its alias, a second statement, a
   type mismatch), and retry once. Then report what could not be computed.

## DuckDB notes

- Text literals take single quotes. Identifiers with spaces, capitals or
  reserved words take double quotes: `"Net Amount"`.
- Dates: `DATE '2026-07-01'`, `TIMESTAMP '2026-07-01 00:00:00'`,
  `date_trunc('month', ordered_at)`, `strftime(ordered_at, '%Y-%m')`,
  `ordered_at - INTERVAL 90 DAY`, `date_diff('day', start_at, end_at)`.
- Case-insensitive matching: `tier ILIKE 'gold'`, `name ILIKE '%valve%'`. Check
  a category's spelling with a small `SELECT DISTINCT` when a filter returns
  nothing.
- Ratios: `sum(a) / nullif(sum(b), 0)` avoids division by zero. In DuckDB `/`
  keeps the fraction even between integers (`//` is integer division). Round
  for display with `round(x, 2)`.
- Top N per group: `QUALIFY row_number() OVER (PARTITION BY g ORDER BY x DESC) <= 3`.
- If a table seems to lack a column you need, `SELECT * FROM <alias>.<table> LIMIT 5`
  once shows every column.

## Pitfalls

- Only `SELECT` runs. The tool refuses `ATTACH`, `COPY`, `PRAGMA`, `SET`,
  `INSTALL`, `LOAD`, file readers such as `read_csv`, file paths, and any
  table outside the attached aliases. There are no files to read.
- Keep each value in its column's unit, as the column description gives it.
  Write a rate or share stored as a fraction (0.12) as a percentage (12%).
- An empty result is a finding: no rows matched. Check the filter values once
  (spelling, case, date range) before you report it.
- With two structured sources selected, each has its own alias. Join across
  them only on an identifier both catalogs list.
- Do not show the SQL in the answer unless the user asks for it.

## Example

Question: "Which loyalty tier brought the most net revenue in the third
quarter of 2026?" The catalog lists the source alias `retail_sales`, with
`orders` (`ordered_at` as its time column, `net_amount` in USD) linked to
`customers` by `customer_id`:

```
query_tables(
    question="Net revenue and order count by loyalty tier, Q3 2026",
    sql="SELECT c.tier, count(*) AS orders, round(sum(o.net_amount), 2) AS net_revenue "
        "FROM retail_sales.orders AS o "
        "JOIN retail_sales.customers AS c USING (customer_id) "
        "WHERE o.ordered_at >= TIMESTAMP '2026-07-01' AND o.ordered_at < TIMESTAMP '2026-10-01' "
        "GROUP BY c.tier ORDER BY net_revenue DESC",
)
```

Question: "Which three machines had the most unplanned downtime in the latest
month?" The aliases and names below are illustrative; take yours from the
catalog:

```
query_tables(
    question="Unplanned downtime hours by machine in the latest month of data",
    sql="SELECT d.machine_id, m.line, round(sum(d.duration_minutes) / 60, 1) AS downtime_hours "
        "FROM plant_ops.downtime_events AS d "
        "JOIN plant_ops.machines AS m USING (machine_id) "
        "WHERE d.cause = 'unplanned' AND d.started_at >= "
        "(SELECT date_trunc('month', max(started_at)) FROM plant_ops.downtime_events) "
        "GROUP BY d.machine_id, m.line ORDER BY downtime_hours DESC LIMIT 3",
)
```

Answer with the rows, their units and the window. Every row of a table ends
with the token of the call that produced it:

| Tier | Orders | Net revenue (USD) | Evidence |
| --- | --- | --- | --- |
| gold | 3 | 395.00 | [evidence:<evidence_id of the call>] |
| silver | 1 | 45.25 | [evidence:<evidence_id of the call>] |

A table with no token in its rows is uncited, even when its figures are right.
