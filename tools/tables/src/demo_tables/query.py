# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run one query in a short-lived worker process, killed when it outlives its timeout.

`run` starts `python -I -m demo_tables.query` and writes `{databases, sql, max_rows, max_bytes}` to its stdin. The
worker opens a fresh in-memory DuckDB, attaches each selected source's file `READ_ONLY` under its alias, then turns
external access and extension autoloading off and locks the configuration, so the query can read only those files,
and only read them. It runs the query only when DuckDB, too, sees one SELECT statement.

Everything it writes is bounded. It fetches one row at a time, at most `max_rows + 1`, and stops adding rows before
its output passes `max_bytes`. Each cell is written JSON-safe within `MAX_CELL_CHARS` characters of JSON: dates and
times in ISO 8601, decimals as floats, integers past 2**53 and non-finite floats as strings, binary as a placeholder,
text cut, and lists and structs cut to `MAX_ITEMS` items and to the cell's budget. The parent reads at most
`max_bytes + OUTPUT_OVERHEAD_BYTES` and kills a worker that writes more.
"""

from __future__ import annotations

import asyncio
import datetime
import decimal
import json
import math
import re
import sys
import tempfile
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

MAX_ROWS = 200
MAX_COLUMNS = 100
MAX_CELL_CHARS = 2000  # one cell's JSON, however nested
MAX_ITEMS = 100  # items of one list or struct
MAX_OUTPUT_BYTES = 1_000_000  # the rows a worker writes; the result the agent reads is far smaller (server.fit)
OUTPUT_OVERHEAD_BYTES = 64 * 1024  # column names and flags past the rows' budget
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
    truncated: bool  # the query had more rows than these
    clipped: int  # how many values were cut to fit MAX_CELL_CHARS or MAX_ITEMS
    byte_limited: bool = False  # rows were left out to keep the worker's output under max_bytes


class QueryFailed(Exception):
    """The query did not run to the end. The message is safe to show the agent."""


class _TooLarge(Exception):
    pass


async def run(databases: list[Attachment], sql: str, *, timeout: float, max_bytes: int = MAX_OUTPUT_BYTES) -> Rows:
    payload = {
        "databases": [{"alias": database.alias, "path": str(database.path)} for database in databases],
        "sql": sql,
        "max_rows": MAX_ROWS,
        "max_bytes": max_bytes,
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
        output = await asyncio.wait_for(
            _exchange(process, json.dumps(payload).encode(), max_bytes + OUTPUT_OVERHEAD_BYTES), timeout
        )
    except TimeoutError as error:
        raise QueryFailed(
            f"The query took longer than {timeout:g} seconds and was stopped. Filter or aggregate it and try again."
        ) from error
    except _TooLarge as error:
        raise QueryFailed("The query's result is too large. Select fewer or shorter columns.") from error
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


async def _exchange(process: asyncio.subprocess.Process, payload: bytes, limit: int) -> bytes:
    """Send the payload, then read the worker's output up to `limit` bytes; past it, raise _TooLarge."""
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(payload)
    await process.stdin.drain()
    process.stdin.close()
    chunks: list[bytes] = []
    size = 0
    while chunk := await process.stdout.read(64 * 1024):
        size += len(chunk)
        if size > limit:
            raise _TooLarge
        chunks.append(chunk)
    await process.wait()
    return b"".join(chunks)


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
        max_bytes = min(int(payload.get("max_bytes", MAX_OUTPUT_BYTES)), MAX_OUTPUT_BYTES)
        rows: list[list[Any]] = []
        size, clipped, truncated, byte_limited = 0, 0, False, False
        # One row at a time, so only one row is ever held as Python objects.
        while (fetched := cursor.fetchone()) is not None:
            if len(rows) == max_rows:
                truncated = True
                break
            cells = [Cell() for _ in fetched]
            row = [json_value(value, cell) for value, cell in zip(fetched, cells, strict=True)]
            row_size = len(json.dumps(row))
            if size + row_size > max_bytes:
                truncated = byte_limited = True
                break
            rows.append(row)
            size += row_size
            clipped += sum(cell.cut for cell in cells)
        return {
            "columns": unique([str(column[0])[:128] for column in cursor.description]),
            "rows": rows,
            "truncated": truncated,
            "clipped": clipped,
            "byte_limited": byte_limited,
        }


@dataclass
class Cell:
    """What is left of one cell's JSON budget, and whether anything was cut to keep to it."""

    left: int = MAX_CELL_CHARS
    cut: bool = field(default=False)


def json_value(value: Any, cell: Cell) -> Any:
    """`value` as JSON can carry it without loss of meaning, within the cell's budget."""
    if isinstance(value, list | tuple):
        items = []
        for item in value:  # stops early: a list of millions is never walked whole
            if len(items) == MAX_ITEMS or cell.left <= 0:
                cell.cut = True
                break
            items.append(json_value(item, cell))
        return items
    if isinstance(value, dict):
        entries = {}
        for key, item in value.items():
            if len(entries) == MAX_ITEMS or cell.left <= 0:
                cell.cut = True
                break
            text = _clip(str(key), cell)
            entries[text] = json_value(item, cell)
        return entries
    scalar = _scalar(value)
    if isinstance(scalar, str):
        return _clip(scalar, cell)
    cell.left -= len(json.dumps(scalar))
    return scalar


def _scalar(value: Any) -> Any:
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
    return str(value)  # str, timedelta, UUID, ...


def _clip(text: str, cell: Cell) -> str:
    room = max(cell.left - 2, 1)  # the quotes count too
    if len(text) > room:
        cell.cut = True
        text = text[: room - 1] + "…"
    cell.left -= len(text) + 2
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


_DATABASE_FILE = re.compile(r"[^\s\"'`]*\.duckdb\b")
_ABSOLUTE_PATH = re.compile(r"(?<![\w.:])/(?:[^\s\"'`/]+/)*[^\s\"'`/]*")
# A relative path: one starting ./, ../ or ~/, or a quoted name/... (DuckDB quotes the files it names). An unquoted
# word/word stays, as in DuckDB's own "LIMIT/OFFSET" or "arg_min/arg_max", and so does a quoted date like '12/03/2024'.
_RELATIVE_PATH = re.compile(r"(?<![\w.:/-])(?:~|\.\.?)/[^\s\"'`]*|(?<=[\"'`])[A-Za-z_][\w.-]*/[^\s\"'`]*(?=[\"'`])")


def redact_error(message: str) -> str:
    """DuckDB's error class and its first two lines, without database files or absolute or relative paths."""
    lines = [line.strip() for line in message.strip().splitlines() if line.strip()][:2]
    text = " ".join(lines) or "DuckDB could not run the query."
    text = _DATABASE_FILE.sub("<database file>", text)
    text = _RELATIVE_PATH.sub("<path>", _ABSOLUTE_PATH.sub("<path>", text))
    return text if len(text) <= MAX_ERROR_CHARS else text[: MAX_ERROR_CHARS - 1] + "…"


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
        result = {"error": redact_error(str(error))}
    except MemoryError:
        result = {"error": "The query ran out of memory. Filter or aggregate it and try again."}
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
