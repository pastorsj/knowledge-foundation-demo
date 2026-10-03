# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import API_KEY
from conftest import EMBED_URL
from conftest import RERANK_URL
from conftest import FakeNvidia
from conftest import documents
from conftest import write_pack
from langchain_core.documents import Document
from pydantic import ValidationError

from demo_retrieval import ingest
from demo_retrieval import nvidia
from demo_retrieval.datapack import CorpusDocument
from demo_retrieval.settings import Settings


def test_defaults_target_build_nvidia_com():
    settings = Settings.from_env({"RETRIEVER_API_KEY": API_KEY, "RETRIEVER_BASE_URL": "", "RETRIEVER_RERANK_URL": ""})

    assert settings.base_url == "https://integrate.api.nvidia.com/v1"
    assert settings.embed_model == "nvidia/nemotron-3-embed-1b"
    assert settings.rerank_model == "nvidia/llama-nemotron-rerank-vl-1b-v2"
    assert settings.rerank_url is None
    assert API_KEY not in repr(settings)


def test_the_api_key_is_required():
    with pytest.raises(ValueError, match="RETRIEVER_API_KEY"):
        Settings.from_env({"NVIDIA_API_KEY": "nvapi-global"})


def test_global_nvidia_variables_never_reach_the_clients(
    settings: Settings, nvidia_api: FakeNvidia, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-leaked")
    monkeypatch.setenv("NVIDIA_BASE_URL", "https://leaked.example.com/v1")

    nvidia.embedder(settings).embed_query("q")
    nvidia.reranker(settings).compress_documents([Document(page_content="p")], "q")

    assert [call.url for call in nvidia_api.calls] == [EMBED_URL, RERANK_URL]
    assert {call.headers["Authorization"] for call in nvidia_api.calls} == {f"Bearer {API_KEY}"}


def test_self_hosted_endpoints(settings: Settings, nvidia_api: FakeNvidia):
    settings = replace(settings, base_url="http://nim:8000/v1")
    # register_model is process-wide, so the custom URL gets its own model id to leave other tests untouched.
    custom = replace(
        settings, rerank_model="example/self-hosted-reranker", rerank_url="http://reranker:8000/v1/ranking"
    )

    nvidia.embedder(settings).embed_query("q")
    nvidia.reranker(settings).compress_documents([Document(page_content="p")], "q")
    nvidia.reranker(custom).compress_documents([Document(page_content="p")], "q")

    assert [call.url for call in nvidia_api.calls] == [
        "http://nim:8000/v1/embeddings",
        "http://nim:8000/v1/ranking",
        "http://reranker:8000/v1/ranking",
    ]


def test_usage_telemetry_is_off_by_default():
    env = {key: value for key, value in os.environ.items() if key != "NVIDIA_USAGE_TELEMETRY_ENABLED"}
    code = "import os, demo_retrieval; print(os.environ['NVIDIA_USAGE_TELEMETRY_ENABLED'])"

    output = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)

    assert output.stdout.strip() == "false"


@pytest.mark.parametrize(
    ("metadata", "error"),
    [({"image": "https://example.com/chart.png"}, '"image" is reserved'), ({"title": "x"}, "collide")],
    ids=["image", "schema-field"],
)
def test_reserved_metadata_keys_are_rejected(metadata: dict[str, str], error: str):
    with pytest.raises(ValidationError, match=error):
        CorpusDocument.model_validate({**documents()[0], "metadata": metadata})


def test_ingest_rejects_an_image_key_before_embedding(settings: Settings, data_dir: Path, nvidia_api: FakeNvidia):
    write_pack(data_dir, [{**documents()[0], "metadata": {"image": "chart.png"}}])

    with pytest.raises(ValidationError, match='"image" is reserved'):
        ingest.run(settings, data_dir)
    assert nvidia_api.calls == []
