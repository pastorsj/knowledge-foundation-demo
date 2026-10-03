# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The worker process: deadlines and crashes replace it, and the next call succeeds."""

import os
import shutil
import signal
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from market_analytics import tools
from market_analytics.worker import Worker
from market_analytics.worker import WorkerError

SCAN = ("analyze_market_relationships", {"top_k": 2})


@pytest.fixture
def worker(pack_root: Path) -> Iterator[Worker]:
    worker = Worker(pack_root, timeout=60)
    worker.start()
    yield worker
    worker.close()


def test_worker_answers_calls(worker: Worker) -> None:
    result = worker.call(*SCAN)
    assert result["status"] == "succeeded"
    assert len(result["payload"]["central_assets"]) == 2


def test_worker_warms_up_before_it_reports_ready(pack_root: Path, caplog: pytest.LogCaptureFixture) -> None:
    worker = Worker(pack_root, timeout=60)
    with caplog.at_level("INFO", logger="market_analytics.worker"):
        worker.start()
    worker.close()
    for tool in tools.TOOLS:
        # The fixture's minute bars end weeks before its daily prices, so intraday_scan's warm-up finds none.
        assert f"{tool} {'empty' if tool == 'intraday_scan' else 'succeeded'}" in caplog.text


def test_the_warm_up_skips_tools_the_pack_has_no_data_for(
    daily_only_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    worker = Worker(daily_only_root, timeout=60)
    with caplog.at_level("INFO", logger="market_analytics.worker"):
        worker.start()
    worker.close()
    assert "market_scan succeeded" in caplog.text
    for tool in ("sentiment_timeline", "analyze_news_price_relationship", "intraday_scan"):
        assert tool not in caplog.text


def test_worker_is_replaced_after_a_deadline(worker: Worker) -> None:
    first = worker.pid
    worker.timeout = 1e-6  # no call can answer this fast

    with pytest.raises(TimeoutError, match="exceeded its 1e-06 s deadline"):
        worker.call(*SCAN)

    assert worker.alive and worker.pid != first
    worker.timeout = 60
    assert worker.call(*SCAN)["status"] == "succeeded"


def test_worker_is_replaced_after_it_dies(worker: Worker) -> None:
    first = worker.pid
    assert first is not None
    os.kill(first, signal.SIGKILL)  # e.g. the kernel's OOM killer
    deadline = time.monotonic() + 10
    while worker.alive and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not worker.alive

    assert worker.call(*SCAN)["status"] == "succeeded"
    assert worker.pid != first


def test_worker_start_reports_why_the_pack_did_not_load(pack_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "active"
    shutil.copytree(pack_root, root)
    (root / "tables" / "daily_prices.parquet").unlink()

    with pytest.raises(WorkerError, match="failed to start: FileNotFoundError"):
        Worker(root, timeout=60).start()
