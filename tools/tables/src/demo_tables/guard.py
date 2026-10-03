# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The SQL guard: exactly one SELECT over the attached sources' own tables, checked before anything runs.

A query may use CTEs, joins, subqueries, set operations (UNION, INTERSECT, EXCEPT), window functions and the
generators `range`, `generate_series` and `unnest`. Every other table is `<alias>.<table>` (or
`<alias>.main.<table>`) of a selected source, and an unqualified name must be one of the query's own CTEs. Anything
that reads past those tables is refused: file and URL scans (`read_*`, `glob`, `parquet_scan`, a quoted path as a
table), `query`/`query_table`, DuckDB's system and pragma functions, other databases, `INTO`, and every statement
that is not a SELECT (ATTACH, COPY, PRAGMA, SET, INSTALL, LOAD, EXPORT, ...).

This is the first of two locks. The worker runs the query in a fresh in-memory DuckDB with external access off and
its configuration locked (query.py), so a query the guard misreads still cannot reach a file.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from collections.abc import Mapping

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.errors import TokenError

# Table functions that read nothing but their arguments.
GENERATORS = frozenset({"range", "generate_series", "unnest"})
# Functions that read files, URLs, other databases, the environment or DuckDB's own state, wherever they appear.
FORBIDDEN_FUNCTION = re.compile(
    r"^(read_|parquet_|iceberg_|delta_|sqlite_|postgres_|mysql_|sniff_|duckdb_|pragma_|which_secret)"
    r"|^(glob|query|query_table|getenv|current_setting|load_extension)$"
)
# Statement and clause nodes a single SELECT never needs.
FORBIDDEN_NODES = (
    exp.Command,
    exp.Into,
    exp.Lock,
    exp.DML,
    exp.DDL,
    exp.Pragma,
    exp.Set,
    exp.Attach,
    exp.Detach,
    exp.Copy,
    exp.Install,
    exp.Use,
    exp.Describe,
)
SOURCES = (exp.Table, exp.Subquery, exp.Unnest, exp.Values, exp.Lateral)


class QueryRejected(ValueError):
    """A query the tool refuses. The message is written here and safe to show the agent."""


def validate(sql: str, tables: Mapping[str, Collection[str]]) -> None:
    """Raise QueryRejected unless `sql` is one SELECT over `tables` ({alias: table names}) and generators."""
    if not sql.strip():
        raise QueryRejected("The query is empty.")
    try:
        statements = [statement for statement in sqlglot.parse(sql, read="duckdb") if statement is not None]
    except (ParseError, TokenError) as error:
        raise QueryRejected(f"The query could not be parsed as DuckDB SQL: {_first_line(error)}") from error
    if len(statements) != 1 or not isinstance(statements[0], exp.Select | exp.SetOperation):
        raise QueryRejected(
            "Send exactly one SELECT statement (WITH, JOIN, UNION and subqueries are fine); nothing else runs."
        )
    tree = statements[0]

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise QueryRejected(f"{type(node).__name__.upper()} is not supported: only a read-only SELECT runs.")
        if isinstance(node, exp.Func) and FORBIDDEN_FUNCTION.match(_function_name(node)):
            raise QueryRejected(f"{_function_name(node)}() is not supported: {_only(tables)}")
        if isinstance(node, exp.From | exp.Join) and not isinstance(node.this, SOURCES):
            raise QueryRejected(f"This FROM clause is not supported: {_only(tables)}")

    known = {alias.casefold(): {name.casefold() for name in names} for alias, names in tables.items()}
    ctes = {cte.alias_or_name.casefold() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        _check_table(table, known, ctes, tables)


def _check_table(
    table: exp.Table, known: dict[str, set[str]], ctes: set[str], tables: Mapping[str, Collection[str]]
) -> None:
    target = table.this
    if not isinstance(target, exp.Identifier):  # a table function
        name = _function_name(target) if isinstance(target, exp.Func) else type(target).__name__.lower()
        if name not in GENERATORS or table.db or table.catalog:
            raise QueryRejected(f"{name}() is not supported as a table: {_only(tables)}")
        return
    if not table.db and not table.catalog:
        if target.name.casefold() in ctes:
            return
        raise QueryRejected(f"Name every table as <alias>.<table>; {target.name!r} is not one. {_only(tables)}")
    alias = (table.catalog or table.db).casefold()
    schema_ok = not table.catalog or table.db.casefold() == "main"
    if not schema_ok or alias not in known or table.name.casefold() not in known[alias]:
        raise QueryRejected(f"{table.sql(dialect='duckdb')} is not a table of the selected sources. {_only(tables)}")


def _function_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).casefold()
    if isinstance(node, exp.Func):
        return node.sql_name().casefold()
    return type(node).__name__.casefold()


def _only(tables: Mapping[str, Collection[str]]) -> str:
    listing = ", ".join(sorted(f"{alias}.{name}" for alias, names in tables.items() for name in names))
    return f"query only the selected tables: {listing}."


def _first_line(error: Exception) -> str:
    return str(error).strip().splitlines()[0][:300] if str(error).strip() else type(error).__name__
