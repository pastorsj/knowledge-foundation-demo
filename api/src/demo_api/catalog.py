# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The knowledge catalog: every industry pack and the user's workspace, side by side under ``/knowledge``.

The ingest service is its only writer (atomically); the API reads it, read-only, once per request
(``KnowledgeCatalog.read``, in a thread)::

    /knowledge/catalog/packs/<pack_id>.json        PackManifest   (contracts/catalog/pack-manifest.schema.json)
    /knowledge/catalog/sources/<source_id>.json    SourceManifest (contracts/catalog/source-manifest.schema.json)
    /knowledge/sources/<source_id>/tables.duckdb   a structured source's tables

A manifest that does not match its schema is logged and skipped, so it never takes the other packs down.
A source is offered only while the running stack can serve it:

- its capabilities are narrowed to the tool families in the agent image (``AGENT_FEATURES``);
- its status is ``ready`` or ``ingesting`` (what is ready stays usable while more files ingest);
- a structured source also needs at least one table, in its own ``sources/<id>/tables.duckdb``.

A pack offers the questions whose sources are offered and whose tool pills the stack serves (``kumo`` needs the
``kumo`` feature, ``duckdb`` the ``tables`` one), and the conversations whose sources are offered.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from .pack import PackConversationView
from .pack import PackQuestionView
from .pack import PackSummary
from .pack import PackView
from .pack import picker_examples
from .registry import ToolRegistry

logger = logging.getLogger(__name__)

PACK_ID = r"^[a-z][a-z0-9-]*$"
# The manifests' JSON Schemas (contracts/catalog); the image copies them to /opt/demo-api/contracts/catalog
SCHEMA_DIR = Path(__file__).resolve().parents[3] / "contracts" / "catalog"
# Source statuses whose content a tool can use
USABLE_STATUSES = frozenset({"ready", "ingesting"})
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
    """The catalog under ``root``. Each call reads it anew; ``read()`` reads it once for a whole request.

    A manifest is parsed and validated once per version of its file (inode, size, modification and change times;
    ingest replaces a manifest whole), so a request costs a ``stat`` per manifest and a bad one is logged once. Cached
    manifests are shared between requests: read them, never change them.
    """

    def __init__(
        self, root: Path, registry: ToolRegistry, features: frozenset[str], *, schema_dir: Path = SCHEMA_DIR
    ) -> None:
        self.root = root
        self._families = frozenset(registry.families(features))
        self._pills = frozenset(registry.pills(features))
        self._validators = {
            kind: Draft202012Validator(json.loads((schema_dir / f"{name}.schema.json").read_text(encoding="utf-8")))
            for kind, name in (("packs", "pack-manifest"), ("sources", "source-manifest"))
        }
        self._parsed: dict[Path, tuple[tuple[int, ...], dict[str, Any] | None]] = {}

    def read(self) -> CatalogSnapshot:
        """Every valid manifest, read now. Blocking: a route runs it in a thread, once per request."""
        catalog = self.root / "catalog"
        if not catalog.is_dir():
            raise CatalogUnavailableError("The knowledge catalog is not built yet; ingest writes it on startup.")
        packs, sources = self._manifests(catalog, "packs"), self._manifests(catalog, "sources")
        return CatalogSnapshot(self.root, packs, sources, self._families, self._pills)

    def packs(self) -> list[PackSummary]:
        return self.read().packs()

    def pack_view(self, pack_id: str | None = None) -> PackView:
        return self.read().pack_view(pack_id)

    def sources(self, pack_id: str | None = None) -> list[Source]:
        return self.read().sources(pack_id)

    def source(self, source_id: str) -> Source | None:
        return self.read().source(source_id)

    def database_path(self, source_id: str) -> Path | None:
        return self.read().database_path(source_id)

    def _manifests(self, catalog: Path, kind: str) -> list[dict[str, Any]]:
        """Every manifest in ``catalog/<kind>`` that matches its schema; any other is logged and skipped."""
        manifests = []
        paths = sorted((catalog / kind).glob("*.json"))
        for path in paths:
            manifest = self._manifest(path, kind)
            # A manifest is named after its id; anything else (a temporary file, say) is not one
            if manifest is not None and manifest["id"] == path.stem:
                manifests.append(manifest)
        directory, present = catalog / kind, set(paths)
        for gone in [path for path in self._parsed if path.parent == directory and path not in present]:
            self._parsed.pop(gone, None)
        return manifests

    def _manifest(self, path: Path, kind: str) -> dict[str, Any] | None:
        """The file's manifest if it matches its schema, else None (logged once per version of the file)."""
        try:
            stat = path.stat()
        except OSError:
            return None  # removed since the directory was listed
        version = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if (cached := self._parsed.get(path)) is not None and cached[0] == version:
            return cached[1]
        manifest: dict[str, Any] | None
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("Skipping unreadable catalog manifest %s/%s", kind, path.name)
            manifest = None
        if manifest is not None and (error := best_match(self._validators[kind].iter_errors(manifest))):
            where = "/".join(str(part) for part in error.absolute_path) or "the manifest"
            logger.warning("Skipping catalog manifest %s/%s: %s: %s", kind, path.name, where, error.message)
            manifest = None
        self._parsed[path] = (version, manifest)
        return manifest


class CatalogSnapshot:
    """The catalog as one read found it: its packs, and the sources this stack can serve."""

    def __init__(
        self,
        root: Path,
        packs: list[dict[str, Any]],
        sources: list[dict[str, Any]],
        families: frozenset[str],
        pills: frozenset[str],
    ) -> None:
        self.root = root
        self._pills = pills
        # The industries by title, then the workspace
        self._packs = sorted(packs, key=lambda m: (m["kind"] != "industry", m["title"].casefold(), m["id"]))
        pack_order = {pack["id"]: index for index, pack in enumerate(self._packs)}
        source_order = {id_: index for pack in self._packs for index, id_ in enumerate(pack["sources"])}
        offered = [source for manifest in sources if (source := _source(manifest, families)) is not None]
        self._sources = sorted(
            offered,
            key=lambda s: (pack_order.get(s.pack_id, len(pack_order)), source_order.get(s.id, len(source_order)), s.id),
        )

    def packs(self) -> list[PackSummary]:
        """``GET /v1/packs``: the industries by title, then the workspace."""
        return [_summary(manifest) for manifest in self._packs]

    def pack_view(self, pack_id: str | None = None) -> PackView:
        """``GET /v1/pack``: one pack (by default the first industry), with what this stack can answer of it."""
        if not self._packs:
            raise CatalogUnavailableError("The knowledge catalog has no packs yet; ingest is still syncing them.")
        manifest = self._packs[0] if pack_id is None else next((m for m in self._packs if m["id"] == pack_id), None)
        if manifest is None:
            raise PackNotFoundError(f"No pack named {pack_id} in the knowledge catalog.")
        available = {source.id for source in self._sources}
        questions = [
            question
            for question in manifest["questions"]
            if set(question["sources"]) <= available and set(question["tools"]) <= self._pills
        ]
        return PackView(
            **_summary(manifest).model_dump(),
            **{key: manifest.get(key) for key in ("version", "as_of", "disclaimer")},
            questions=[
                PackQuestionView(
                    **{key: question.get(key) for key in ("id", "label", "tag", "description", "question", "sources")},
                    tools=question["tools"],
                    featured=bool(question.get("featured", False)),
                )
                for question in questions
            ],
            # An empty list in the manifest means questions.yaml declared none: the picker's default order
            examples=picker_examples(questions, manifest["examples"] or None),
            conversations=[
                PackConversationView(
                    **{key: conversation.get(key) for key in ("id", "label", "tag", "description", "sources", "turns")}
                )
                for conversation in manifest["conversations"]
                if set(conversation["sources"]) <= available
            ],
        )

    def sources(self, pack_id: str | None = None) -> list[Source]:
        """The sources this stack can serve, of one pack or of every pack, in pack order."""
        if pack_id is not None and pack_id not in {pack["id"] for pack in self._packs}:
            raise PackNotFoundError(f"No pack named {pack_id} in the knowledge catalog.")
        return [source for source in self._sources if pack_id in (None, source.pack_id)]

    def source(self, source_id: str) -> Source | None:
        """An offered source, or None."""
        return next((source for source in self._sources if source.id == source_id), None)

    def database_path(self, source_id: str) -> Path | None:
        """The DuckDB file of an offered structured source, or None."""
        source = self.source(source_id)
        return self.root / source.database_file if source is not None and source.database_file else None


def _source(manifest: dict[str, Any], families: frozenset[str]) -> Source | None:
    """The source a valid manifest describes, or None when this stack cannot serve it."""
    if manifest["status"] not in USABLE_STATUSES:
        return None
    capabilities = tuple(family for family in manifest["capabilities"] if family in families)
    if not capabilities:
        return None
    kind = manifest["kind"]
    structured: dict[str, Any] = {}
    if kind == "structured":
        database = manifest["database"]
        # Its own directory only: sources/<id>/tables.duckdb, inside the knowledge volume
        if database["path"] != f"sources/{manifest['id']}/tables.duckdb" or not database["tables"]:
            return None
        structured = {
            "database_name": database["alias"],
            "database_file": database["path"],
            "tables": tuple(database["tables"]),
            "prediction_templates": tuple((manifest.get("prediction") or {}).get("templates", [])),
        }
    return Source(
        id=manifest["id"],
        pack_id=manifest["pack_id"],
        name=manifest["name"],
        description=manifest["description"],
        agent_description=manifest["agent_description"] or manifest["description"],
        kind=kind,
        capabilities=capabilities,
        synthetic=manifest["synthetic"],
        default_enabled=manifest["default_enabled"],
        example_questions=tuple(manifest["example_questions"]),
        status=manifest["status"],
        collection=manifest["documents"]["collection"] if kind == "documents" else None,
        **structured,
    )


def _summary(manifest: dict[str, Any]) -> PackSummary:
    return PackSummary(**{key: manifest.get(key) for key in ("id", "kind", "title", "description", "icon", "status")})
