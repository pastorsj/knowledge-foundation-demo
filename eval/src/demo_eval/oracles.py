# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Oracle answers, computed from the deployment's own build through its read-only query route.

Each oracle is a pack's `eval/oracles/<name>.sql`, run as `SELECT * FROM (<sql>) <order> LIMIT <limit>` through
`POST /v1/data_sources/<structured source>/query` (at most 100 rows, read-only DuckDB). Nothing is computed here,
so the reference values always come from the same build the agent's tools read.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .client import Deployment
from .client import HttpError
from .spec import Oracle
from .spec import QuestionSpec

ASSET_FIELDS = ("asset_id", "first_asset", "second_asset")


class OracleError(ValueError):
    """The deployment refused an oracle's query: the SQL does not fit its build."""


SAFE_ID = re.compile(r"^[A-Za-z0-9._:/-]{1,64}$")


def structured_source(deployment: Deployment) -> str:
    """The id of the pack's structured source (its DuckDB), which the oracles query."""
    for source in deployment.data_sources():
        if source.get("kind") == "structured":
            return str(source["id"])
    raise LookupError("the deployment offers no structured data source to compute the oracles from")


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


def asset_ids(oracles: dict[str, list[dict[str, Any]]], runs: Iterable[dict[str, Any]] = ()) -> list[str]:
    """The asset ids the checks may look for: in the oracle rows, and the assets the runs' predictions ranked."""
    found: dict[str, None] = {}
    for rows in oracles.values():
        for row in rows:
            for field in ASSET_FIELDS:
                if isinstance(row.get(field), str):
                    found[row[field]] = None
    for run in runs:
        for receipt in (run.get("turn") or {}).get("receipts", []):
            if receipt.get("artifactKind") == "structured_prediction":
                for row in (receipt.get("content") or {}).get("rows", []):
                    if isinstance(row.get("assetId"), str):
                        found[row["assetId"]] = None
    return [asset for asset in found if SAFE_ID.match(asset)]


def company_names(deployment: Deployment, source_id: str, template: str, ids: list[str]) -> dict[str, str]:
    """asset id -> company name, from the answers.yaml `names` query (its `{ids}` is a quoted id list)."""
    names: dict[str, str] = {}
    if not template or not ids:
        return names
    for start in range(0, len(ids), 90):
        quoted = ", ".join("'" + asset.replace("'", "''") + "'" for asset in ids[start : start + 90])
        for row in deployment.query(source_id, template.replace("{ids}", quoted)):
            if row.get("asset_id") and row.get("company_name"):
                names[str(row["asset_id"])] = str(row["company_name"])
    return names
