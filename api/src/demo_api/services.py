# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The objects routes share, created once per process by the app's lifespan."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request

from .catalog import CatalogSnapshot
from .catalog import CatalogUnavailableError
from .catalog import KnowledgeCatalog
from .jobs.runner import JobRunner
from .jobs.store import JobStore
from .registry import ToolRegistry
from .settings import Settings
from .speech import SpeechService


@dataclass
class Services:
    settings: Settings
    registry: ToolRegistry
    catalog: KnowledgeCatalog
    store: JobStore
    runner: JobRunner
    http: httpx.AsyncClient  # outgoing calls to Phoenix
    ingest: httpx.AsyncClient  # the documents routes, forwarded to INGEST_URL
    transport: httpx.AsyncBaseTransport | None  # for clients made per request (Auto Ontology); tests fake it
    speech: SpeechService  # voice input; off unless configured


def get_services(request: Request) -> Services:
    return request.app.state.services


async def read_catalog(services: Services) -> CatalogSnapshot:
    """The knowledge catalog, read once for this request off the event loop; 503 before ingest has written it."""
    try:
        return await asyncio.to_thread(services.catalog.read)
    except CatalogUnavailableError as error:
        raise HTTPException(503, str(error)) from error


ServicesDep = Annotated[Services, Depends(get_services)]
