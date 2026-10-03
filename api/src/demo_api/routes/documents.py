# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The documents routes, forwarded to the ingest service (``INGEST_URL``) as they are.

They follow AI-Q's documents contract, which the UI's upload feature speaks:

| Route | Ingest's answer |
|---|---|
| ``GET``/``POST /v1/collections`` | the collections; create one |
| ``GET``/``DELETE /v1/collections/{name}`` | one collection; delete it |
| ``POST /v1/collections/{name}/documents`` | multipart ``files`` -> ``{job_id, file_ids, message}`` |
| ``GET /v1/collections/{name}/documents`` | ``{files: [...]}`` |
| ``DELETE /v1/collections/{name}/documents`` | JSON ``{file_ids}`` |
| ``GET /v1/documents/{job_id}/status`` | the ingestion job's status |

The request body is passed on as it arrives, never gathered in memory, and is cut off past
``INGEST_MAX_REQUEST_MB`` (413). Ingest's status code, body and content type come back unchanged; an
unreachable ingest is 503, a slow one 504.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import APIRouter
from fastapi import Path
from fastapi import Request
from fastapi import Response
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from demo_api.services import Services
from demo_api.services import ServicesDep

router = APIRouter(prefix="/v1", tags=["documents"])

# Uploads can be large: allow minutes between body chunks and for ingest's answer once the body is sent
TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=120.0, pool=5.0)
MIB = 1024 * 1024
_NAME = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
Name = Annotated[str, Path(pattern=_NAME)]
# Request headers ingest needs; response headers the caller gets back
_REQUEST_HEADERS = ("content-type", "content-length", "accept")


class _TooLargeError(Exception):
    pass


@router.get("/collections")
async def list_collections(request: Request, services: ServicesDep) -> Response:
    return await _forward(request, services, "/v1/collections")


@router.post("/collections")
async def create_collection(request: Request, services: ServicesDep) -> Response:
    return await _forward(request, services, "/v1/collections")


@router.get("/collections/{name}")
async def get_collection(name: Name, request: Request, services: ServicesDep) -> Response:
    return await _forward(request, services, f"/v1/collections/{name}")


@router.delete("/collections/{name}")
async def delete_collection(name: Name, request: Request, services: ServicesDep) -> Response:
    return await _forward(request, services, f"/v1/collections/{name}")


@router.get("/collections/{name}/documents")
async def list_documents(name: Name, request: Request, services: ServicesDep) -> Response:
    return await _forward(request, services, f"/v1/collections/{name}/documents")


@router.post("/collections/{name}/documents")
async def upload_documents(name: Name, request: Request, services: ServicesDep) -> Response:
    """Multipart ``files``, streamed to ingest."""
    return await _forward(request, services, f"/v1/collections/{name}/documents")


@router.delete("/collections/{name}/documents")
async def delete_documents(name: Name, request: Request, services: ServicesDep) -> Response:
    """JSON ``{file_ids}``."""
    return await _forward(request, services, f"/v1/collections/{name}/documents")


@router.get("/documents/{job_id}/status")
async def job_status(job_id: Name, request: Request, services: ServicesDep) -> Response:
    return await _forward(request, services, f"/v1/documents/{job_id}/status")


async def _forward(request: Request, services: Services, path: str) -> Response:
    limit = services.settings.ingest_max_request_mb * MIB
    too_large = JSONResponse(
        {"detail": f"The upload is larger than {services.settings.ingest_max_request_mb} MB."}, status_code=413
    )
    headers = {key: value for key in _REQUEST_HEADERS if (value := request.headers.get(key)) is not None}
    length = headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > limit):
        return too_large
    has_body = length not in (None, "0") or "chunked" in request.headers.get("transfer-encoding", "")
    upstream = services.ingest.build_request(
        request.method,
        path,
        params=list(request.query_params.multi_items()),
        headers=headers,
        content=_capped(request.stream(), limit) if has_body else None,
    )
    try:
        response = await services.ingest.send(upstream)
    except _TooLargeError:
        return too_large
    except ClientDisconnect:
        return JSONResponse({"detail": "The upload was interrupted."}, status_code=400)
    except httpx.TimeoutException:
        return JSONResponse({"detail": "The ingest service did not answer in time."}, status_code=504)
    except httpx.TransportError:
        return JSONResponse({"detail": "The ingest service is unavailable."}, status_code=503)
    return Response(response.content, status_code=response.status_code, media_type=response.headers.get("content-type"))


async def _capped(body: AsyncIterator[bytes], limit: int) -> AsyncIterator[bytes]:
    """``body`` chunk by chunk, failing once more than ``limit`` bytes have come."""
    received = 0
    async for chunk in body:
        received += len(chunk)
        if received > limit:
            raise _TooLargeError
        if chunk:
            yield chunk
