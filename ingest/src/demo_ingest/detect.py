# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""What a file is: an extension allowlist, then its content, so a renamed file is refused before any parser sees it.

Binary formats must carry their own signature (``filetype``, docling's own dependency, and Parquet's magic bytes).
Text formats have none to check, so they are only refused when their bytes are a known binary format or hold NULs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import filetype

from .models import IngestError

Kind = Literal["document", "table"]

IMAGES = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".webp": "webp",
    ".bmp": "bmp",
}
DOCUMENTS = {
    ".pdf": "pdf",
    **IMAGES,
    ".docx": "docx",
    ".pptx": "pptx",
    ".html": "html",
    ".htm": "html",
    ".md": "md",
    ".txt": "txt",
}
TABLES = {".csv": "csv", ".tsv": "tsv", ".xlsx": "xlsx", ".parquet": "parquet", ".json": "json", ".jsonl": "jsonl"}
TEXT_FORMATS = {"html", "md", "txt", "csv", "tsv", "json", "jsonl"}
IMAGE_FORMATS = set(IMAGES.values())
# filetype's extension for each binary format; Office files are zip archives, which older writers leave generic.
SIGNATURES = {
    "pdf": {"pdf"},
    "docx": {"docx", "zip"},
    "pptx": {"pptx", "zip"},
    "xlsx": {"xlsx", "zip"},
}
FILETYPE_IMAGES = {"png": "png", "jpg": "jpeg", "tif": "tiff", "webp": "webp", "bmp": "bmp"}
ACCEPTED_EXTENSIONS = sorted(DOCUMENTS | TABLES)


@dataclass(frozen=True)
class Detected:
    kind: Kind
    format: str


def kind_of(file_name: str) -> Kind | None:
    """The kind an extension promises, before the content is checked; None when it is not supported."""
    suffix = Path(file_name).suffix.lower()
    return "document" if suffix in DOCUMENTS else "table" if suffix in TABLES else None


def detect(path: Path, file_name: str, *, max_bytes: int = 100 * 1024 * 1024) -> Detected:
    suffix = Path(file_name).suffix.lower()
    if suffix in DOCUMENTS:
        kind: Kind = "document"
        expected = DOCUMENTS[suffix]
    elif suffix in TABLES:
        kind, expected = "table", TABLES[suffix]
    else:
        shown = f"{suffix} files" if suffix else "Files without an extension"
        raise IngestError("unsupported_type", f"{shown} are not supported. Accepted: {', '.join(ACCEPTED_EXTENSIONS)}.")
    size = path.stat().st_size
    if size == 0:
        raise IngestError("empty_file", f"{file_name} is empty.")
    if size > max_bytes:
        raise IngestError(
            "too_large", f"{file_name} is {size / 2**20:.1f} MB; the limit is {max_bytes / 2**20:.0f} MB."
        )

    head = _head(path)
    guess = filetype.guess(head)
    found = guess.extension if guess else None
    if expected in IMAGE_FORMATS:
        if found not in FILETYPE_IMAGES:
            raise _mismatch(file_name, "an image", found)
        return Detected(kind, FILETYPE_IMAGES[found])  # a PNG named .jpg is still an image docling reads
    if expected == "parquet":
        if not (head.startswith(b"PAR1") and _tail(path, 4) == b"PAR1"):
            raise _mismatch(file_name, "a Parquet file", found)
    elif expected in TEXT_FORMATS:
        if found is not None or b"\x00" in head:
            raise _mismatch(file_name, "text", found or "binary data")
    elif found not in SIGNATURES[expected]:
        raise _mismatch(file_name, f"a {expected.upper()} file", found)
    return Detected(kind, expected)


def _mismatch(file_name: str, expected: str, found: str | None) -> IngestError:
    content = f"{found.upper()} content" if found and found != "binary data" else (found or "unrecognised content")
    return IngestError("type_mismatch", f"{file_name} should be {expected}, but it holds {content}.")


def _head(path: Path) -> bytes:
    with path.open("rb") as f:
        return f.read(8192)


def _tail(path: Path, count: int) -> bytes:
    with path.open("rb") as f:
        f.seek(-count, 2)
        return f.read(count)
