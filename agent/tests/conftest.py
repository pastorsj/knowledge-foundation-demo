# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from common import AGENT
from common import OPENSHELL
from common import load_yaml


@pytest.fixture(scope="session")
def config() -> dict:
    return load_yaml(AGENT / "profile" / "config.yaml")


@pytest.fixture(scope="session")
def policy() -> dict:
    return load_yaml(AGENT / "sandbox-policy.yaml")


@pytest.fixture(scope="session")
def providers() -> dict[str, dict]:
    return {path.stem: load_yaml(path) for path in (OPENSHELL / "providers").glob("*.yaml")}
