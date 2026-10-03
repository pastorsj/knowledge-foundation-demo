# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The knowledge catalog: every industry pack and the user's workspace, side by side under ``/knowledge``.

The ingest service is its only writer (atomically); the API reads it, read-only, on every call::

    /knowledge/catalog/packs/<pack_id>.json        PackManifest   (contracts/catalog/pack-manifest.schema.json)
    /knowledge/catalog/sources/<source_id>.json    SourceManifest (contracts/catalog/source-manifest.schema.json)
    /knowledge/sources/<source_id>/tables.duckdb   a structured source's tables

A source is offered only while the running stack can serve it:

- its capabilities are narrowed to the tool families in the agent image (``AGENT_FEATURES``);
- its status is ``ready`` or ``ingesting`` (what is ready stays usable while more files ingest);
- a structured source also needs at least one table in a DuckDB file inside the knowledge volume.

A pack offers the questions whose sources are offered and whose tool pills the stack serves (``kumo`` needs the
``kumo`` feature, ``duckdb`` the ``tables`` one), and the conversations whose sources are offered.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .pack import PackConversationView
from .pack import PackQuestionView
from .pack import PackSummary
from .pack import PackView
from .pack import picker_examples
from .registry import ToolRegistry

logger = logging.getLogger(__name__)

PACK_ID = r"^[a-z][a-z0-9-]*$"
# Source statuses whose content a tool can use
USABLE_STATUSES = frozenset({"ready", "ingesting"})
# The DuckDB file of a structured source, relative to the knowledge root (the source manifest's pattern)
_DATABASE_PATH = re.compile(r"^sources/[^/]+/tables\.duckdb$")
# What the agent sees of a structured source in its run instructions
MAX_AGENT_TABLES = 30
MAX_AGENT_COLUMNS = 40


class CatalogUnavailableError(Exception):
    """The knowledge catalog has not been written yet (ingest has not started, or is still syncing the packs)."""


class PackNotFoundError(Exception):
    """No pack of the catalog has this id."""


@dataclass(frozen=True, slots=True)
class Source:
    """One source of the catalog, as this stack can serve it."""

    id: str
    pack_id: str
    name: str
    description: str
    agent_description: str
    kind: str  # documents or structured
    capabilities: tuple[str, ...]  # narrowed to the running tool families
    synthetic: bool
    default_enabled: bool
    example_questions: tuple[str, ...]
    status: str
    database_name: str | None = None  # a structured source's DuckDB alias (retail.sales -> retail_sales)
    database_file: str | None = None  # its DuckDB file, relative to the knowledge root
    tables: tuple[dict[str, Any], ...] = ()  # its TableInfo, as profiled by ingest
    prediction_templates: tuple[dict[str, Any], ...] = ()
    collection: str | None = None  # a documents source's Milvus collection alias

    def public(self) -> dict[str, Any]:
        """``GET /v1/data_sources``: what the UI shows of the source."""
        return {
            "id": self.id,
            "pack_id": self.pack_id,
            "name": self.name,
            "description": self.description,
            "default_enabled": self.default_enabled,
            "kind": self.kind,
            "capabilities": list(self.capabilities),
            "synthetic": self.synthetic,
            "status": self.status,
            "database_name": self.database_name,
        }

    def catalog_entry(self) -> dict[str, Any]:
        """How the agent sees this source in its run instructions.

        A structured source carries its database (the alias its tables are qualified with, and each table's
        columns, keys and row count) and the prediction templates it offers when the prediction tool runs, so the
        agent writes SQL and PQL without a schema tool. At most ``MAX_AGENT_TABLES`` tables of at most
        ``MAX_AGENT_COLUMNS`` columns each; ``omitted_tables``/``omitted_columns`` count the rest.
        """
        entry: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.agent_description,
            "kind": self.kind,
            "capabilities": list(self.capabilities),
            "synthetic": self.synthetic,
            "example_questions": list(self.example_questions),
        }
        if self.kind != "structured":
            return entry
        database: dict[str, Any] = {
            "alias": self.database_name,
            "tables": [_agent_table(table) for table in self.tables[:MAX_AGENT_TABLES]],
        }
        if len(self.tables) > MAX_AGENT_TABLES:
            database["omitted_tables"] = len(self.tables) - MAX_AGENT_TABLES
        predicts = "structured_prediction" in self.capabilities
        return entry | {
            "database": database,
            "prediction_templates": [_agent_template(template) for template in self.prediction_templates]
            if predicts
            else [],
        }


def _agent_table(table: dict[str, Any]) -> dict[str, Any]:
    columns = table.get("columns", [])
    view = {
        "name": table["name"],
        "description": table.get("description", ""),
        "row_count": table.get("row_count"),
        "primary_key": table.get("primary_key"),
        "time_column": table.get("time_column"),
        "columns": [
            {"name": column["name"], "type": column["type"], "description": column.get("description", "")}
            for column in columns[:MAX_AGENT_COLUMNS]
        ],
        "foreign_keys": [
            {key: foreign_key[key] for key in ("column", "references_table", "references_column")}
            for foreign_key in table.get("foreign_keys", [])
        ],
    }
    if len(columns) > MAX_AGENT_COLUMNS:
        view["omitted_columns"] = len(columns) - MAX_AGENT_COLUMNS
    return view


def _agent_template(template: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "name", "description", "pql", "anchor_time")
    return {key: template[key] for key in keys if key in template}


class KnowledgeCatalog:
    def __init__(self, root: Path, registry: ToolRegistry, features: frozenset[str]) -> None:
        self.root = root
        self._families = registry.families(features)
        self._pills = registry.pills(features)

    # ------------------------------------------------------------------ packs

    def packs(self) -> list[PackSummary]:
        """``GET /v1/packs``: the industries by title, then the workspace."""
        return [_summary(manifest) for manifest in self._pack_manifests()]

    def pack_view(self, pack_id: str | None = None) -> PackView:
        """``GET /v1/pack``: one pack (by default the first industry), with what this stack can answer of it."""
        manifests = self._pack_manifests()
        if not manifests:
            raise CatalogUnavailableError("The knowledge catalog has no packs yet; ingest is still syncing them.")
        manifest = manifests[0] if pack_id is None else next((m for m in manifests if m["id"] == pack_id), None)
        if manifest is None:
            raise PackNotFoundError(f"No pack named {pack_id} in the knowledge catalog.")
        available = {source.id for source in self.sources()}
        questions = [
            question
            for question in manifest.get("questions", [])
            if set(question.get("sources", [])) <= available and set(question.get("tools", [])) <= self._pills
        ]
        return PackView(
            **_summary(manifest).model_dump(),
            **{key: manifest.get(key) for key in ("version", "as_of", "disclaimer")},
            questions=[
                PackQuestionView(
                    **{key: question.get(key) for key in ("id", "label", "tag", "description", "question", "sources")},
                    tools=question.get("tools", []),
                    featured=bool(question.get("featured", False)),
                )
                for question in questions
            ],
            # An empty list in the manifest means questions.yaml declared none: the picker's default order
            examples=picker_examples(questions, manifest.get("examples") or None),
            conversations=[
                PackConversationView(
                    **{key: conversation.get(key) for key in ("id", "label", "tag", "description", "sources", "turns")}
                )
                for conversation in manifest.get("conversations", [])
                if set(conversation.get("sources", [])) <= available
            ],
        )

    def _pack_manifests(self) -> list[dict[str, Any]]:
        """The readable pack manifests, in the order ``GET /v1/packs`` lists them."""
        manifests = []
        for manifest in self._manifests("packs"):
            try:
                _summary(manifest)
            except ValidationError:
                logger.warning("Skipping pack manifest %s: it does not match PackManifest", manifest.get("id"))
                continue
            manifests.append(manifest)
        return sorted(
            manifests,
            key=lambda m: (m["kind"] != "industry", str(m.get("title", "")).casefold(), m["id"]),
        )

    # ---------------------------------------------------------------- sources

    def sources(self, pack_id: str | None = None) -> list[Source]:
        """The sources this stack can serve, of one pack or of every pack, in pack order."""
        packs = self._pack_manifests()
        if pack_id is not None and pack_id not in {pack["id"] for pack in packs}:
            raise PackNotFoundError(f"No pack named {pack_id} in the knowledge catalog.")
        pack_order = {pack["id"]: index for index, pack in enumerate(packs)}
        source_order = {source_id: index for pack in packs for index, source_id in enumerate(pack.get("sources", []))}
        sources = [
            source
            for manifest in self._manifests("sources")
            if (source := self._source(manifest)) is not None and pack_id in (None, source.pack_id)
        ]
        return sorted(
            sources,
            key=lambda s: (pack_order.get(s.pack_id, len(packs)), source_order.get(s.id, len(source_order)), s.id),
        )

    def source(self, source_id: str) -> Source | None:
        """An offered source, or None."""
        return next((source for source in self.sources() if source.id == source_id), None)

    def database_path(self, source_id: str) -> Path | None:
        """The DuckDB file of an offered structured source, or None."""
        source = self.source(source_id)
        if source is None or source.database_file is None:
            return None
        return self.root / source.database_file

    def _source(self, manifest: dict[str, Any]) -> Source | None:
        """The source a manifest describes, or None when this stack cannot serve it."""
        if manifest.get("status") not in USABLE_STATUSES:
            return None
        capabilities = tuple(family for family in manifest.get("capabilities", []) if family in self._families)
        if not capabilities:
            return None
        kind = manifest.get("kind")
        database = manifest.get("database") or {}
        structured: dict[str, Any] = {}
        if kind == "structured":
            path, alias, tables = database.get("path"), database.get("alias"), database.get("tables") or []
            if not (isinstance(path, str) and _DATABASE_PATH.fullmatch(path) and alias and tables):
                return None
            templates = (manifest.get("prediction") or {}).get("templates", [])
            structured = {
                "database_name": alias,
                "database_file": path,
                "tables": tuple(tables),
                "prediction_templates": tuple(templates),
            }
        elif kind != "documents":
            return None
        return Source(
            id=manifest["id"],
            pack_id=str(manifest.get("pack_id") or "workspace"),
            name=manifest.get("name") or manifest["id"],
            description=manifest.get("description", ""),
            agent_description=manifest.get("agent_description") or manifest.get("description", ""),
            kind=kind,
            capabilities=capabilities,
            synthetic=bool(manifest.get("synthetic", False)),
            default_enabled=bool(manifest.get("default_enabled", True)),
            example_questions=tuple(manifest.get("example_questions", [])),
            status=manifest["status"],
            collection=(manifest.get("documents") or {}).get("collection") if kind == "documents" else None,
            **structured,
        )

    # -------------------------------------------------------------- manifests

    def _manifests(self, kind: str) -> list[dict[str, Any]]:
        """Every readable manifest in ``catalog/<kind>``, read now; one that is unreadable is skipped."""
        catalog = self.root / "catalog"
        if not catalog.is_dir():
            raise CatalogUnavailableError("The knowledge catalog is not built yet; ingest writes it on startup.")
        manifests = []
        for path in sorted((catalog / kind).glob("*.json")):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning("Skipping unreadable catalog manifest %s/%s", kind, path.name)
                continue
            # A manifest is named after its id; anything else (a temporary file, say) is not one
            if isinstance(manifest, dict) and manifest.get("id") == path.stem:
                manifests.append(manifest)
        return manifests


def _summary(manifest: dict[str, Any]) -> PackSummary:
    return PackSummary(**{key: manifest.get(key) for key in ("id", "kind", "title", "description", "icon", "status")})
