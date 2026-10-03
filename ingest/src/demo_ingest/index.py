# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The shared Milvus collection behind the ``knowledge`` alias, which the retrieval server searches.

The schema is tools/retrieval's (``store.create_collection``, copied here: services share no code): ``source_id``
as partition key, HNSW/COSINE, and dynamic fields for each chunk's metadata. Ingest creates ``knowledge__v1`` and
the alias on first use, then keeps it current per document: delete the document's chunks, insert its new ones.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from typing import Any

from pymilvus import DataType
from pymilvus import MilvusClient

from .documents import Chunk

VECTOR_FIELD = "embedding"
INDEX = {"index_type": "HNSW", "metric_type": "COSINE", "params": {"M": 16, "efConstruction": 200}}
VERSION = "v1"
# Keys a chunk's dynamic metadata never carries: the VL reranker reads `image` as a picture of the passage.
RESERVED = {"image", "chunk_id", "source_id", "document_id", "title", "url", "published_at", "text", VECTOR_FIELD}
HEADING_SEPARATOR = " › "


def create_collection(client: MilvusClient, name: str, dimension: int) -> None:
    """tools/retrieval/src/demo_retrieval/store.py:create_collection, field for field."""
    schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
    schema.add_field("chunk_id", DataType.VARCHAR, max_length=512, is_primary=True)
    schema.add_field("source_id", DataType.VARCHAR, max_length=128, is_partition_key=True)
    schema.add_field("document_id", DataType.VARCHAR, max_length=512)
    schema.add_field("title", DataType.VARCHAR, max_length=4096)
    schema.add_field("url", DataType.VARCHAR, max_length=4096, nullable=True)
    schema.add_field("published_at", DataType.VARCHAR, max_length=64, nullable=True)
    schema.add_field("text", DataType.VARCHAR, max_length=65535)
    schema.add_field(VECTOR_FIELD, DataType.FLOAT_VECTOR, dim=dimension)
    index = client.prepare_index_params()
    index.add_index(VECTOR_FIELD, **INDEX)
    client.create_collection(name, schema=schema, index_params=index, consistency_level="Strong")


def citation(title: str, page: int | None) -> str:
    return f"{title}, p. {page}" if page is not None else title


class KnowledgeIndex:
    def __init__(self, uri: str, alias: str) -> None:
        self.alias = alias
        self.collection_name = f"{alias}__{VERSION}"
        self._client = MilvusClient(uri=uri)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    def ping(self) -> bool:
        with self._lock:
            self._client.list_collections()
        return True

    def _target(self) -> str | None:
        # list_aliases returns {"aliases": [...]}; describe_alias on a missing alias would raise and log an error.
        if self.alias not in self._client.list_aliases()["aliases"]:
            return None
        return self._client.describe_alias(self.alias)["collection_name"]

    def _ensure(self, dimension: int) -> str:
        target = self._target()
        if target is not None:
            return target
        if not self._client.has_collection(self.collection_name):
            create_collection(self._client, self.collection_name, dimension)
        self._client.create_alias(self.collection_name, self.alias)
        return self.collection_name

    def replace_document(
        self,
        *,
        source_id: str,
        document_id: str,
        title: str,
        url: str | None,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
        metadata: dict[str, Any],
    ) -> int:
        """Delete the document's chunks, then insert these; returns how many were inserted."""
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
        shared = {key: value for key, value in metadata.items() if key not in RESERVED and value is not None}
        rows = []
        for number, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True), start=1):
            row = {
                **shared,
                "citation": citation(title, chunk.page_start),
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "headings": HEADING_SEPARATOR.join(chunk.headings),
                "labels": list(chunk.labels),
            }
            rows.append(
                {
                    **{key: value for key, value in row.items() if value is not None},
                    "chunk_id": f"{document_id}:{number:04d}",
                    "source_id": source_id,
                    "document_id": document_id,
                    "title": title[:4096],
                    "url": url,
                    "published_at": None,
                    "text": chunk.text[:65535],
                    VECTOR_FIELD: list(vector),
                }
            )
        with self._lock:
            target = self._target() if not rows else self._ensure(len(vectors[0]))
            if target is None:  # nothing indexed yet, so nothing to delete
                return 0
            self._client.delete(target, filter=f"document_id == {json.dumps(document_id)}")
            if rows:
                self._client.insert(target, rows)
        return len(rows)

    def delete_document(self, document_id: str) -> None:
        self._delete(f"document_id == {json.dumps(document_id)}")

    def delete_source(self, source_id: str) -> None:
        self._delete(f"source_id == {json.dumps(source_id)}")

    def _delete(self, expression: str) -> None:
        with self._lock:
            if (target := self._target()) is not None:
                self._client.delete(target, filter=expression)

    def count(self, *, source_id: str | None = None, document_id: str | None = None) -> int:
        filters = [
            f"{name} == {json.dumps(value)}"
            for name, value in (("source_id", source_id), ("document_id", document_id))
            if value
        ]
        with self._lock:
            target = self._target()
            if target is None:
                return 0
            result = self._client.query(
                target, filter=" and ".join(filters) or 'chunk_id != ""', output_fields=["count(*)"]
            )
        return int(result[0]["count(*)"])
