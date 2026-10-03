# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The SQL guard: one SELECT over the attached aliases' own tables, nothing else."""

from __future__ import annotations

import pytest

from demo_tables.guard import QueryRejected
from demo_tables.guard import validate

TABLES = {"retail_sales": {"customers", "orders"}, "workspace_tables": {"targets"}}

# Each one tries to reach past the attached tables: files, other databases, configuration or extensions.
ESCAPES = {
    "attach": "ATTACH '/tmp/evil.duckdb' AS evil",
    "copy": "COPY (SELECT * FROM retail_sales.customers) TO '/tmp/customers.csv'",
    "read_csv": "SELECT * FROM read_csv('/etc/passwd')",
    "read_csv_in_a_subquery": "SELECT * FROM retail_sales.orders WHERE customer_id IN (FROM read_csv_auto('/etc/x'))",
    "read_csv_in_a_cte": "WITH x AS (SELECT * FROM read_csv('/etc/passwd')) SELECT * FROM x",
    "read_text_as_a_scalar": "SELECT read_text('/etc/passwd')",
    "read_parquet_lateral": "SELECT * FROM retail_sales.orders, LATERAL (SELECT * FROM read_parquet('/k/x.parquet'))",
    "pragma": "PRAGMA database_list",
    "set": "SET enable_external_access = true",
    "reset": "RESET lock_configuration",
    "two_statements": "SELECT 1; SELECT 2",
    "select_then_attach": "SELECT * FROM retail_sales.orders; ATTACH '/tmp/evil.duckdb' AS evil",
    "install": "INSTALL httpfs",
    "load": "LOAD httpfs",
    "export": "EXPORT DATABASE '/tmp/dump'",
    "glob": "SELECT * FROM glob('/knowledge/**')",
    "query_table": "SELECT * FROM query_table('retail_sales.orders')",
    "query": "SELECT * FROM query('SELECT 1')",
    "parquet_scan": "SELECT * FROM parquet_scan('/knowledge/x.parquet')",
    "file_path": "SELECT * FROM '/etc/passwd'",
    "quoted_file_path": 'SELECT * FROM "/knowledge/sources/retail.sales/tables.duckdb"',
    "url": "SELECT * FROM 'https://example.com/data.csv'",
    "another_database": "SELECT * FROM memory.main.secrets",
    "an_unattached_alias": "SELECT * FROM retail_finance.ledger",
    "an_unknown_table": "SELECT * FROM retail_sales.secrets",
    "a_table_function_under_an_alias": "SELECT * FROM retail_sales.orders, retail_sales.read_csv('/etc/passwd')",
    "a_system_table_function": "SELECT * FROM duckdb_settings()",
    "a_pragma_table_function": "SELECT * FROM pragma_database_list()",
    "a_qualified_system_function": "SELECT * FROM system.main.duckdb_tables()",
    "an_unqualified_table": "SELECT * FROM orders",
    "select_into": "SELECT * INTO stolen FROM retail_sales.orders",
    "create": "CREATE TABLE copy AS SELECT * FROM retail_sales.orders",
    "insert": "INSERT INTO retail_sales.orders SELECT * FROM retail_sales.orders",
    "delete": "DELETE FROM retail_sales.orders",
    "a_delete_in_a_cte": "WITH gone AS (DELETE FROM retail_sales.orders RETURNING *) SELECT count(*) FROM gone",
    "describe": "DESCRIBE retail_sales.orders",
    "detach": "DETACH retail_sales",
    "use": "USE retail_sales",
    "call": "CALL pragma_database_list()",
    "getenv": "SELECT getenv('HOME')",
    "current_setting": "SELECT current_setting('enable_external_access')",
    "unparsable": "SELEC * FRM retail_sales.orders",
    "empty": "   ",
    # A bare name is a CTE only where that CTE is visible: not outside its subquery, not before its definition.
    "a_cte_name_used_outside_its_subquery": "SELECT * FROM (WITH duckdb_tables AS (SELECT 1 AS x) "
    "SELECT * FROM duckdb_tables) a, duckdb_tables b",
    "a_cte_name_used_before_its_definition": 'WITH a AS (SELECT * FROM "/tmp/x/secret.csv"), '
    '"/tmp/x/secret.csv" AS (SELECT 1) SELECT * FROM a',
    "a_cte_name_used_in_a_sibling_cte_body_before_it": "WITH a AS (SELECT * FROM b), b AS (SELECT 1 AS x) "
    "SELECT * FROM a",
    "a_derived_table_alias_used_as_a_table": "SELECT * FROM (SELECT 1 AS x) AS duckdb_tables, duckdb_tables",
    "a_cte_inside_a_cte_used_outside_it": "WITH a AS (WITH secret AS (SELECT 1) SELECT * FROM secret) "
    "SELECT * FROM a, secret",
    # DuckDB binds a recursive CTE's anchor (the union's left side) to the catalog, not to the CTE itself.
    "a_recursive_cte_named_in_its_own_anchor": "WITH RECURSIVE duckdb_databases AS (SELECT path::VARCHAR AS p "
    "FROM duckdb_databases UNION SELECT p FROM duckdb_databases WHERE false) SELECT * FROM duckdb_databases",
    "a_recursive_cte_named_in_a_subquery_of_its_anchor": "WITH RECURSIVE t AS (SELECT * FROM (SELECT * FROM t) "
    "UNION ALL SELECT * FROM t) SELECT * FROM t",
    # Its own name only at the top level of a UNION's right side: not in the body's own WITH, not under INTERSECT
    # or EXCEPT (DuckDB recurses only over UNION), not in a subquery of the recursive term.
    "a_recursive_cte_named_in_its_bodys_own_with": "WITH RECURSIVE duckdb_databases AS (WITH x AS (SELECT "
    "path::VARCHAR AS p FROM duckdb_databases) SELECT p FROM x UNION SELECT p FROM duckdb_databases WHERE false) "
    "SELECT * FROM duckdb_databases",
    "a_recursive_cte_over_intersect": "WITH RECURSIVE duckdb_databases AS (SELECT 'x' AS p INTERSECT "
    "SELECT path FROM duckdb_databases) SELECT * FROM duckdb_databases",
    "a_recursive_cte_over_except": "WITH RECURSIVE duckdb_databases AS (SELECT 'x' AS p EXCEPT "
    "SELECT CAST(path AS VARCHAR) FROM duckdb_databases) SELECT * FROM duckdb_databases",
    "a_recursive_cte_without_a_union": "WITH RECURSIVE duckdb_databases AS (SELECT path FROM duckdb_databases) "
    "SELECT * FROM duckdb_databases",
    "a_recursive_cte_named_in_a_subquery_of_its_recursive_term": "WITH RECURSIVE t AS (SELECT 1 AS n UNION ALL "
    "SELECT n + 1 FROM (SELECT * FROM t) s WHERE n < 5) SELECT * FROM t",
    # DuckDB folds ASCII letters only; Unicode case folding would make these CTE names match the system views.
    "a_cte_name_with_a_long_s": 'WITH "duckdb_databaſes" AS (SELECT 1 AS x) SELECT * FROM duckdb_databases',
    "a_cte_name_with_a_kelvin_sign": 'WITH "ducKdb_tables" AS (SELECT 1 AS x) SELECT * FROM duckdb_tables',
    "a_cte_name_with_a_dotless_i": 'WITH "pragma_database_lıst" AS (SELECT 1 AS x) SELECT * FROM pragma_database_list',
}

ALLOWED = {
    "a_table": "SELECT * FROM retail_sales.orders",
    "a_join_with_a_schema": "SELECT o.order_id, c.tier FROM retail_sales.orders o "
    "JOIN retail_sales.main.customers c USING (customer_id)",
    "a_cte": "WITH big AS (SELECT * FROM retail_sales.orders WHERE net_amount > 100) SELECT count(*) FROM big",
    "a_union_across_sources": "SELECT customer_id FROM retail_sales.customers "
    "UNION SELECT customer_id FROM workspace_tables.targets",
    "from_first": "FROM retail_sales.orders SELECT order_id",
    "a_window": "SELECT tier, sum(net_amount) OVER (PARTITION BY tier) FROM retail_sales.orders "
    "JOIN retail_sales.customers USING (customer_id)",
    "any_case": 'SELECT * FROM RETAIL_SALES."Orders"',
    "range": "SELECT count(*) FROM range(10000000000)",
    "generate_series": "SELECT * FROM generate_series(1, 3) AS t(x)",
    "unnest": "SELECT * FROM unnest([1, 2, 3])",
    "a_subquery": "SELECT * FROM (SELECT customer_id, count(*) AS n FROM retail_sales.orders GROUP BY 1) WHERE n > 1",
    "a_trailing_semicolon": "SELECT 1;",
    "a_cte_used_by_a_later_cte": "WITH a AS (SELECT * FROM retail_sales.orders), b AS (SELECT * FROM a) "
    "SELECT * FROM b",
    "a_cte_used_in_a_subquery": "WITH a AS (SELECT * FROM retail_sales.orders) "
    "SELECT * FROM (SELECT * FROM a) WHERE customer_id IN (SELECT customer_id FROM a)",
    "a_cte_in_any_case": 'WITH Big AS (SELECT * FROM retail_sales.orders) SELECT * FROM "BIG"',
    "a_recursive_cte": "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM t WHERE n < 5) SELECT * FROM t",
    "a_recursive_cte_joining_a_table": "WITH RECURSIVE t(n) AS (SELECT 1 UNION SELECT t.n + 1 FROM t "
    "JOIN retail_sales.orders o ON o.net_amount > t.n WHERE t.n < 3) SELECT * FROM t",
    "a_recursive_with_whose_ctes_do_not_recurse": "WITH RECURSIVE a AS (SELECT * FROM retail_sales.orders), "
    "b AS (SELECT * FROM a INTERSECT SELECT * FROM a) SELECT * FROM b",
    "a_cte_inside_a_subquery": "SELECT * FROM (WITH a AS (SELECT * FROM retail_sales.orders) SELECT * FROM a) s",
}


@pytest.mark.parametrize("sql", ESCAPES.values(), ids=ESCAPES.keys())
def test_escapes_are_refused(sql: str):
    with pytest.raises(QueryRejected):
        validate(sql, TABLES)


@pytest.mark.parametrize("sql", ALLOWED.values(), ids=ALLOWED.keys())
def test_selects_over_the_attached_tables_pass(sql: str):
    validate(sql, TABLES)


def test_messages_tell_the_agent_what_to_write_instead():
    with pytest.raises(QueryRejected, match=r"retail_sales\.orders"):
        validate("SELECT * FROM orders", TABLES)
    with pytest.raises(QueryRejected, match="retail_sales.customers, retail_sales.orders"):
        validate("SELECT * FROM retail_sales.secrets", TABLES)
    with pytest.raises(QueryRejected, match="exactly one SELECT"):
        validate("SELECT 1; SELECT 2", TABLES)
    with pytest.raises(QueryRejected, match="read_csv"):
        validate("SELECT * FROM read_csv('/etc/passwd')", TABLES)
