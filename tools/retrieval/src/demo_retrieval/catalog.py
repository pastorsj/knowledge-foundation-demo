# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The knowledge catalog: `<KNOWLEDGE_DIR>/catalog/sources/<source_id>.json`, read on every call.

The ingest service writes each source manifest atomically (contracts/catalog/source-manifest.schema.json). A call may
search a source when its manifest exists, its kind is `documents` and its status is `ready` or `ingesting` (what an
ingesting source already holds stays usable). Only the manifests a call names are read.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import ValidationError

# contracts/catalog/source-manifest.schema.json's id pattern; it also keeps a source id from naming another path.
SOURCE_ID = re.compile(r"[a-z][a-z0-9_-]*\.[a-z][a-z0-9_]*")
SEARCHABLE = ("ready", "ingesting")


class CatalogError(ValueError):
    """A source a call may not search. The message is written here and safe to show the agent."""


class SourceManifest(BaseModel):
    """The fields of a SourceManifest this service reads."""

    model_config = ConfigDict(extra="ignore")

    id: str
    kind: Literal["documents", "structured"]
    status: Literal["ready", "ingesting", "empty", "failed"]


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


def document_sources(knowledge_dir: Path, source_ids: list[str]) -> list[str]:
    """The requested ids, sorted and without repeats, when every one is a searchable documents source."""
    requested = sorted(set(source_ids))
    if not requested:
        raise CatalogError("source_ids is empty: select at least one document source")
    problems = []
    for source_id in requested:
        manifest = read_source(knowledge_dir, source_id)
        if manifest is None:
            problems.append(f"{source_id} is not in the knowledge catalog")
        elif manifest.kind != "documents":
            problems.append(f"{source_id} is a {manifest.kind} source, not a documents source")
        elif manifest.status not in SEARCHABLE:
            problems.append(f"{source_id} has status {manifest.status!r}; only ready or ingesting sources are searched")
    if problems:
        raise CatalogError("; ".join(problems))
    return requested
