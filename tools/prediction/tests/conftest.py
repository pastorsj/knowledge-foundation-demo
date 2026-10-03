# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""A knowledge root built from the shared catalog fixture (contracts/fixtures/catalog, copied into tests/fixtures).

retail.sales gets its tables.duckdb from the fixture CSVs, typed as its manifest says, as the ingest service builds it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import duckdb
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "catalog"
SALES = "retail.sales"
ACCOUNTS = "workspace.tables"  # 1,200 accounts, past Kumo's 1,000 per request; no foreign keys


def fixture_source(source_id: str) -> dict[str, Any]:
    return json.loads((FIXTURES / "sources" / f"{source_id}.json").read_text())


def write_source(knowledge_dir: Path, manifest: dict[str, Any]) -> None:
    """Write a source manifest the way ingest does: to a temporary name, then renamed into place."""
    sources = knowledge_dir / "catalog" / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    staged = sources / f".{manifest['id']}.json.tmp"
    staged.write_text(json.dumps(manifest, indent=2))
    staged.replace(sources / f"{manifest['id']}.json")


def build_database(path: Path, tables: list[dict[str, Any]], csv_dir: Path) -> None:
    """Each table from <csv_dir>/<name>.csv, with the manifest's column types."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(path)) as db:
        for table in tables:
            columns = ", ".join(f"'{column['name']}': '{column['type']}'" for column in table["columns"])
            csv = csv_dir / f"{table['name']}.csv"
            db.execute(
                f"CREATE TABLE {table['name']} AS FROM read_csv('{csv}', header = true, columns = {{{columns}}})"
            )


def copy_of_sales(source_id: str, alias: str) -> dict[str, Any]:
    """retail.sales under another id and alias, with its own copy of the database file."""
    manifest = {**fixture_source(SALES), "id": source_id, "pack_id": source_id.split(".", maxsplit=1)[0]}
    manifest["database"] = {**manifest["database"], "path": f"sources/{source_id}/tables.duckdb", "alias": alias}
    return manifest


ACCOUNT_TABLES = [
    {
        "name": "accounts",
        "description": "One row per account.",
        "row_count": 1200,
        "primary_key": "account_id",
        "time_column": None,
        "columns": [
            {"name": "account_id", "type": "INTEGER", "description": "", "nullable": False},
            {"name": "segment", "type": "VARCHAR", "description": "", "nullable": False},
        ],
        "foreign_keys": [],
    }
]


@pytest.fixture
def knowledge_dir(tmp_path: Path) -> Path:
    """retail.sales and workspace.tables (structured, ready) and retail.policies (documents)."""
    root = tmp_path / "knowledge"
    (root / "catalog" / "sources").mkdir(parents=True)
    for path in (FIXTURES / "sources").glob("*.json"):
        shutil.copy(path, root / "catalog" / "sources" / path.name)
    sales = fixture_source(SALES)
    build_database(root / sales["database"]["path"], sales["database"]["tables"], FIXTURES / "tables")

    csv_dir = tmp_path / "uploads"
    csv_dir.mkdir()
    rows = "".join(f"{1200 - i},{'enterprise' if i % 3 == 0 else 'smb'}\n" for i in range(1200))
    (csv_dir / "accounts.csv").write_text("account_id,segment\n" + rows)
    build_database(root / "sources" / ACCOUNTS / "tables.duckdb", ACCOUNT_TABLES, csv_dir)
    accounts = {
        **fixture_source(SALES),
        "id": ACCOUNTS,
        "pack_id": "workspace",
        "files": [],
        "database": {
            "path": f"sources/{ACCOUNTS}/tables.duckdb",
            "alias": "workspace_tables",
            "tables": ACCOUNT_TABLES,
        },
    }
    accounts.pop("prediction")
    write_source(root, accounts)
    return root


def add_copy_of_sales(knowledge_dir: Path, source_id: str, alias: str) -> None:
    manifest = copy_of_sales(source_id, alias)
    (knowledge_dir / "sources" / source_id).mkdir(parents=True)
    shutil.copy(knowledge_dir / "sources" / SALES / "tables.duckdb", knowledge_dir / manifest["database"]["path"])
    write_source(knowledge_dir, manifest)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
