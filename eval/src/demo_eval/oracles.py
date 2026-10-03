# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Oracle answers, computed from the deployment's own build through its read-only query route.

Each oracle is a pack's `eval/oracles/<name>.sql`, run as `SELECT * FROM (<sql>) <order> LIMIT <limit>` through
`POST /v1/data_sources/<structured source>/query` (at most 100 rows, read-only DuckDB). Nothing is computed here,
so the reference values always come from the same data the agent's tools read.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .client import Deployment
from .client import HttpError
from .spec import Oracle
from .spec import QuestionSpec
from .spec import catalog_id


class OracleError(ValueError):
    """The oracles cannot be computed: no source to query, or the deployment refused a query's SQL."""


def structured_source(deployment: Deployment, pack_id: str, wanted: str = "") -> str:
    """The catalog id of the pack's structured source (its DuckDB), which the oracles query.

    ``wanted`` is the pack-local id `answers.yaml` names; without it the pack must have exactly one.
    """
    structured = [str(s["id"]) for s in deployment.data_sources(pack_id) if s.get("kind") == "structured"]
    if wanted:
        if (chosen := catalog_id(pack_id, wanted)) not in structured:
            raise OracleError(f"answers.yaml names source {wanted}, which is not a structured source of {pack_id}")
        return chosen
    if len(structured) != 1:
        found = f"it has {', '.join(structured)}" if structured else "it has none"
        raise OracleError(f"pack {pack_id} needs one structured source to compute the oracles from; {found}")
    return structured[0]


def oracle_sql(pack_dir: Path, oracle: Oracle) -> str:
    sql = (pack_dir / "eval" / "oracles" / f"{oracle.sql}.sql").read_text().strip().rstrip(";")
    order = f" {oracle.order}" if oracle.order else ""
    return f"SELECT * FROM (\n{sql}\n){order} LIMIT {oracle.limit}"


def compute(
    deployment: Deployment, source_id: str, pack_dir: Path, questions: Iterable[QuestionSpec]
) -> dict[str, list[dict[str, Any]]]:
    """Every oracle the questions use, by name."""
    results: dict[str, list[dict[str, Any]]] = {}
    for question in questions:
        for oracle in question.oracles:
            if oracle.name not in results:
                try:
                    results[oracle.name] = deployment.query(source_id, oracle_sql(pack_dir, oracle))
                except HttpError as error:
                    if error.status is None or error.status >= 500:
                        raise
                    raise OracleError(f"oracle {oracle.name} ({oracle.sql}.sql) failed: {error}") from None
    return results
