# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The documents routes (AI-Q's documents contract), forwarded to the ingest service as they are."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from demo_api.app import create_app
from demo_api.settings import Settings

MB = 1024 * 1024


class UnreadTransport(httpx.AsyncBaseTransport):
    """``httpx.MockTransport`` without its ``request.aread()``: a handler gets the body as it streams, as a server
    would, so a test can see the chunks it arrives in."""

    def __init__(self, upstreams: dict[str, Callable[[httpx.Request], Any]]) -> None:
        self.upstreams = upstreams

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = self.upstreams[request.url.host](request)
        return await response if asyncio.iscoroutine(response) else response


@pytest.fixture
async def app(settings: Settings, upstreams: dict[str, Callable[[httpx.Request], Any]]) -> AsyncIterator[FastAPI]:
    app = create_app(settings, transport=UnreadTransport(upstreams))
    async with app.router.lifespan_context(app):
        yield app


class FakeIngest:
    """Records each forwarded request, the chunks its body arrived in, and answers with ``reply``."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.reply = httpx.Response(200, json={"ok": True})

    async def handle(self, request: httpx.Request) -> httpx.Response:
        chunks = [chunk async for chunk in request.stream]
        self.requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": request.headers,
                "chunks": chunks,
                "body": b"".join(chunks),
            }
        )
        return self.reply


@pytest.fixture
def ingest(upstreams) -> FakeIngest:
    fake = FakeIngest()
    upstreams["ingest.test"] = fake.handle
    return fake


def multipart(*files: tuple[str, bytes, str]) -> tuple[bytes, str]:
    """A ``multipart/form-data`` body with one ``files`` part per file, and its content type."""
    boundary = "kf-test-boundary"
    parts = [
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()
        + content
        + b"\r\n"
        for name, content, content_type in files
    ]
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


async def chunks_of(body: bytes, size: int) -> AsyncIterator[bytes]:
    for start in range(0, len(body), size):
        yield body[start : start + size]


async def test_an_upload_is_streamed_to_ingest_and_its_answer_comes_back_as_is(api, ingest):
    body, content_type = multipart(
        ("policy.pdf", b"%PDF-1.7\n" + b"x" * 300_000, "application/pdf"),
        ("orders.csv", b"order_id,net_amount\nO1,1.5\n", "text/csv"),
    )
    accepted = {"job_id": "job-7", "file_ids": ["f-0123456789abcdef", "f-fedcba9876543210"], "message": "Accepted"}
    ingest.reply = httpx.Response(202, json=accepted)

    response = await api.post(
        "/v1/collections/workspace/documents",
        content=chunks_of(body, 64 * 1024),
        headers={"content-type": content_type},
    )

    assert (response.status_code, response.json()) == (202, accepted)
    assert response.headers["content-type"] == "application/json"
    (forwarded,) = ingest.requests
    assert (forwarded["method"], forwarded["url"]) == ("POST", "http://ingest.test/v1/collections/workspace/documents")
    assert forwarded["headers"]["content-type"] == content_type
    assert forwarded["body"] == body
    assert len(forwarded["chunks"]) > 1  # passed on as it arrived, never gathered in memory


async def test_an_upload_with_a_content_length_keeps_it(api, ingest):
    body, content_type = multipart(("notes.md", b"# Notes\n", "text/markdown"))

    await api.post("/v1/collections/workspace/documents", content=body, headers={"content-type": content_type})

    assert ingest.requests[0]["headers"]["content-length"] == str(len(body))
    assert ingest.requests[0]["body"] == body


async def test_an_upload_over_the_request_cap_is_413_before_ingest_sees_it(api, ingest, settings):
    settings.ingest_max_request_mb = 1
    body, content_type = multipart(("big.csv", b"a" * MB, "text/csv"))

    response = await api.post(
        "/v1/collections/workspace/documents", content=body, headers={"content-type": content_type}
    )

    assert response.status_code == 413
    assert "1 MB" in response.json()["detail"]
    assert ingest.requests == []


async def test_a_streamed_upload_is_cut_off_at_the_request_cap(api, ingest, settings):
    settings.ingest_max_request_mb = 1
    body, content_type = multipart(("big.csv", b"a" * (2 * MB), "text/csv"))

    response = await api.post(
        "/v1/collections/workspace/documents",
        content=chunks_of(body, 256 * 1024),
        headers={"content-type": content_type},
    )

    assert response.status_code == 413
    assert ingest.requests == []  # the fake ingest never received a whole request


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/v1/collections", None),
        ("POST", "/v1/collections", {"name": "research", "description": "Team files"}),
        ("GET", "/v1/collections/workspace", None),
        ("DELETE", "/v1/collections/research", None),
        ("GET", "/v1/collections/workspace/documents", None),
        ("DELETE", "/v1/collections/workspace/documents", {"file_ids": ["f-0123456789abcdef"]}),
        ("GET", "/v1/documents/job-7/status", None),
    ],
)
async def test_each_documents_route_reaches_the_same_ingest_route(api, ingest, method, path, body):
    answer = {"route": path}
    ingest.reply = httpx.Response(200, json=answer)

    content = json.dumps(body).encode() if body is not None else None
    headers = {"content-type": "application/json"} if body is not None else {}
    response = await api.request(method, path, content=content, headers=headers)

    assert (response.status_code, response.json()) == (200, answer)
    (forwarded,) = ingest.requests
    assert (forwarded["method"], forwarded["url"]) == (method, f"http://ingest.test{path}")
    if body is not None:
        assert json.loads(forwarded["body"]) == body
        assert forwarded["headers"]["content-type"] == "application/json"
    else:
        assert forwarded["body"] == b""


async def test_query_parameters_are_forwarded(api, ingest):
    await api.get("/v1/collections/workspace/documents", params={"limit": "5", "status": "ready"})

    assert ingest.requests[0]["url"] == "http://ingest.test/v1/collections/workspace/documents?limit=5&status=ready"


@pytest.mark.parametrize("status", [404, 409, 422, 500])
async def test_an_ingest_error_comes_back_with_its_status_and_body(api, ingest, status):
    ingest.reply = httpx.Response(status, json={"detail": f"ingest says {status}"})

    response = await api.get("/v1/documents/job-404/status")

    assert (response.status_code, response.json()) == (status, {"detail": f"ingest says {status}"})


async def test_an_unreachable_ingest_is_503(api, upstreams):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    upstreams["ingest.test"] = down

    response = await api.get("/v1/collections")
    assert response.status_code == 503
    assert response.json()["detail"] == "The ingest service is unavailable."


async def test_a_slow_ingest_is_504(api, upstreams):
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    upstreams["ingest.test"] = slow

    assert (await api.get("/v1/documents/job-7/status")).status_code == 504


@pytest.mark.parametrize(
    "path", ["/v1/collections/bad%20name/documents", "/v1/collections/..", "/v1/documents/a%2Fb/status"]
)
async def test_malformed_names_never_reach_ingest(api, ingest, path):
    assert (await api.get(path)).status_code in {404, 422}
    assert ingest.requests == []
