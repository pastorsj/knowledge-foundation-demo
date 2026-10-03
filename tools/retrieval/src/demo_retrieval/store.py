# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Milvus layout: one collection of every documents source's chunks, behind the alias `knowledge` the server searches.

The ingest service writes the collection (`knowledge__v<N>`, with `create_collection`'s schema) and points the alias
at it; this service only searches, on the AsyncMilvusClient. The tests use `create_collection` to write a fixture.
"""

from __future__ import annotations

import json
from typing import Any

from pymilvus import AsyncMilvusClient
from pymilvus import DataType
from pymilvus import MilvusClient

ALIAS = "knowledge"
SCALAR_FIELDS = ("chunk_id", "source_id", "document_id", "title", "url", "published_at", "text")
VECTOR_FIELD = "embedding"
INDEX = {"index_type": "HNSW", "metric_type": "COSINE", "params": {"M": 16, "efConstruction": 200}}
# HNSW needs ef >= the search limit; the tool's per-source limit is at most 4 x 25 = 100.
SEARCH_PARAMS = {"metric_type": "COSINE", "params": {"ef": 128}}

Candidate = tuple[dict[str, Any], float]  # (stored fields including dynamic metadata, cosine similarity)


def create_collection(client: MilvusClient, name: str, dimension: int) -> None:
    # Dynamic fields hold each source's own metadata (file name, page, ...) beside these columns.
    schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
    schema.add_field("chunk_id", DataType.VARCHAR, max_length=512, is_primary=True)
    # The partition key keeps each source's chunks together, so a per-source filter scans one partition.
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


async def search(
    client: AsyncMilvusClient, collection: str, vector: list[float], source_id: str, limit: int
) -> list[Candidate]:
    """The chunks of one source nearest to the vector."""
    results = await client.search(
        collection,
        data=[vector],
        anns_field=VECTOR_FIELD,
        filter=f"source_id == {json.dumps(source_id)}",
        limit=limit,
        search_params=SEARCH_PARAMS,
        output_fields=[*SCALAR_FIELDS, "$meta"],
    )
    return [(hit["entity"], hit["distance"]) for hit in results[0]]
