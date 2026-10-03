# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""External datasets: the fingerprint, fetching from a source, and verification. Offline, on the made-up fixture."""

from __future__ import annotations

import functools
import hashlib
import http.server
import json
import os
import shutil
import tarfile
import threading
from pathlib import Path

import pytest
from conftest import MINUTE_BARS

from demo_data import external
from demo_data import fetch
from demo_data.external import Dataset
from demo_data.external import ExternalError

MANIFEST = json.loads((MINUTE_BARS / "benchmark-bundle-manifest.json").read_text())


def dataset(root: Path, fingerprint: str = MANIFEST["dataset_fingerprint"]) -> Dataset:
    return Dataset("minute-bars", root, "benchmark-bundle-manifest.json", fingerprint, MANIFEST["total_bytes"])


def test_the_fingerprint_is_the_digest_of_the_file_list():
    files = [{"path": "a.parquet", "bytes": 3, "sha256": "0" * 64}]

    assert external.fingerprint(files) == "0ae5d918d58396d35077613f64e84a1bfc80ba7a51a9b72af77b29cefc7ff0f0"
    assert external.fingerprint(MANIFEST["files"]) == MANIFEST["dataset_fingerprint"]


def test_fetch_copies_and_verifies_every_file_and_a_rerun_copies_nothing(tmp_path):
    target = dataset(tmp_path / "minute-bars")

    report = fetch.fetch(target, str(MINUTE_BARS))

    assert (report.files, report.bad, report.extra) == (12, [], [])
    assert (target.root / "market/stocks_1min/XAAA_full_1min_adjsplit.parquet").is_file()
    assert set(json.loads((target.root / external.RECORD).read_text())) == {f["path"] for f in MANIFEST["files"]}
    assert fetch.fetch(target, f"file://{MINUTE_BARS}").hashed == 0


def test_fetch_refuses_a_different_dataset_before_fetching_any_file(tmp_path):
    target = dataset(tmp_path / "minute-bars", fingerprint="f" * 64)

    with pytest.raises(ExternalError, match="this is a different dataset"):
        fetch.fetch(target, str(MINUTE_BARS))

    assert list(target.root.iterdir()) == []


def test_fetch_rejects_a_corrupted_file_and_keeps_the_others(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(MINUTE_BARS, source)
    corrupted = source / MANIFEST["files"][0]["path"]
    corrupted.write_bytes(bytes(corrupted.stat().st_size))  # same size, different content
    target = dataset(tmp_path / "minute-bars")

    with pytest.raises(ExternalError, match="1 of 12 files failed.*sha256"):
        fetch.fetch(target, str(source), attempts=1)

    assert not (target.root / MANIFEST["files"][0]["path"]).exists()
    assert not list(target.root.rglob("*.part"))
    recorded = json.loads((target.root / external.RECORD).read_text())
    assert set(recorded) == {entry["path"] for entry in MANIFEST["files"][1:]}  # a rerun fetches only the bad one


def test_fetch_streams_from_a_web_server_without_range_requests(tmp_path):
    """Python's web server ignores Range headers, as some servers do, so each file must come in one GET."""
    pytest.importorskip("aiohttp")  # the `remote` extra
    source, content = tmp_path / "source", os.urandom(12 << 20)  # past what fsspec reads ahead in its first request
    files = [{"path": "bars.parquet", "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}]
    source.mkdir()
    (source / "bars.parquet").write_bytes(content)
    (source / "manifest.json").write_text(json.dumps({"files": files}))
    target = Dataset("bars", tmp_path / "bars", "manifest.json", external.fingerprint(files), len(content))
    handler = functools.partial(QuietHandler, directory=str(source))
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            report = fetch.fetch(target, f"http://127.0.0.1:{server.server_port}/", attempts=1)
        finally:
            server.shutdown()

    assert (report.files, report.bad) == (1, [])


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


def test_fetch_unpacks_an_archive(tmp_path):
    archive = tmp_path / "minute-bars.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(MINUTE_BARS.rglob("*")):
            tar.add(path, arcname=path.relative_to(MINUTE_BARS).as_posix(), recursive=False)
    target = dataset(tmp_path / "sources" / "minute-bars")

    report = fetch.fetch(target, f"file://{archive}")

    assert (report.files, report.hashed, report.bad) == (12, 12, [])
    assert not (tmp_path / "sources" / ".minute-bars.staging").exists()


def test_verification_rehashes_only_changed_files_and_prepare_notices_them(tmp_path):
    target = dataset(tmp_path / "minute-bars")
    fetch.fetch(target, str(MINUTE_BARS))
    external.require_verified(target)
    touched = target.root / MANIFEST["files"][3]["path"]
    os.utime(touched, ns=(0, 0))

    with pytest.raises(ExternalError, match="1 of 12 files are missing, changed or not verified"):
        external.require_verified(target)
    report = external.verify(target)

    assert (report.hashed, report.bad) == (1, [])
    assert len(external.require_verified(target)) == 12


def test_verification_reports_missing_and_unlisted_files(tmp_path):
    target = dataset(tmp_path / "minute-bars")
    fetch.fetch(target, str(MINUTE_BARS))
    (target.root / MANIFEST["files"][0]["path"]).unlink()
    (target.root / "notes.txt").write_text("not in the manifest")

    report = external.verify(target)

    assert (report.bad, report.extra) == ([MANIFEST["files"][0]["path"]], ["notes.txt"])


def test_a_missing_dataset_says_how_to_fetch_it(tmp_path):
    with pytest.raises(ExternalError, match=r"demo.sh data fetch.*DATA_SOURCE_MINUTE_BARS"):
        external.require_verified(dataset(tmp_path / "minute-bars"))
