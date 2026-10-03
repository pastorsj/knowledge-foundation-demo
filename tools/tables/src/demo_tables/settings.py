# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The environment contract. Nothing else in the package reads configuration from the environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

PORT = 8321
TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class Settings:
    knowledge_dir: Path = Path("/knowledge")
    port: int = PORT
    timeout_seconds: float = TIMEOUT_SECONDS  # a query still running then is killed with its worker process

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        # Compose renders unset variables as "", so empty means "use the default".
        return cls(
            knowledge_dir=Path(env.get("KNOWLEDGE_DIR") or "/knowledge"),
            port=int(env.get("TABLES_PORT") or PORT),
        )
