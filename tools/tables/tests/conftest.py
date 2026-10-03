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
from mcp.client import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult

from demo_tables.server import create_server
from demo_tables.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures" / "catalog"
SALES = "retail.sales"
TARGETS = "workspace.tables"  # a second structured source, under the alias workspace_tables


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


TARGET_TABLES = [
    {
        "name": "targets",
        "description": "Quarterly spend target per customer.",
        "row_count": 2,
        "primary_key": "customer_id",
        "time_column": None,
        "columns": [
            {"name": "customer_id", "type": "VARCHAR", "description": "", "nullable": False},
            {"name": "target", "type": "DECIMAL(10,2)", "description": "USD", "nullable": False},
            {"name": "set_on", "type": "DATE", "description": "", "nullable": False},
        ],
        "foreign_keys": [],
    }
]


def targets_source(**overrides: Any) -> dict[str, Any]:
    manifest = {
        **fixture_source(SALES),
        "id": TARGETS,
        "pack_id": "workspace",
        "name": "Uploaded tables",
        "files": [],
        "database": {"path": f"sources/{TARGETS}/tables.duckdb", "alias": "workspace_tables", "tables": TARGET_TABLES},
        **overrides,
    }
    manifest.pop("prediction")
    return manifest


@pytest.fixture
def knowledge_dir(tmp_path: Path) -> Path:
    """retail.sales and workspace.tables (structured, ready), retail.policies (documents), retail.staging (structured,
    empty), retail.missing (structured, ready, but its tables.duckdb is not there)."""
    root = tmp_path / "knowledge"
    (root / "catalog" / "sources").mkdir(parents=True)
    for path in (FIXTURES / "sources").glob("*.json"):
        shutil.copy(path, root / "catalog" / "sources" / path.name)
    sales = fixture_source(SALES)
    build_database(root / sales["database"]["path"], sales["database"]["tables"], FIXTURES / "tables")

    csv_dir = tmp_path / "uploads"
    csv_dir.mkdir()
    (csv_dir / "targets.csv").write_text("customer_id,target,set_on\nC1,250.00,2026-07-01\nC3,500.50,2026-07-01\n")
    build_database(root / "sources" / TARGETS / "tables.duckdb", TARGET_TABLES, csv_dir)
    write_source(root, targets_source())

    staging = {**sales, "id": "retail.staging", "status": "empty"}
    staging["database"] = {**sales["database"], "path": "sources/retail.staging/tables.duckdb", "alias": "staging"}
    write_source(root, staging)
    missing = {**sales, "id": "retail.missing"}
    missing["database"] = {**sales["database"], "path": "sources/retail.missing/tables.duckdb", "alias": "missing"}
    write_source(root, missing)
    return root


@pytest.fixture
def settings(knowledge_dir: Path) -> Settings:
    return Settings(knowledge_dir=knowledge_dir)


@pytest.fixture
def server(settings: Settings) -> MCPServer:
    return create_server(settings)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def query(server: MCPServer, sql: str, source_ids: list[str] | None = None, **arguments: Any) -> CallToolResult:
    arguments = {
        "question": "What does the data say?",
        "sql": sql,
        "source_ids": [SALES] if source_ids is None else source_ids,
        **arguments,
    }
    async with Client(server) as client:
        return await client.call_tool("query_tables", arguments)
