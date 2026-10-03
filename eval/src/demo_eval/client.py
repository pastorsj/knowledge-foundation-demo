# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""JSON over HTTP with the standard library, and the deployment's public API.

A deployment is reached the way a browser reaches it: through the UI, which serves `/api/health` and proxies
`/api/v1/<path>` to the job API (`ui/src/app/api/v1/[...path]/route.ts` lists the routes). So the same URL works for
`http://127.0.0.1:3300` on the host, an SSH tunnel to it, or a link to a remote host's UI.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from typing import Any

# Transient answers worth another try: the queue is full (429) or something in the path restarted.
RETRY_STATUSES = frozenset({429, 502, 503, 504})


class HttpError(Exception):
    """A request failed: an HTTP status (``status``) or no answer at all (``status`` None)."""

    def __init__(self, url: str, status: int | None, detail: str) -> None:
        super().__init__(f"{_safe(url)}: {status or 'unreachable'} {detail}".strip())
        self.status = status
        self.detail = detail


def _safe(url: str) -> str:
    """The URL without a query string, which may hold a credential."""
    return urllib.parse.urlsplit(url)._replace(query="", fragment="").geturl()


def _retry_after(value: str | None) -> float:
    try:
        return max(0.0, float(value or 0))
    except ValueError:
        return 0.0


def request_json(
    method: str,
    url: str,
    body: Any = None,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
    attempts: int = 4,
    sleep: Callable[[float], None] | None = None,
) -> Any:
    """Send ``body`` as JSON and return the decoded answer. Retries transient failures with a growing pause."""
    data = None if body is None else json.dumps(body).encode()
    all_headers = {"accept": "application/json", **({"content-type": "application/json"} if data else {})}
    all_headers |= headers or {}
    error: HttpError | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, data, all_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as failure:
            detail = failure.read()[:500].decode(errors="replace")
            error = HttpError(url, failure.code, detail)
            if failure.code not in RETRY_STATUSES:
                raise error from None
            pause = _retry_after(failure.headers.get("Retry-After")) or 5.0 * (attempt + 1)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as failure:
            error = HttpError(url, None, str(getattr(failure, "reason", failure))[:200])
            pause = 5.0 * (attempt + 1)
        except json.JSONDecodeError as failure:
            raise HttpError(url, 200, f"not JSON: {failure}") from None
        if attempt + 1 < attempts:
            (sleep or time.sleep)(min(pause, 60.0))
    assert error is not None
    raise error


class Deployment:
    """The public API of a running deployment, at the UI's URL."""

    def __init__(self, url: str, *, request: Callable[..., Any] = request_json) -> None:
        self.url = url.rstrip("/")
        self._request = request

    def get(self, path: str, **options: Any) -> Any:
        return self._request("GET", f"{self.url}/api/{path.lstrip('/')}", **options)

    def post(self, path: str, body: Any, **options: Any) -> Any:
        return self._request("POST", f"{self.url}/api/{path.lstrip('/')}", body, **options)

    def health(self) -> dict[str, Any]:
        return self.get("health", attempts=2)

    def packs(self) -> list[dict[str, Any]]:
        """The packs a user can pick: the industries by title, then the workspace."""
        return self.get("v1/packs")["packs"]

    def pack(self, pack_id: str) -> dict[str, Any]:
        """One pack's view: its title, version and the questions this deployment serves."""
        return self.get(f"v1/pack?{urllib.parse.urlencode({'id': pack_id})}")

    def data_sources(self, pack_id: str) -> list[dict[str, Any]]:
        """The pack's sources, by catalog id (`<pack>.<source>`)."""
        return self.get(f"v1/data_sources?{urllib.parse.urlencode({'pack': pack_id})}")

    def query(self, source_id: str, sql: str) -> list[dict[str, Any]]:
        """Rows of a read-only query on a structured source (at most 100, the API's cap)."""
        result = self.post(f"v1/data_sources/{urllib.parse.quote(source_id)}/query", {"sql": sql}, timeout=300)
        return [dict(zip(result["columns"], row, strict=True)) for row in result["rows"]]

    def submit(self, question: str, sources: list[str], pack_id: str) -> str:
        """Submit one question as a fresh job (a new session) with its own sources, as the UI's cards do.

        ``sources`` are catalog ids (`<pack>.<source>`) of the pack ``pack_id``.

        The job id is chosen here, so a retried submit cannot start a second job: if the first one was accepted
        (its answer lost to a proxy error or a timeout), the retry gets a 409 for the same id.
        """
        job_id = str(uuid.uuid4())
        body = {"input": question, "data_sources": sources, "pack_id": pack_id, "job_id": job_id}
        try:
            self.post("v1/jobs/async/submit", body, attempts=8)
        except HttpError as error:
            if error.status != 409:
                raise
        return job_id

    def status(self, job_id: str) -> dict[str, Any]:
        return self.get(f"v1/jobs/async/job/{urllib.parse.quote(job_id)}")

    def export(self, job_id: str) -> dict[str, Any]:
        return self.get(f"v1/jobs/async/job/{urllib.parse.quote(job_id)}/export", timeout=120)

    def cancel(self, job_id: str) -> None:
        try:
            self.post(f"v1/jobs/async/job/{urllib.parse.quote(job_id)}/cancel", {}, attempts=2)
        except HttpError:
            pass  # it finished meanwhile


TERMINAL = frozenset({"success", "failure", "interrupted"})


def wait(
    deployment: Deployment,
    job_id: str,
    *,
    max_seconds: float,
    poll_seconds: float = 4.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Poll a job until it ends. A job still running after ``max_seconds`` is cancelled and reported as "stalled"."""
    started = clock()
    while True:
        status = deployment.status(job_id)
        if status.get("status") in TERMINAL:
            return status
        if clock() - started > max_seconds:
            deployment.cancel(job_id)
            return {**status, "status": "stalled", "api_status": status.get("status")}
        sleep(poll_seconds)
