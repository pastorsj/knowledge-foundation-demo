# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The technology pills of a recorded session: what its runs actually used, for the UI's replays list.

Each completed registered tool call (an ``artifact.available`` event) contributes its tool's registry ``pills``, in
the registry's pill order (``Pill`` in ``contracts/tool-registry.schema.json``), as one pill per kind across the
session's turns. Hermes's own tools (skills, memory, terminal and the rest) have no pill.
"""

from __future__ import annotations

from typing import Any

from .registry import ToolRegistry

PILL_ORDER = ("retrieval", "duckdb", "kumo", "ontology")


def session_pills(turns: list[dict[str, Any]], registry: ToolRegistry) -> list[dict[str, Any]]:
    """``[{pill, tools}]``: each pill once, with the tool ids that brought it."""
    found: dict[str, list[str]] = {}
    for turn in turns:
        for event in turn.get("events", []):
            tool = registry.by_id(event.get("toolName"))
            if event.get("eventKind") != "artifact.available" or tool is None:
                continue
            for pill in tool.pills:
                tools = found.setdefault(pill, [])
                if tool.id not in tools:
                    tools.append(tool.id)
    return [{"pill": pill, "tools": found[pill]} for pill in sorted(found, key=PILL_ORDER.index)]
