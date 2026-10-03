# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The packs a user picks from (the industries and the workspace), and one pack's questions.

- ``GET /v1/packs``: ``PackList``, the industries by title, then the workspace.
- ``GET /v1/pack?id=``: ``PackView`` of one pack, by default the first industry: its questions whose sources and
  tools this stack serves, the composer picker's examples among them, and the conversations ``demo-api record``
  asks.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query

from demo_api.catalog import PACK_ID
from demo_api.catalog import CatalogUnavailableError
from demo_api.catalog import PackNotFoundError
from demo_api.pack import PackList
from demo_api.pack import PackView
from demo_api.services import ServicesDep

router = APIRouter(prefix="/v1", tags=["packs"])


@router.get("/packs")
async def packs(services: ServicesDep) -> PackList:
    try:
        return PackList(packs=services.catalog.packs())
    except CatalogUnavailableError as error:
        raise HTTPException(503, str(error)) from error


@router.get("/pack")
async def pack(
    services: ServicesDep,
    pack_id: Annotated[str | None, Query(alias="id", pattern=PACK_ID, max_length=64)] = None,
) -> PackView:
    try:
        return services.catalog.pack_view(pack_id)
    except CatalogUnavailableError as error:
        raise HTTPException(503, str(error)) from error
    except PackNotFoundError as error:
        raise HTTPException(404, str(error)) from error
