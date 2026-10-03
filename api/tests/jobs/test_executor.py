# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""The run metrics' wall time, from Hermes's own run times (the live test's default latency budgets use it)."""

from __future__ import annotations

import pytest

from demo_api.hermes.client import RunStatus
from demo_api.jobs.executor import _wall_ms


@pytest.mark.parametrize(
    ("created_at", "updated_at", "wall_ms"),
    [
        (1_790_000_000.0, 1_790_000_012.3456, 12346),
        (1_790_000_000.0, 1_790_000_000.0, 0),
        (1_790_000_012.0, 1_790_000_000.0, 0),  # a clock step back never gives a negative duration
        (None, 1_790_000_012.0, None),  # without both times the metric is left out
        (1_790_000_000.0, None, None),
    ],
)
def test_wall_time_comes_from_the_runs_created_and_updated_times(created_at, updated_at, wall_ms):
    status = RunStatus(run_id="run-1", status="completed", created_at=created_at, updated_at=updated_at)
    assert _wall_ms(status) == wall_ms
