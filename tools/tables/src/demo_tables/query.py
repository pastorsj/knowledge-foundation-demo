# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run one query in a short-lived worker process, killed when it outlives its timeout.

`run` starts `python -I -m demo_tables.query` and writes `{databases, sql, max_rows}` to its stdin. The worker
opens a fresh in-memory DuckDB, attaches each selected source's file `READ_ONLY` under its alias, then turns external
access and extension autoloading off and locks the configuration, so the query can read only those files, and only
read them. It runs the query only when DuckDB, too, sees one SELECT statement, fetches at most `max_rows + 1` rows,
and writes them JSON-safe to stdout: dates and times in ISO 8601, decimals as floats, integers past 2**53 and
non-finite floats as strings, binary as a placeholder, strings cut to `MAX_CELL_CHARS`.
"""

from __future__ import annotations

import asyncio
import datetime
import decimal
import json
import math
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_ROWS = 200
MAX_COLUMNS = 100
MAX_CELL_CHARS = 2000
MAX_ERROR_CHARS = 600
MEMORY_LIMIT = "1GB"
THREADS = 2
ADDRESS_SPACE_BYTES = 4 * 1024**3  # the worker's whole address space, DuckDB's buffers and Python included


@dataclass(frozen=True)
class Attachment:
    alias: str
    path: Path


@dataclass(frozen=True)
class Rows:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool  # the query had more than MAX_ROWS rows
    clipped: int  # how many values were cut to MAX_CELL_CHARS


class QueryFailed(Exception):
    """The query did not run to the end. The message is safe to show the agent."""


async def run(databases: list[Attachment], sql: str, *, timeout: float) -> Rows:
    payload = {
        "databases": [{"alias": database.alias, "path": str(database.path)} for database in databases],
        "sql": sql,
        "max_rows": MAX_ROWS,
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-m",
        __name__,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={"LANG": "C.UTF-8"},
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(json.dumps(payload).encode()), timeout)
    except TimeoutError as error:
        raise QueryFailed(
            f"The query took longer than {timeout:g} seconds and was stopped. Filter or aggregate it and try again."
        ) from error
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    if process.returncode or not output:
        raise QueryFailed("The query ran out of memory or stopped its worker. Filter or aggregate it and try again.")
    result = json.loads(output)
    if "error" in result:
        raise QueryFailed(result["error"])
    return Rows(**result)


# ---------------------------------------------------------------------------------------------------- the worker


class _Refused(Exception):
    pass


def execute(payload: dict[str, Any]) -> dict[str, Any]:
    import duckdb

    with (
        tempfile.TemporaryDirectory(prefix="demo-tables-") as temp_directory,
        duckdb.connect(
            ":memory:",
            config={
                "autoinstall_known_extensions": "false",
                "autoload_known_extensions": "false",
                "python_enable_replacements": "false",
                "threads": str(THREADS),
                "memory_limit": MEMORY_LIMIT,
                "temp_directory": temp_directory,
                "max_temp_directory_size": "0B",
            },
        ) as connection,
    ):
        for database in payload["databases"]:
            path = database["path"].replace("'", "''")
            connection.execute(f"ATTACH '{path}' AS \"{database['alias']}\" (READ_ONLY)")
        connection.execute("SET TimeZone = 'UTC'")
        connection.execute("SET enable_external_access = false")
        connection.execute("SET autoload_known_extensions = false")
        connection.execute("SET lock_configuration = true")

        statements = connection.extract_statements(payload["sql"])
        if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
            raise _Refused("Send exactly one SELECT statement; nothing else runs.")
        cursor = connection.execute(statements[0])
        if len(cursor.description) > MAX_COLUMNS:
            raise _Refused(f"The query returns {len(cursor.description)} columns; select at most {MAX_COLUMNS}.")
        max_rows = min(int(payload["max_rows"]), MAX_ROWS)
        fetched = cursor.fetchmany(max_rows + 1)
        clipped = [0]
        rows = [[json_value(value, clipped) for value in row] for row in fetched[:max_rows]]
        return {
            "columns": unique([str(column[0])[:128] for column in cursor.description]),
            "rows": rows,
            "truncated": len(fetched) > max_rows,
            "clipped": clipped[0],
        }


def json_value(value: Any, clipped: list[int]) -> Any:
    """`value` as JSON can carry it without loss of meaning; `clipped[0]` counts strings cut short."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) <= 2**53 - 1 else str(value)  # past this, JSON readers lose precision
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, decimal.Decimal):
        return float(value) if value.is_finite() else str(value)
    if isinstance(value, datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return f"[binary: {len(value)} bytes]"
    if isinstance(value, list | tuple):
        return [json_value(item, clipped) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item, clipped) for key, item in value.items()}
    text = value if isinstance(value, str) else str(value)  # str, timedelta, UUID, ...
    if len(text) > MAX_CELL_CHARS:
        clipped[0] += 1
        return text[: MAX_CELL_CHARS - 1] + "…"
    return text


def unique(names: list[str]) -> list[str]:
    """Column names as row keys: a repeated name gets a suffix (customer_id, customer_id_2)."""
    seen: set[str] = set()
    result = []
    for name in names:
        candidate, number = name, 1
        while candidate in seen:
            number += 1
            candidate = f"{name}_{number}"
        seen.add(candidate)
        result.append(candidate)
    return result


def main() -> None:
    import duckdb

    if sys.platform == "linux":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))
    try:
        result = execute(json.load(sys.stdin))
    except _Refused as error:
        result = {"error": str(error)}
    except duckdb.Error as error:  # the agent needs DuckDB's message (a missing column, a type) to fix its SQL
        message = str(error).strip()
        result = {"error": message[: MAX_ERROR_CHARS - 1] + "…" if len(message) > MAX_ERROR_CHARS else message}
    except MemoryError:
        result = {"error": "The query ran out of memory. Filter or aggregate it and try again."}
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
