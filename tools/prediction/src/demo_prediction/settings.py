# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The environment contract. Nothing else in the package reads configuration from the environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

PORT = 8322
API_KEY_SECRET = Path("/run/secrets/kumo_api_key")


@dataclass(frozen=True)
class Settings:
    knowledge_dir: Path = Path("/knowledge")
    port: int = PORT
    kumo_url: str | None = None  # None: predict is still registered and answers available=false
    kumo_api_key: str | None = field(default=None, repr=False)  # only for a gateway in front of a hosted NIM

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        # Compose renders unset variables as "", so empty means "use the default".
        api_key = env.get("KUMO_API_KEY") or (API_KEY_SECRET.read_text().strip() if API_KEY_SECRET.exists() else "")
        return cls(
            knowledge_dir=Path(env.get("KNOWLEDGE_DIR") or "/knowledge"),
            port=int(env.get("PREDICTION_PORT") or PORT),
            kumo_url=env.get("KUMO_RELATIONAL_URL") or None,
            kumo_api_key=api_key or None,
        )
