# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]


def _submodule_ready() -> bool:
    """True when the superproject records the submodule and it is checked out (it is private)."""
    command = ["git", "-C", str(ROOT), "rev-parse", "--verify", "--quiet", ":vendor/auto-ontology"]
    recorded = subprocess.run(command, capture_output=True, check=False).returncode == 0
    return recorded and (ROOT / "vendor" / "auto-ontology" / ".git").exists()


@pytest.mark.skipif(not _submodule_ready(), reason="needs the private vendor/auto-ontology submodule")
def test_patches_apply_to_the_pinned_commit() -> None:
    result = subprocess.run([HERE / "prepare.sh", "--check"], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    assert "All patches apply" in result.stdout
