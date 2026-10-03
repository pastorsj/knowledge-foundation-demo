# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Smoke test against the real endpoints: RETRIEVER_API_KEY=nvapi-... uv run pytest -m live

Never runs in CI (it is deselected by default). Embeds 16 short passages into Milvus Lite, as ingest would.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import MANUALS
from conftest import POLICIES
from conftest import chunk
from pymilvus import MilvusClient

from demo_retrieval import nvidia
from demo_retrieval import store
from demo_retrieval.search import Retriever
from demo_retrieval.settings import Settings

pytestmark = [
    pytest.mark.live,
    pytest.mark.anyio,
    pytest.mark.skipif(not os.environ.get("RETRIEVER_API_KEY"), reason="needs RETRIEVER_API_KEY"),
]

POLICY_TEXTS = {
    "returns": "Furniture returned within 30 days is refunded in full, less a 15 percent restocking fee unless the "
    "item arrived damaged.",
    "hours": "Stores open at 9:00 and close at 21:00; on public holidays they close at 17:00.",
    "suppliers": "Suppliers are paid 45 days after the end of the month in which the invoice was received.",
    "gift-cards": "Gift cards are not redeemable for cash and cannot be refunded once activated.",
    "price-match": "We match a local competitor's advertised price on an identical item in stock.",
    "discount": "Employees receive a 20 percent discount after 90 days of service.",
    "recall": "When a product is recalled, pull it from the shelves within two hours and notify the district office.",
    "cash": "Two employees count the cash drawer together at closing and sign the count sheet.",
}
MANUAL_TEXTS = {
    "returns": "At the till, scan the receipt, inspect the furniture, then apply the restocking fee in the POS.",
    "hours": "The opening shift disarms the alarm at 8:30 and unlocks the front doors at 9:00.",
    "suppliers": "Match each delivery note against the purchase order before signing for the goods.",
    "gift-cards": "Activate a gift card only after the customer has paid for it.",
    "price-match": "Ask for the competitor's advert and check that the item is identical before matching.",
    "discount": "Staff purchases are rung up by a supervisor, never by the employee themselves.",
    "recall": "Recalled stock goes to the quarantine cage with a red tag until the supplier collects it.",
    "cash": "Drop excess cash into the safe whenever the drawer holds more than 500 dollars.",
}


@pytest.fixture
def settings(tmp_path: Path, knowledge_dir: Path) -> Settings:
    return Settings.from_env(
        {**os.environ, "MILVUS_URI": str(tmp_path / "milvus.db"), "KNOWLEDGE_DIR": str(knowledge_dir)}
    )


@pytest.fixture
def indexed(settings: Settings) -> str:
    rows = [
        chunk(POLICIES, f"{POLICIES}:{key}.pdf", 1, f"Policy: {key}", text) for key, text in POLICY_TEXTS.items()
    ] + [chunk(MANUALS, f"{MANUALS}:{key}.docx", 1, f"Manual: {key}", text) for key, text in MANUAL_TEXTS.items()]
    vectors = nvidia.embedder(settings).embed_documents([row["text"] for row in rows])  # input_type=passage
    client = MilvusClient(uri=settings.milvus_uri)
    try:
        store.create_collection(client, "knowledge__live", dimension=len(vectors[0]))
        client.insert("knowledge__live", [{**row, store.VECTOR_FIELD: v} for row, v in zip(rows, vectors, strict=True)])
        client.create_alias("knowledge__live", store.ALIAS)
        fields = client.describe_collection("knowledge__live")["fields"]
    finally:
        client.close()
    assert next(f for f in fields if f["name"] == "embedding")["params"]["dim"] == 2048
    return "knowledge__live"


async def test_search_with_the_real_models(settings: Settings, indexed: str):
    retriever = Retriever(settings)
    try:
        result = await retriever.retrieve(
            "How much is the restocking fee on returned furniture, and how is it applied?", [MANUALS, POLICIES], top_k=4
        )
    finally:
        await retriever.close()

    assert result.collection_version == indexed
    assert result.candidate_counts == {MANUALS: 8, POLICIES: 8}
    assert {hit.document_id for hit in result.hits[:2]} == {f"{POLICIES}:returns.pdf", f"{MANUALS}:returns.docx"}
    assert result.hits[0].score > result.hits[-1].score
