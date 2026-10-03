# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The knowledge catalog: `<KNOWLEDGE_DIR>/catalog/sources/<source_id>.json`, read on every call.

The ingest service writes each source manifest atomically (contracts/catalog/source-manifest.schema.json). A call may
predict over a source when its manifest exists, its kind is `structured`, its status is `ready` or `ingesting` (what an
ingesting source already holds stays usable) and its DuckDB file is there. Only the manifests a call names are read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError

# The contract's patterns. They also keep a source id or a database path from naming another path, and an alias
# from being anything but an identifier in the ATTACH statement.
SOURCE_ID = re.compile(r"[a-z][a-z0-9_-]*\.[a-z][a-z0-9_]*")
ALIAS = re.compile(r"[a-z][a-z0-9_]*")
DATABASE_PATH = re.compile(r"sources/[^/]+/tables\.duckdb")
QUERYABLE = ("ready", "ingesting")
# DuckDB's own catalogs, which an attached database may not shadow.
RESERVED_ALIASES = frozenset({"memory", "system", "temp", "main"})


class CatalogError(ValueError):
    """A source a call may not predict over. The message is written here and safe to show the agent."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Column(_Model):
    name: str
    type: str
    description: str = ""
    nullable: bool = True


class ForeignKey(_Model):
    column: str
    references_table: str
    references_column: str


class TableInfo(_Model):
    name: str
    description: str = ""
    row_count: int = 0
    primary_key: str | None = None
    time_column: str | None = None
    columns: list[Column] = Field(default_factory=list)
    foreign_keys: list[ForeignKey] = Field(default_factory=list)


class Database(_Model):
    path: str
    alias: str
    tables: list[TableInfo] = Field(default_factory=list)


class Template(_Model):
    """A curated prediction of the source: `pql: "template:<id>"` runs it."""

    id: str
    name: str = ""
    description: str = ""
    pql: str
    anchor_time: str | None = None


class Prediction(_Model):
    templates: list[Template] = Field(default_factory=list)


class SourceManifest(_Model):
    """The fields of a SourceManifest this service reads."""

    id: str
    kind: Literal["documents", "structured"]
    status: Literal["ready", "ingesting", "empty", "failed"]
    database: Database | None = None
    prediction: Prediction | None = None


@dataclass(frozen=True)
class Source:
    """A structured source a call may query: its manifest and its DuckDB file."""

    manifest: SourceManifest
    path: Path

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def alias(self) -> str:
        assert self.manifest.database is not None
        return self.manifest.database.alias

    @property
    def tables(self) -> list[TableInfo]:
        assert self.manifest.database is not None
        return self.manifest.database.tables

    @property
    def templates(self) -> list[Template]:
        return self.manifest.prediction.templates if self.manifest.prediction else []

    def table(self, name: str) -> TableInfo | None:
        """The table called `name`, in any case."""
        return next((table for table in self.tables if table.name.casefold() == name.casefold()), None)


def read_source(knowledge_dir: Path, source_id: str) -> SourceManifest | None:
    """The source's manifest, or None when the catalog has no such source."""
    if not SOURCE_ID.fullmatch(source_id):
        raise CatalogError(f"{source_id!r} is not a source id")
    try:
        text = (knowledge_dir / "catalog" / "sources" / f"{source_id}.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        manifest = SourceManifest.model_validate_json(text)
    except ValidationError as error:
        raise CatalogError(f"{source_id} has an unreadable manifest") from error
    if manifest.id != source_id:
        raise CatalogError(f"{source_id}'s manifest names another source ({manifest.id!r})")
    return manifest


def structured_sources(knowledge_dir: Path, source_ids: list[str]) -> list[Source]:
    """The requested sources, sorted by id and without repeats, when every one can be predicted over."""
    requested = sorted(set(source_ids))
    if not requested:
        raise CatalogError("source_ids is empty: select at least one structured source")
    sources, problems = [], []
    for source_id in requested:
        try:
            sources.append(_structured(knowledge_dir, source_id))
        except CatalogError as error:
            problems.append(str(error))
    if problems:
        raise CatalogError("; ".join(problems))
    aliases = [source.alias for source in sources]
    if len(set(aliases)) != len(aliases):
        raise CatalogError(f"two of the selected sources share an alias: {aliases}")
    return sources


def _structured(knowledge_dir: Path, source_id: str) -> Source:
    manifest = read_source(knowledge_dir, source_id)
    if manifest is None:
        raise CatalogError(f"{source_id} is not in the knowledge catalog")
    if manifest.kind != "structured":
        raise CatalogError(f"{source_id} is a {manifest.kind} source, not a structured source")
    if manifest.status not in QUERYABLE:
        raise CatalogError(f"{source_id} has status {manifest.status!r}; only ready or ingesting sources are used")
    database = manifest.database
    if database is None or not DATABASE_PATH.fullmatch(database.path) or ".." in database.path.split("/"):
        raise CatalogError(f"{source_id} has no valid database in its manifest")
    if not ALIAS.fullmatch(database.alias) or database.alias in RESERVED_ALIASES:
        raise CatalogError(f"{source_id} has an alias that cannot be attached: {database.alias!r}")
    path = knowledge_dir / database.path
    if not path.is_file():
        raise CatalogError(f"{source_id} has no tables yet")
    return Source(manifest=manifest, path=path)
