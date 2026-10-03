# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The knowledge catalog under /knowledge: pack and source manifests, and each source's files.

Ingest is the only writer. Every write validates against the shared contract (contracts/catalog) and is atomic:
the JSON goes to a temporary file in the same directory, then ``os.replace`` swaps it in, so a reader sees the old
manifest or the new one, never half of one. Readers run under other uids, so files are 0644.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .settings import Settings

SOURCE_ID = re.compile(r"^[a-z][a-z0-9_-]*\.[a-z][a-z0-9_]*$")
PACK_ID = re.compile(r"^[a-z][a-z0-9-]*$")


class CatalogError(Exception):
    """A manifest that does not satisfy its contract, or an id that is not a catalog id."""


@cache
def _validator(path: Path) -> Draft202012Validator:
    schema = json.loads(path.read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def _validate(validator: Draft202012Validator, manifest: dict[str, Any], what: str) -> None:
    errors = sorted(validator.iter_errors(manifest), key=lambda error: list(error.absolute_path))
    if errors:
        details = "; ".join(f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors[:5])
        raise CatalogError(f"invalid {what} {manifest.get('id')!r}: {details}")


def write_json(path: Path, value: Any) -> None:
    """Atomically replace ``path`` with ``value`` as JSON, readable by every uid."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as f:
        try:
            json.dump(value, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
            os.chmod(f.name, 0o644)
        except BaseException:
            os.unlink(f.name)
            raise
    os.replace(f.name, path)


class Catalog:
    def __init__(self, root: Path, schema_dir: Path | None = None) -> None:
        self.root = root
        schema_dir = schema_dir or Settings().catalog_schema_dir
        self._source_schema = schema_dir / "source-manifest.schema.json"
        self._pack_schema = schema_dir / "pack-manifest.schema.json"
        self._sources = root / "catalog" / "sources"
        self._packs = root / "catalog" / "packs"
        for directory in (self._sources, self._packs, root / "sources"):
            directory.mkdir(parents=True, exist_ok=True)

    # Sources

    def write_source(self, manifest: dict[str, Any]) -> None:
        _validate(_validator(self._source_schema), manifest, "source manifest")
        write_json(self._sources / f"{_source_id(manifest['id'])}.json", manifest)

    def read_source(self, source_id: str) -> dict[str, Any] | None:
        if not SOURCE_ID.match(source_id):
            return None
        return _read(self._sources / f"{source_id}.json")

    def sources(self) -> list[dict[str, Any]]:
        return [m for path in sorted(self._sources.glob("*.json")) if (m := _read(path)) is not None]

    def delete_source(self, source_id: str) -> None:
        """Remove the manifest, then the source's files, DuckDB and parsed documents."""
        (self._sources / f"{_source_id(source_id)}.json").unlink(missing_ok=True)
        shutil.rmtree(self.source_dir(source_id), ignore_errors=True)

    def source_dir(self, source_id: str) -> Path:
        return self.root / "sources" / _source_id(source_id)

    # Packs

    def write_pack(self, manifest: dict[str, Any]) -> None:
        _validate(_validator(self._pack_schema), manifest, "pack manifest")
        write_json(self._packs / f"{_pack_id(manifest['id'])}.json", manifest)

    def read_pack(self, pack_id: str) -> dict[str, Any] | None:
        if not PACK_ID.match(pack_id):
            return None
        return _read(self._packs / f"{pack_id}.json")

    def packs(self) -> list[dict[str, Any]]:
        return [m for path in sorted(self._packs.glob("*.json")) if (m := _read(path)) is not None]

    def writable(self) -> bool:
        probe = self.root / "catalog" / ".writable"
        try:
            probe.write_text("ok")
            probe.unlink()
        except OSError:
            return False
        return True


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


def _source_id(source_id: str) -> str:
    if not isinstance(source_id, str) or not SOURCE_ID.match(source_id):
        raise CatalogError(f"not a source id: {source_id!r}")
    return source_id


def _pack_id(pack_id: str) -> str:
    if not isinstance(pack_id, str) or not PACK_ID.match(pack_id):
        raise CatalogError(f"not a pack id: {pack_id!r}")
    return pack_id
