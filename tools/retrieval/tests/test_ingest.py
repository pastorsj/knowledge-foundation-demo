# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests
from conftest import EMBED_URL
from conftest import TOPICS
from conftest import FakeNvidia
from conftest import documents
from conftest import write_pack
from pymilvus import MilvusClient
from tenacity import wait_none

from demo_retrieval import ingest
from demo_retrieval import store
from demo_retrieval.datapack import CollectionManifest
from demo_retrieval.settings import Settings


def test_ingest_embeds_passages_and_writes_the_manifest(settings: Settings, data_dir: Path, nvidia_api: FakeNvidia):
    manifest = ingest.run(settings, data_dir)

    long_chunks = len(ingest.splitter.split_text(documents()[-1]["text"]))
    assert long_chunks > 1
    assert manifest.chunk_count == 2 * len(TOPICS) + long_chunks
    assert manifest.document_count == 2 * len(TOPICS) + 1
    assert manifest.collection == "test_pack"
    assert manifest.physical_collection.startswith("test_pack__")
    assert manifest.source_ids == ["market_news", "market_regulations"]
    assert manifest.embed_model == "nvidia/nemotron-3-embed-1b"
    assert manifest.index == {"index_type": "HNSW", "metric_type": "COSINE", "params": {"M": 16, "efConstruction": 200}}
    assert CollectionManifest.load(data_dir) == manifest

    requests = nvidia_api.to(EMBED_URL)
    assert len(requests) == -(-manifest.chunk_count // ingest.EMBED_BATCH)
    assert all(r.body["input_type"] == "passage" and r.body["truncate"] == "END" for r in requests)
    assert all(r.body["model"] == "nvidia/nemotron-3-embed-1b" for r in requests)

    client = MilvusClient(uri=settings.milvus_uri)
    assert store.alias_target(client, "test_pack") == manifest.physical_collection
    chunk = client.get(manifest.physical_collection, ids=["edgar:0:0001"])[0]
    assert chunk["form"] == "8-K" and chunk["url"].startswith("https://www.sec.gov/")
    client.close()


def test_ingest_outlasts_a_burst_of_transient_failures(
    settings: Settings, data_dir: Path, nvidia_api: FakeNvidia, monkeypatch: pytest.MonkeyPatch
):
    # The bulk retry policy, minus its waits: eight attempts per request.
    monkeypatch.setattr(ingest, "_embed_passages", ingest._embed_passages.retry_with(wait=wait_none()))
    nvidia_api.fail(requests.ConnectionError("connection reset"), 408, 429, 500, 502, 503, 504)

    manifest = ingest.run(settings, data_dir)

    assert len(nvidia_api.to(EMBED_URL)) == 7 + -(-manifest.chunk_count // ingest.EMBED_BATCH)


def test_unchanged_corpus_is_not_reindexed(settings: Settings, data_dir: Path, nvidia_api: FakeNvidia):
    first = ingest.run(settings, data_dir)
    nvidia_api.calls.clear()

    assert ingest.run(settings, data_dir) == first
    assert nvidia_api.calls == []


def test_changed_corpus_swaps_the_alias_and_drops_the_old_build(settings: Settings, data_dir: Path):
    first = ingest.run(settings, data_dir)
    write_pack(data_dir, documents()[:4])

    second = ingest.run(settings, data_dir)

    assert second.physical_collection != first.physical_collection
    assert second.chunk_count == 4
    client = MilvusClient(uri=settings.milvus_uri)
    assert store.alias_target(client, "test_pack") == second.physical_collection
    assert client.list_collections() == [second.physical_collection]
    client.close()


def test_documents_of_sources_the_pack_does_not_declare_are_rejected(settings: Settings, data_dir: Path):
    write_pack(data_dir, [{**documents()[0], "source_id": "market_structured"}])

    with pytest.raises(ValueError, match="market_structured"):
        ingest.run(settings, data_dir)


def test_duplicate_document_ids_are_rejected_before_anything_is_embedded(
    settings: Settings, data_dir: Path, nvidia_api: FakeNvidia
):
    write_pack(data_dir, [*documents(), documents()[0]])

    with pytest.raises(ValueError, match="repeats document_id 'edgar:0'"):
        ingest.run(settings, data_dir)
    assert nvidia_api.calls == []


def test_an_interrupted_build_resumes_without_embedding_finished_chunks(
    settings: Settings, data_dir: Path, nvidia_api: FakeNvidia, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(ingest, "EMBED_BATCH", 10)
    # An id with an apostrophe in the first batch, which the resumed build must find.
    write_pack(data_dir, [{**documents()[0], "document_id": "edgar:o'neil"}, *documents()])
    embed = ingest._embed_passages
    batches = []

    def fail_on_the_third_batch(embedder, texts):
        batches.append(texts)
        if len(batches) == 3:
            raise RuntimeError("[400] Bad Request")  # not transient, so the build stops
        return embed(embedder, texts)

    monkeypatch.setattr(ingest, "_embed_passages", fail_on_the_third_batch)
    with pytest.raises(RuntimeError, match="400"):
        ingest.run(settings, data_dir)
    client = MilvusClient(uri=settings.milvus_uri)
    assert store.alias_target(client, "test_pack") is None  # nothing serves the unfinished build
    (unfinished,) = client.list_collections()
    client.close()

    monkeypatch.setattr(ingest, "_embed_passages", embed)
    nvidia_api.calls.clear()
    manifest = ingest.run(settings, data_dir)

    assert manifest.physical_collection == unfinished
    embedded = [text for call in nvidia_api.to(EMBED_URL) for text in call.body["input"]]
    assert len(embedded) == manifest.chunk_count - 20  # the first two batches were in already
    client = MilvusClient(uri=settings.milvus_uri)
    stored = client.query(unfinished, filter='chunk_id != ""', output_fields=["chunk_id"], limit=1000)
    assert len(stored) == len({row["chunk_id"] for row in stored}) == manifest.chunk_count
    assert store.alias_target(client, "test_pack") == unfinished
    client.close()


def test_the_alias_is_the_packs_documents_collection(settings: Settings, data_dir: Path):
    pack = json.loads((data_dir / "pack.json").read_text())
    (data_dir / "pack.json").write_text(json.dumps({**pack, "documents": {"collection": "market_evidence"}}))

    assert ingest.run(settings, data_dir).collection == "market_evidence"
