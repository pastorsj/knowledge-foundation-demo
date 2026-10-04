# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Types every stage shares: the error a file fails with, the pipeline stages and their AI-Q status."""

from __future__ import annotations

import re
from datetime import UTC
from datetime import datetime
from enum import StrEnum

URL = re.compile(r"https?://\S+")
PATH = re.compile(r"(?<![\w.])/(?:[\w.-]+/)+[\w.-]*")


def redact(error: BaseException | str, limit: int = 300) -> str:
    """The first line of an error, without URLs or absolute paths: what the job status and manifests may show."""
    lines = str(error).strip().splitlines()
    text = lines[0] if lines else type(error).__name__
    return PATH.sub("<path>", URL.sub("<url>", text))[:limit]


class IngestError(Exception):
    """A file that cannot be ingested. ``code`` is stable (the UI may map it); ``message`` is for people.

    ``transient``: another try may succeed (Parse did not answer); ``fallback``: why Parse was not used, as Outcome.
    """

    transient = False
    fallback: str | None = None

    MESSAGES = {
        "unsupported_type": "This file type is not supported.",
        "type_mismatch": "The file's content does not match its extension.",
        "empty_file": "The file is empty.",
        "too_large": "The file is larger than the upload limit.",
        "parser_unavailable": "Nemotron Parse is unavailable, and an image has no text layer to fall back to.",
        "conversion_failed": "The document could not be converted.",
        "load_failed": "The table could not be loaded.",
        "embedding_failed": "The document's passages could not be embedded.",
        "timeout": "A pipeline stage took too long.",
        "internal_error": "The file could not be ingested.",
    }

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = (message or self.MESSAGES.get(code, code))[:600]
        super().__init__(f"{code}: {self.message}")


class Stage(StrEnum):
    RECEIVED = "received"
    DETECTED = "detected"
    PARSING = "parsing"  # PDF and images, page i of n
    CONVERTING = "converting"  # born-digital documents
    LOADING = "loading"  # tables, sheet i of n
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    PROFILING = "profiling"
    READY = "ready"
    FAILED = "failed"


class FileStatus(StrEnum):
    """AI-Q's DocumentFileStatus."""

    UPLOADING = "uploading"
    INGESTING = "ingesting"
    SUCCESS = "success"
    FAILED = "failed"


class JobState(StrEnum):
    """AI-Q's JobState."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL = frozenset({FileStatus.SUCCESS, FileStatus.FAILED})


def status_for(stage: str | None) -> FileStatus:
    if stage is None:
        return FileStatus.UPLOADING
    if stage == Stage.READY:
        return FileStatus.SUCCESS
    if stage == Stage.FAILED:
        return FileStatus.FAILED
    return FileStatus.INGESTING


def utcnow() -> str:
    """ISO 8601 UTC with a Z, as the catalog contracts write it."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
