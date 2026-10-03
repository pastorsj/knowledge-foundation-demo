# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The entities Kumo scores: the entity table's primary keys, in order, that pass the query's entity filter.

Kumo predicts for every id it is given, whatever the filter says, so a filter that is a plain condition on the entity
table is applied here. The condition is never pasted into SQL as written: `condition_sql` parses it with sqlglot as
one boolean expression whose columns are the entity table's own, with no subquery, table, star or forbidden function,
and the SQL that runs is regenerated from that tree.

`select` runs it in a short-lived worker, `python -I -m demo_prediction.entities`: a fresh in-memory DuckDB with the
source's file attached `READ_ONLY`, external access off and the configuration locked, a 2 GiB address space, killed
after `TIMEOUT_SECONDS`. It returns at most `limit` ids, fetched with `fetchmany`, and the population's size.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Collection
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.errors import TokenError

TIMEOUT_SECONDS = 10.0
ADDRESS_SPACE_BYTES = 2 * 1024**3
MAX_OUTPUT_BYTES = 4 * 1024**2
# As the tables guard: functions that read files, URLs, other databases, the environment or DuckDB's state.
FORBIDDEN_FUNCTION = re.compile(
    r"^(read_|parquet_|iceberg_|delta_|sqlite_|postgres_|mysql_|sniff_|duckdb_|pragma_|which_secret)"
    r"|^(glob|query|query_table|getenv|current_setting|load_extension)$"
)
# Nodes a condition on one row never needs; generators and aggregates included.
FORBIDDEN_NODES = (
    exp.Query,
    exp.Subquery,
    exp.Table,
    exp.Star,
    exp.Command,
    exp.DML,
    exp.DDL,
    exp.Set,
    exp.Pragma,
    exp.Placeholder,
    exp.Parameter,
    exp.AggFunc,
    exp.Window,
    exp.GenerateSeries,
    exp.Explode,
    exp.Unnest,
)


class FilterRejected(ValueError):
    """An entity filter this tool cannot apply. The message says why and is safe to show the agent."""


class SelectionFailed(Exception):
    """The ids could not be read. The message is safe to show the agent."""


def condition_sql(condition: str, table: str, columns: Collection[str]) -> str:
    """The entity filter as DuckDB SQL regenerated from its parse, or FilterRejected."""
    try:
        parsed = sqlglot.parse_one(condition, read="duckdb", into=exp.Condition)
    except (ParseError, TokenError) as error:
        raise FilterRejected("it is not one SQL condition") from error
    if parsed is None or isinstance(parsed, exp.Column | exp.Literal):
        raise FilterRejected("it is not a condition")
    known = {column.casefold() for column in columns}
    for node in parsed.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise FilterRejected(f"it uses {type(node).__name__.upper()}, and a filter is one condition on one row")
        if isinstance(node, exp.Func):
            name = str(node.name) if isinstance(node, exp.Anonymous) else node.sql_name()
            if FORBIDDEN_FUNCTION.match(name.casefold()):
                raise FilterRejected(f"{name.casefold()}() is not supported in a filter")
        if isinstance(node, exp.Column):
            if node.db or node.catalog or (node.table and node.table.casefold() != table.casefold()):
                raise FilterRejected(f"{node.sql(dialect='duckdb')} is not a column of {table}")
            if node.name.casefold() not in known:
                raise FilterRejected(f"{node.name} is not a column of {table}")
    return parsed.sql(dialect="duckdb")


def select(
    path: Path, table: str, key: str, condition: str | None, limit: int, *, timeout: float = TIMEOUT_SECONDS
) -> tuple[list[Any], int]:
    """At most `limit` primary keys of `table`, in key order, passing `condition` (from condition_sql); and how many
    pass it in all."""
    payload = {"path": str(path), "table": table, "key": key, "condition": condition, "limit": limit}
    try:
        finished = subprocess.run(
            [sys.executable, "-I", "-m", __name__],
            input=json.dumps(payload).encode(),
            capture_output=True,
            timeout=timeout,
            env={"LANG": "C.UTF-8"},
            check=False,
        )
    except subprocess.TimeoutExpired as error:  # run() kills the worker
        raise SelectionFailed(f"selecting the entities took longer than {timeout:g} seconds") from error
    if finished.returncode or not finished.stdout or len(finished.stdout) > MAX_OUTPUT_BYTES:
        raise SelectionFailed("selecting the entities ran out of memory or stopped its worker")
    result = json.loads(finished.stdout)
    if "error" in result:
        raise SelectionFailed(result["error"])
    return result["ids"], result["population"]


# ---------------------------------------------------------------------------------------------------- the worker


def execute(payload: dict[str, Any]) -> dict[str, Any]:
    import duckdb

    def quoted(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    with (
        tempfile.TemporaryDirectory(prefix="demo-prediction-") as temp_directory,
        duckdb.connect(
            ":memory:",
            config={
                "autoinstall_known_extensions": "false",
                "autoload_known_extensions": "false",
                "python_enable_replacements": "false",
                "threads": "1",
                "memory_limit": "512MB",
                "temp_directory": temp_directory,
                "max_temp_directory_size": "0B",
            },
        ) as connection,
    ):
        location = payload["path"].replace("'", "''")
        connection.execute(f"ATTACH '{location}' AS source (READ_ONLY)")
        connection.execute("SET enable_external_access = false")
        connection.execute("SET autoload_known_extensions = false")
        connection.execute("SET lock_configuration = true")
        table, key = quoted(payload["table"]), quoted(payload["key"])
        where = f" WHERE ({payload['condition']})" if payload["condition"] else ""
        source = f"FROM source.main.{table} AS {table}{where}"
        (population,) = connection.execute(f"SELECT count(*) {source}").fetchone()
        cursor = connection.execute(f"SELECT {key} {source} ORDER BY 1 LIMIT {int(payload['limit'])}")
        ids = [row[0] for row in cursor.fetchmany(int(payload["limit"]))]
        return {"ids": ids, "population": population}


def main() -> None:
    import duckdb

    if sys.platform == "linux":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))
    try:
        result = execute(json.load(sys.stdin))
    except duckdb.Error as error:
        result = {"error": str(error).strip().splitlines()[0][:300]}
    except MemoryError:
        result = {"error": "selecting the entities ran out of memory"}
    sys.stdout.write(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
