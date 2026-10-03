# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The catalog's data sources, and the read-only data viewer of each structured source.

- ``GET /v1/data_sources?pack=``: the sources of one pack (all packs when omitted) that this stack can serve.
- ``schema``: tables, views, columns and keys, read from the source's DuckDB file; the keys ingest profiled
  into the source manifest stand in for constraints the file does not declare.
- ``preview``: the first 100 rows of one table or view.
- ``query``: one bounded SELECT, run in a separate process (``database/worker.py``) with the database attached
  under its alias, so the SQL the agent wrote (``retail_sales.orders``) runs as it did.
- ``ontology``: the Auto Ontology graph of the database (ontology profile only).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated
from typing import Any

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from pydantic import BaseModel
from pydantic import Field

from demo_api.auto_ontology.client import AutoOntologyClient
from demo_api.auto_ontology.client import AutoOntologyError
from demo_api.auto_ontology.models import OntologySnapshot
from demo_api.catalog import PACK_ID
from demo_api.catalog import PackNotFoundError
from demo_api.catalog import Source
from demo_api.database.query import QueryError
from demo_api.database.query import run_query
from demo_api.database.schema import governed_columns
from demo_api.database.schema import read_schema
from demo_api.services import Services
from demo_api.services import ServicesDep
from demo_api.services import read_catalog

router = APIRouter(prefix="/v1", tags=["data sources"])


class QueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=20_000)


@router.get("/data_sources")
async def data_sources(
    services: ServicesDep,
    pack: Annotated[str | None, Query(pattern=PACK_ID, max_length=64)] = None,
) -> list[dict[str, Any]]:
    """The sources a question can use: those of ``pack`` (every pack when omitted) the running tools can serve."""
    catalog = await read_catalog(services)
    try:
        return [source.public() for source in catalog.sources(pack)]
    except PackNotFoundError as error:
        raise HTTPException(404, str(error)) from error


@router.get("/data_sources/{source_id}/schema")
async def schema(source_id: str, services: ServicesDep) -> dict[str, Any]:
    source, path = await _structured(services, source_id)
    tables = await asyncio.to_thread(read_schema, path, source.tables)
    return {"source_id": source.id, "database_name": source.database_name, **tables}


@router.get("/data_sources/{source_id}/preview")
async def preview(
    source_id: str, table: str, services: ServicesDep, limit: Annotated[int, Query(ge=1, le=100)] = 100
) -> dict[str, Any]:
    """The first ``limit`` rows of ``table`` (``name``, or ``schema.name`` outside ``main``)."""
    source, path = await _structured(services, source_id)
    tables = await asyncio.to_thread(read_schema, path, source.tables)
    match = next((item for item in tables["tables"] if item["name"] == table), None)
    if match is None:
        raise HTTPException(404, f"No table or view named {table}.")
    relation = f'"{match["schema"]}"."{table.rsplit(".", 1)[-1]}"'
    return {"table": table, **await _query(source, path, f"SELECT * FROM {relation}", tables, max_rows=limit)}


@router.post("/data_sources/{source_id}/query")
async def query(source_id: str, body: QueryRequest, services: ServicesDep) -> dict[str, Any]:
    source, path = await _structured(services, source_id)
    tables = await asyncio.to_thread(read_schema, path, source.tables)
    return await _query(source, path, body.sql, tables)


@router.get("/data_sources/{source_id}/ontology")
async def ontology(source_id: str, services: ServicesDep) -> OntologySnapshot:
    source, _ = await _structured(services, source_id)
    settings = services.settings
    if not settings.auto_ontology_url:
        raise HTTPException(404, "Auto Ontology is not running (ontology profile).")
    client = AutoOntologyClient(
        settings.auto_ontology_url,
        email=settings.auto_ontology_email,
        password=settings.auto_ontology_password.get_secret_value(),
        origin=settings.auto_ontology_origin,
        transport=services.transport,
    )
    try:
        async with client:
            return await client.ontology_snapshot(source_id=source.id, database_name=source.database_name)
    except AutoOntologyError as error:
        raise HTTPException(error.status_code, str(error)) from error


async def _structured(services: Services, source_id: str) -> tuple[Source, Path]:
    """An offered structured source and its DuckDB file, or 404."""
    catalog = await read_catalog(services)
    source, path = catalog.source(source_id), catalog.database_path(source_id)
    if source is None or source.database_name is None or path is None or not path.is_file():
        raise HTTPException(404, f"{source_id} is not an available structured data source.")
    return source, path


async def _query(
    source: Source, path: Path, sql: str, schema: dict[str, Any], *, max_rows: int = 100
) -> dict[str, Any]:
    try:
        return await run_query(path, source.database_name, sql, governed_columns(schema), max_rows=max_rows)
    except QueryError as error:
        raise HTTPException(error.status_code, error.message) from error
