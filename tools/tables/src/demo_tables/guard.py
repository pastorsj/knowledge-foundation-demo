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
import string
from collections.abc import Collection
from collections.abc import Mapping

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError
from sqlglot.errors import ParseError
from sqlglot.errors import TokenError
from sqlglot.optimizer.scope import traverse_scope

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

    known = {ascii_lower(alias): {ascii_lower(name) for name in names} for alias, names in tables.items()}
    # Names resolve per scope, as DuckDB binds them: a bare name is a CTE only where that CTE is visible (its WITH
    # encloses the reference, and it is defined before the CTE that uses it). DuckDB matches identifiers whatever
    # their ASCII case, and folds ASCII letters only, so names are compared ASCII-lowercased (never Unicode-folded:
    # a long s or a Kelvin sign must not match "s" or "k"), on a copy; the query that runs is the one the agent wrote.
    resolved = tree.copy()
    for identifier in resolved.find_all(exp.Identifier):
        identifier.set("this", ascii_lower(identifier.name))
    _check_recursive_anchors(resolved, tables)
    checked: set[int] = set()
    try:
        scopes = list(traverse_scope(resolved))
    except OptimizeError as error:
        raise QueryRejected(f"The query's names could not be resolved: {_first_line(error)}") from error
    for scope in scopes:
        visible = set(scope.cte_sources)
        for table in scope.tables:
            _check_table(table, known, visible, tables)
            checked.add(id(table))
    for table in resolved.find_all(exp.Table):
        if id(table) not in checked:  # a table the scopes did not place: refuse rather than guess
            raise QueryRejected(
                f"{table.sql(dialect='duckdb')} is used where a table cannot be checked. {_only(tables)}"
            )


def _check_table(
    table: exp.Table, known: dict[str, set[str]], visible_ctes: set[str], tables: Mapping[str, Collection[str]]
) -> None:
    target = table.this
    if not isinstance(target, exp.Identifier):  # a table function
        name = _function_name(target) if isinstance(target, exp.Func) else type(target).__name__.lower()
        if name not in GENERATORS or table.db or table.catalog:
            raise QueryRejected(f"{name}() is not supported as a table: {_only(tables)}")
        return
    if not table.db and not table.catalog:
        if target.name in visible_ctes:  # both ASCII-lowercased
            return
        raise QueryRejected(
            f"Name every table as <alias>.<table>; {target.name!r} is not one, nor a CTE defined before it in the "
            f"same or an enclosing WITH. {_only(tables)}"
        )
    alias = table.catalog or table.db
    schema_ok = not table.catalog or table.db == "main"
    if not schema_ok or alias not in known or table.name not in known[alias]:
        raise QueryRejected(f"{table.sql(dialect='duckdb')} is not a table of the selected sources. {_only(tables)}")


def _check_recursive_anchors(tree: exp.Expression, tables: Mapping[str, Collection[str]]) -> None:
    """Refuse a recursive CTE's own name in its anchor: DuckDB binds it there to the catalog, not to the CTE.

    sqlglot treats the name as the CTE across the whole union; only the recursive term (its right side) may use it.
    """
    for with_ in tree.find_all(exp.With):
        if not with_.recursive:
            continue
        for cte in with_.expressions:
            body = cte.this
            anchor = body.this if isinstance(body, exp.SetOperation) else body
            for table in anchor.find_all(exp.Table):
                if not table.db and not table.catalog and table.name == cte.alias:
                    raise QueryRejected(
                        f"The recursive CTE {cte.alias!r} may use its own name only after UNION, in its recursive "
                        f"part. {_only(tables)}"
                    )


def ascii_lower(text: str) -> str:
    """`text` with ASCII letters lowercased and every other character kept, as DuckDB folds identifiers."""
    return text.translate(_ASCII_LOWER)


_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _function_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return ascii_lower(str(node.name))
    if isinstance(node, exp.Func):
        return ascii_lower(node.sql_name())
    return ascii_lower(type(node).__name__)


def _only(tables: Mapping[str, Collection[str]]) -> str:
    listing = ", ".join(sorted(f"{alias}.{name}" for alias, names in tables.items() for name in names))
    return f"query only the selected tables: {listing}."


def _first_line(error: Exception) -> str:
    return str(error).strip().splitlines()[0][:300] if str(error).strip() else type(error).__name__
