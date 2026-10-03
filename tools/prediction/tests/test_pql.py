# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the tool reads from a PQL query before Kumo sees it: tables, entity, filter, horizon, task type."""

from __future__ import annotations

import pytest

from demo_prediction.pql import Horizon
from demo_prediction.pql import PqlError
from demo_prediction.pql import horizon
from demo_prediction.pql import parse


def test_horizon_is_read_from_the_pql_window() -> None:
    assert horizon("PREDICT COUNT(orders.*, 0, 90, days) = 0 FOR EACH customers.customer_id") == Horizon(
        value=90, unit="days"
    )
    assert horizon("PREDICT SUM(orders.price, -2, 30, Days) FOR users.user_id=1") == Horizon(value=32, unit="days")
    assert horizon("PREDICT users.age FOR users.user_id=1") is None


def test_tables_come_from_the_entity_the_target_and_aggregate_arguments() -> None:
    query = parse(
        "PREDICT SUM(orders.net_amount WHERE order_lines.qty > 2, 0, 30, days) "
        "FOR EACH Customers.customer_id WHERE customers.tier = 'gold.member' AND COUNT(returns.*, -30, 0, days) = 0"
    )

    assert query.tables == frozenset({"orders", "order_lines", "customers", "returns"})
    assert (query.entity_table, query.entity_column, query.for_each) == ("customers", "customer_id", True)
    assert query.entity_filter == "customers.tier = 'gold.member' AND COUNT(returns.*, -30, 0, days) = 0"
    assert query.horizon == Horizon(value=30, unit="days")


def test_explicit_entities_are_left_to_the_query() -> None:
    query = parse("PREDICT customers.tier FOR customers.customer_id IN ('C1', 'C2')")

    assert (query.entity_table, query.for_each, query.entity_filter) == ("customers", False, None)


@pytest.mark.parametrize(
    ("pql", "task_type"),
    [
        ("PREDICT COUNT(orders.*, 0, 90, days) = 0 FOR EACH customers.customer_id", "binary_classification"),
        ("PREDICT COUNT(orders.*, 0, 90, days) > 2 FOR EACH customers.customer_id", "binary_classification"),
        ("PREDICT customers.churned FOR EACH customers.customer_id", None),
        ("PREDICT SUM(orders.net_amount, 0, 30, days) FOR EACH customers.customer_id", None),
        (
            "PREDICT LIST_DISTINCT(orders.item_id, 0, 7, days) RANK TOP 5 FOR EACH customers.customer_id",
            "temporal_link_prediction",
        ),
        ("PREDICT COUNT(orders.* WHERE orders.net_amount > 10, 0, 7, days) FOR EACH customers.customer_id", None),
    ],
)
def test_the_task_type_shows_in_the_target_when_it_can(pql: str, task_type: str | None) -> None:
    assert parse(pql).task_type == task_type


@pytest.mark.parametrize(
    "pql",
    [
        "",
        "SELECT * FROM customers",
        "PREDICT COUNT(orders.*, 0, 30, days) > 0",
        "PREDICT FOR EACH customers.customer_id",
    ],
)
def test_what_is_not_a_prediction_query_is_refused(pql: str) -> None:
    with pytest.raises(PqlError):
        parse(pql)
