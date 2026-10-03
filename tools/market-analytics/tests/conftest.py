# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest
from fixture_pack import write_pack

from market_analytics.data import MarketData
from market_analytics.data import Pack


@pytest.fixture(scope="session")
def pack_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return write_pack(tmp_path_factory.mktemp("active"))


@pytest.fixture(scope="session")
def pack(pack_root: Path) -> Pack:
    return Pack.load(pack_root)


@pytest.fixture(scope="session")
def data(pack: Pack) -> MarketData:
    return MarketData.load(pack)


@pytest.fixture(scope="session")
def daily_only_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The same pack with neither ticker-linked news nor minute bars."""
    return write_pack(tmp_path_factory.mktemp("daily_only"), daily_only=True)


@pytest.fixture(scope="session")
def daily_only(daily_only_root: Path) -> MarketData:
    return MarketData.load(Pack.load(daily_only_root))
