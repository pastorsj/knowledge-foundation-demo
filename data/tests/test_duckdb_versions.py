# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The DuckDB that writes the database must not be newer than any DuckDB that reads it.

An older DuckDB cannot always open a file a newer one wrote, so upgrade the readers (api, market-analytics, Auto
Ontology) first. This compares the locked versions of every uv project in the repository that uses DuckDB.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from demo_data.pack import DATA_ROOT

REPOSITORY = DATA_ROOT.parent


def locked_duckdb(lock: Path) -> tuple[int, ...] | None:
    packages = tomllib.loads(lock.read_text(encoding="utf-8")).get("package", [])
    versions = [package["version"] for package in packages if package["name"] == "duckdb"]
    return tuple(int(part) for part in versions[0].split(".")) if versions else None


def test_the_writer_is_not_newer_than_any_reader():
    writer = locked_duckdb(DATA_ROOT / "uv.lock")
    readers = {
        lock.parent.relative_to(REPOSITORY).as_posix(): version
        for pattern in ("*/uv.lock", "tools/*/uv.lock", "vendor/*/uv.lock")
        for lock in REPOSITORY.glob(pattern)
        if lock.parent != DATA_ROOT and (version := locked_duckdb(lock))
    }
    # Fails, rather than passing on nothing, if a move ever takes a reader out of the glob patterns.
    assert {"api", "tools/market-analytics"} <= readers.keys(), readers
    assert {name: version for name, version in readers.items() if version < writer} == {}
