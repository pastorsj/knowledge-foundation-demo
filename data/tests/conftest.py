# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from demo_data.pack import DATA_ROOT
from demo_data.pack import Pack
from demo_data.pack import load_pack

PACKS = DATA_ROOT / "packs"
FIXTURES = Path(__file__).parent / "fixtures"
# Made-up per-symbol minute bars and a stub SEC snapshot (fixtures/make_minute_bars_fixture.py); no real data.
MINUTE_BARS = FIXTURES / "external" / "minute-bars"


@pytest.fixture(scope="session")
def synthetic_pack() -> Pack:
    return load_pack(PACKS / "synthetic-market")


@pytest.fixture(scope="session")
def contract() -> dict[str, Any]:
    """The market-analytics/v1 contract as the data-pack design describes it (a local copy; tools own the real one)."""
    return json.loads((FIXTURES / "contracts" / "market-analytics.v1.json").read_text())


@pytest.fixture
def pack_copy(tmp_path: Path):
    """A function that copies a pack into tmp_path/packs and returns the copy's directory."""

    def copy(name: str = "synthetic-market", source: Path = PACKS) -> Path:
        target = tmp_path / "packs" / name
        shutil.copytree(source / name, target, ignore=shutil.ignore_patterns("__pycache__"))
        return target

    return copy


def edit_yaml(path: Path, edit) -> None:
    document = yaml.safe_load(path.read_text())
    edit(document)
    path.write_text(yaml.safe_dump(document, sort_keys=False))


@pytest.fixture
def equities(tmp_path: Path, pack_copy) -> Path:
    """The packs directory holding us-equities pinned to the fixture bars, with the stub SEC snapshot cached.

    The data directory is tmp_path/data and the sources directory tmp_path/sources (empty until a fetch).
    """
    pack = pack_copy("us-equities")
    manifest = json.loads((MINUTE_BARS / "benchmark-bundle-manifest.json").read_text())

    def pin(document: dict[str, Any]) -> None:
        document["external"]["minute-bars"]["fingerprint"] = manifest["dataset_fingerprint"]
        document["external"]["minute-bars"]["bytes"] = manifest["total_bytes"]
        document["market"]["min_sessions"] = 2
        document["prediction"]["anchor"] = "2026-01-02T21:00:00+00:00"

    edit_yaml(pack / "pack.yaml", pin)
    shutil.copytree(FIXTURES / "sec", tmp_path / "data" / "cache" / "sec" / "2026-01-01")
    return pack.parent
