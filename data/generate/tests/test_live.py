# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""A real run of the ci profile (12 issuers, about 60 calls) against the configured endpoint and SEC."""

from __future__ import annotations

import json
import os

import pytest

from data_generate import cli

pytestmark = pytest.mark.live


def designer_key_set() -> bool:
    try:
        return bool(cli.designer_key(os.environ))
    except SystemExit:
        return False


@pytest.mark.skipif(
    not designer_key_set() or not os.environ.get("SEC_USER_AGENT"),
    reason="needs a key for the Data Designer endpoint (cli.designer_key) and SEC_USER_AGENT",
)
def test_the_ci_profile_generates(tmp_path):
    assert cli.main(["--profile", "ci", "--out", str(tmp_path), "--fresh"]) == 0
    checks = json.loads((tmp_path / "checks.json").read_text())
    assert checks["counts"]["companies"] == 12 and checks["counts"]["stories"] == 12
    assert checks["usage"]["calls"] >= 12 + 30 + 12
    assert not (tmp_path / ".work").exists()
