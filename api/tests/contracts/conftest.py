# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path
from typing import Any

import pytest

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"


def load(relative_path: str) -> Any:
    return json.loads((CONTRACTS / relative_path).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def contracts_dir() -> Path:
    return CONTRACTS


@pytest.fixture(scope="session")
def registry() -> dict[str, Any]:
    return load("tool-registry.json")


@pytest.fixture(scope="session")
def registry_schema() -> dict[str, Any]:
    return load("tool-registry.schema.json")


@pytest.fixture(scope="session")
def tools(registry: dict[str, Any]) -> list[dict[str, Any]]:
    return registry["tools"]


@pytest.fixture(scope="session")
def events() -> list[dict[str, Any]]:
    return load("fixtures/execution-events.json")


@pytest.fixture(scope="session")
def receipts() -> list[dict[str, Any]]:
    return load("fixtures/receipts.json")
