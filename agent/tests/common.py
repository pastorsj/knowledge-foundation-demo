# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Paths and helpers shared by the agent's offline checks."""

import re
from pathlib import Path

import yaml

AGENT = Path(__file__).resolve().parents[1]
ROOT = AGENT.parent
OPENSHELL = ROOT / "infra" / "openshell"
SKILL_DIRS = sorted(path.parent for path in (AGENT / "profile" / "skills").glob("*/SKILL.md"))
MCP_TOOL = re.compile(r"\bmcp__([a-z0-9_]+)__([a-z0-9_]+)\b")


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def exposed_tools(config: dict) -> set[str]:
    """Hermes names (mcp__<server>__<tool>) of every MCP tool the config lets the agent see."""
    return {
        f"mcp__{server}__{tool}" for server, spec in config["mcp_servers"].items() for tool in spec["tools"]["include"]
    }
