# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Smoke test against the real endpoints: RETRIEVER_API_KEY=nvapi-... uv run pytest -m live

Never runs in CI (it is deselected by default). Indexes 20 short documents into Milvus Lite.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import write_pack
from pymilvus import MilvusClient

from demo_retrieval import ingest
from demo_retrieval.search import Retriever
from demo_retrieval.settings import Settings

pytestmark = [
    pytest.mark.live,
    pytest.mark.anyio,
    pytest.mark.skipif(not os.environ.get("RETRIEVER_API_KEY"), reason="needs RETRIEVER_API_KEY"),
]

NEWS = {
    "cyber": "Harbor Logistics determined that a ransomware attack on its payment systems is a material "
    "cybersecurity incident. Operations were disrupted for three days and customer data may have been accessed.",
    "earnings": "Ember Foods reported third-quarter revenue up 8 percent and raised full-year guidance.",
    "merger": "Delta Robotics entered into a definitive merger agreement to be acquired for $42 per share in cash.",
    "ceo": "Kestrel Energy's chief executive officer resigned; the board named an interim CEO.",
    "buyback": "Aether Semiconductors' board authorized a $500 million share repurchase program.",
    "credit": "Fathom Marine entered into a $750 million senior unsecured revolving credit facility maturing in 2031.",
    "recall": "Juniper Health recalled two infusion pump models after reports of battery failures.",
    "dividend": "Galena Mining raised its quarterly cash dividend by 10 percent to $0.33 per share.",
    "auditor": "Ion Retail dismissed its independent registered public accounting firm and engaged a new auditor.",
    "bankruptcy": "Meridian Airlines filed voluntary petitions for relief under Chapter 11 of the Bankruptcy Code.",
}
REGULATIONS = {
    "cyber": "Item 1.05 of Form 8-K: a registrant that experiences a cybersecurity incident it determines to be "
    "material must describe its nature, scope, timing and material impact within four business days of that "
    "determination.",
    "fd": "Regulation FD: when an issuer discloses material nonpublic information to certain persons, it must make "
    "public disclosure of that information simultaneously for intentional disclosures.",
    "fraud": "Rule 10b-5 makes it unlawful to make any untrue statement of a material fact in connection with the "
    "purchase or sale of any security.",
    "current": "Rule 13a-11: every registrant shall file a current report on Form 8-K within the period specified.",
    "officers": "Item 5.02 of Form 8-K requires disclosure when a principal executive officer retires, resigns or is "
    "terminated.",
    "mdna": "Item 303 of Regulation S-K requires management's discussion and analysis of financial condition and "
    "results of operations.",
    "plans": "Rule 10b5-1 provides an affirmative defense for trades made under a written plan adopted in good faith.",
    "accountant": "Item 4.01 of Form 8-K requires disclosure when a registrant's independent accountant resigns or "
    "is dismissed.",
    "proposals": "Rule 14a-8 governs when a company must include a shareholder proposal in its proxy statement.",
    "acquisition": "Item 2.01 of Form 8-K requires disclosure of the completion of an acquisition or disposition "
    "of assets.",
}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.from_env({**os.environ, "MILVUS_URI": str(tmp_path / "milvus.db")})


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    rows = [
        {"document_id": f"news:{key}", "source_id": "market_news", "title": f"8-K {key}", "text": text}
        for key, text in NEWS.items()
    ] + [
        {"document_id": f"reg:{key}", "source_id": "market_regulations", "title": f"17 CFR {key}", "text": text}
        for key, text in REGULATIONS.items()
    ]
    write_pack(tmp_path / "active", rows)
    return tmp_path / "active"


async def test_index_and_search_with_the_real_models(settings: Settings, data_dir: Path):
    manifest = ingest.run(settings, data_dir)
    client = MilvusClient(uri=settings.milvus_uri)
    fields = client.describe_collection(manifest.physical_collection)["fields"]
    client.close()
    assert next(f for f in fields if f["name"] == "embedding")["params"]["dim"] == 2048

    retriever = Retriever(settings, manifest.collection)
    try:
        result = await retriever.retrieve(
            "What must a company disclose after a material cybersecurity incident, and how quickly?",
            ["market_news", "market_regulations"],
            top_k=5,
        )
    finally:
        await retriever.close()

    assert result.collection_version == manifest.physical_collection
    assert result.candidate_counts == {"market_news": 10, "market_regulations": 10}
    assert {hit.document_id for hit in result.hits[:2]} == {"reg:cyber", "news:cyber"}
    assert result.hits[0].score > result.hits[-1].score
