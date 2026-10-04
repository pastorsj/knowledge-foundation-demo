# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import embed
from pymilvus import MilvusClient

from demo_ingest.documents import Chunk
from demo_ingest.index import KnowledgeIndex


@pytest.fixture(scope="module")
def milvus_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    return str(tmp_path_factory.mktemp("milvus") / "milvus.db")


@pytest.fixture(scope="module")
def index(milvus_uri: str) -> Iterator[KnowledgeIndex]:
    index = KnowledgeIndex(milvus_uri, "knowledge")
    yield index
    index.close()


def chunks(*texts: str, page: int | None = 1) -> list[Chunk]:
    return [Chunk(text=t, page_start=page, page_end=page, headings=["Policy", "Fees"], labels=["text"]) for t in texts]


def replace(index: KnowledgeIndex, source_id: str, document_id: str, items: list[Chunk], **metadata) -> None:
    index.replace_document(
        source_id=source_id,
        document_id=document_id,
        title="policy.pdf",
        url=None,
        chunks=items,
        vectors=[embed(c.text) for c in items],
        metadata={"parser": "nemotron-parse-2.0", "file_id": "f-0123456789abcdef", **metadata},
    )


def rows(uri: str, document_id: str) -> list[dict]:
    client = MilvusClient(uri=uri)
    try:
        return client.query(
            "knowledge", filter=f'document_id == "{document_id}"', output_fields=["*"], consistency_level="Strong"
        )
    finally:
        client.close()


def test_the_index_creates_its_collection_behind_the_alias(index: KnowledgeIndex, milvus_uri: str):
    replace(index, "s.docs", "s.docs:a", chunks("first"))

    client = MilvusClient(uri=milvus_uri)
    try:
        assert client.describe_alias("knowledge")["collection_name"] == "knowledge__v1"
        fields = {f["name"]: f for f in client.describe_collection("knowledge__v1")["fields"]}
        assert fields["source_id"].get("is_partition_key") is True
        assert fields["embedding"]["params"]["dim"] == 2048
    finally:
        client.close()


def test_replacing_a_document_twice_keeps_only_the_second_version(index: KnowledgeIndex, milvus_uri: str):
    replace(index, "s.docs", "s.docs:b", chunks("one", "two", "three"))
    replace(index, "s.docs", "s.docs:b", chunks("four", "five"))

    stored = sorted(rows(milvus_uri, "s.docs:b"), key=lambda row: row["chunk_id"])
    assert [row["text"] for row in stored] == ["four", "five"]
    assert [row["chunk_id"] for row in stored] == ["s.docs:b:0001", "s.docs:b:0002"]


def test_chunks_carry_their_citation_and_metadata(index: KnowledgeIndex, milvus_uri: str):
    replace(index, "s.docs", "s.docs:c", chunks("paged", page=3), image="must not be stored")
    replace(index, "s.docs", "s.docs:d", chunks("unpaged", page=None))

    [paged] = rows(milvus_uri, "s.docs:c")
    assert paged["citation"] == "policy.pdf, p. 3"
    assert (paged["page_start"], paged["page_end"]) == (3, 3)
    assert paged["headings"] == "Policy › Fees"
    assert (paged["parser"], paged["file_id"]) == ("nemotron-parse-2.0", "f-0123456789abcdef")
    assert (paged["source_id"], paged["title"]) == ("s.docs", "policy.pdf")
    assert "image" not in paged
    [unpaged] = rows(milvus_uri, "s.docs:d")
    assert unpaged["citation"] == "policy.pdf"
    assert "page_start" not in unpaged


def test_delete_document(index: KnowledgeIndex):
    replace(index, "t.docs", "t.docs:a", chunks("alpha", "beta"))
    replace(index, "t.docs", "t.docs:b", chunks("gamma"))
    replace(index, "u.docs", "u.docs:a", chunks("delta"))

    index.delete_document("t.docs:a")

    assert index.count(source_id="t.docs") == 1
    assert index.count(source_id="u.docs") == 1


def test_an_empty_document_only_removes_its_old_chunks(index: KnowledgeIndex, milvus_uri: str):
    replace(index, "v.docs", "v.docs:a", chunks("old"))
    index.replace_document(
        source_id="v.docs", document_id="v.docs:a", title="x", url=None, chunks=[], vectors=[], metadata={}
    )

    assert rows(milvus_uri, "v.docs:a") == []


def test_a_new_index_on_an_existing_store_reuses_the_collection(index: KnowledgeIndex, milvus_uri: str, tmp_path: Path):
    replace(index, "w.docs", "w.docs:a", chunks("kept"))

    again = KnowledgeIndex(milvus_uri, "knowledge")
    try:
        assert again.count(source_id="w.docs") == 1
        assert again.ping() is True
    finally:
        again.close()
