# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The deterministic checks: row references, mentions, percentages, grounding and each check kind."""

import pytest
from support import answer_turn
from support import prediction_receipt
from support import receipt

from demo_eval.checks import body
from demo_eval.checks import evaluate
from demo_eval.checks import grounding
from demo_eval.checks import has_percent
from demo_eval.checks import mentioned
from demo_eval.checks import report_text
from demo_eval.spec import Check
from demo_eval.spec import RowRef
from demo_eval.spec import SpecError
from demo_eval.spec import select_rows

ROWS = [{"id": "AAA", "n": 1}, {"id": "BBB", "n": 3}, {"id": "CCC", "n": 3}, {"id": "DDD"}]
ORACLES = {
    "leaders": [
        {"store": "Northwind Austin", "growth": 0.2474},
        {"store": "Northwind Reno", "growth": 0.2092},
        {"store": "Northwind Tulsa", "growth": -0.1878},
    ]
}


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ("0", ["AAA"]),
        ("-1", ["DDD"]),
        ("9", []),
        (":2", ["AAA", "BBB"]),
        ("-2:", ["CCC", "DDD"]),
        (":", ["AAA", "BBB", "CCC", "DDD"]),
        ("", ["AAA", "BBB", "CCC", "DDD"]),
        ("max(n)", ["BBB", "CCC"]),
    ],
)
def test_row_selectors(selector, expected):
    assert [row["id"] for row in select_rows(ROWS, selector)] == expected


@pytest.mark.parametrize("text", ["leaders.store", "leaders[0]", "leaders[x].store", "leaders[max(].store"])
def test_a_malformed_row_reference_is_refused(text):
    with pytest.raises(SpecError):
        RowRef.parse(text)


def test_a_row_reference_reads_values_and_tolerates_a_short_oracle():
    assert RowRef.parse("leaders[-1].growth").values(ORACLES) == [-0.1878]
    assert RowRef.parse("leaders[5].store").values(ORACLES) == []
    assert RowRef.parse("missing[0].store").values(ORACLES) == []


def test_a_value_is_mentioned_as_a_whole_word_or_phrase_ignoring_case():
    assert mentioned("Northwind Austin", "Austin led: northwind austin sold the most.")
    assert mentioned("A1", "Store A1 led.")
    assert not mentioned("A1", "Store A10 led.")  # a word, not a prefix
    assert not mentioned("Austin", "Austinville led.")
    assert mentioned(42, "Order 42 was late.")
    assert not mentioned(None, "anything")
    assert not mentioned("  ", "anything")
    assert not mentioned(True, "True story")  # a flag is not a name


@pytest.mark.parametrize(
    ("text", "fraction", "expected"),
    [
        ("up +24.74% over 20 days", 0.2474, True),
        ("up 24.7 % over 20 days", 0.2474, True),  # display rounding
        ("down -18.78%", -0.1878, True),
        # A fall written without its sign
        ("fell 1.23% over the window", -0.0123, True),
        ("a 1.23% decline", -0.0123, True),
        ("a median decline of 1.23%", -0.0123, True),
        ("1.23% lower than in July", -0.0123, True),
        ("rose 1.23%", -0.0123, False),
        ("a median of 1.23%", -0.0123, False),  # no direction: the sign is unknown
        ("fell +1.23%", -0.0123, False),  # an explicit sign must be the right one
        ("fell 1.23%", 0.0123, True),  # a positive value matches its unsigned figure, as before
        ("up 25%", 0.2474, False),
        ("up 0.2474", 0.2474, False),  # a fraction is not shown as a percentage
        ("up 24.74%", None, False),
    ],
)
def test_a_fraction_is_found_as_a_percentage(text, fraction, expected):
    assert has_percent(text, fraction) is expected


def test_the_report_text_normalizes_dashes_and_the_body_stops_at_sources():
    turn = answer_turn("Return −18.78% and – more.\n\n## Sources\n\n- [1] 99%")
    text = report_text(turn)
    assert "-18.78%" in text and "−" not in text
    assert "99%" not in body(text)


def test_grounding_is_the_share_of_percentages_found_in_the_evidence():
    turn = answer_turn("Austin +24.74% and Tulsa -18.78%, overall 50%.", rows=[0.2474, -0.1878])
    assert grounding(report_text(turn), turn) == pytest.approx(2 / 3)
    assert grounding("no numbers here", turn) is None


def test_each_check_kind():
    text = "Northwind Austin rose +24.74%; Reno fell. Returns are 30 days. These are **not** forecasts."
    turn = answer_turn(text)
    turn["receipts"] += [
        receipt(
            "r2",
            "retrieval_evidence",
            "mcp__retrieval__retrieve_evidence",
            {"hits": [{"sourceId": "retail.policies", "documentId": "retail.policies:return-policy.pdf"}]},
        ),
        prediction_receipt(),
    ]

    def check(kind, value, **options):
        return evaluate(Check("c", kind, value, **options), text, turn, ORACLES)

    assert check("mentions", "leaders[0].store")
    assert not check("mentions", "leaders[:].store")  # Tulsa is not mentioned
    assert check("mentions", "leaders[:].store", at_least=1)
    assert check("mentions", "leaders[:].store", any=True)
    assert not check("mentions", "leaders[1:].store", any=True)  # "Reno" alone is not "Northwind Reno"
    assert not check("mentions", "leaders[9].store", any=True)  # no rows never passes
    assert check("contains", ("30 DAYS", "reno"))  # every substring, ignoring case
    assert check("contains", ("not forecasts",))  # read through markdown emphasis
    assert not check("contains", ("30 days", "60 days"))
    assert check("percent", "leaders[0].growth")
    assert not check("percent", "leaders[-1].growth")
    assert check("pattern", "(?i)NOT FORECAST")
    assert check("pattern", ["Tulsa", "30 days"])
    assert not check("pattern", ["Tulsa"])
    assert check("retrieved_source", "retail.policies")
    assert not check("retrieved_source", "retail.sales")
    assert not check("percent_grounding", 0.9)  # 24.74% is not in this run's receipts
    assert check("prediction_available", True)


def test_a_prediction_is_available_only_when_kumo_scored_the_entities():
    def available(*receipts):
        turn = {"receipts": list(receipts)}
        return evaluate(Check("c", "prediction_available", True), "", turn, {})

    assert available(prediction_receipt())
    assert not available()  # the run never predicted
    assert not available(prediction_receipt(available=False))  # no Kumo endpoint: the receipt failed
    assert not available(prediction_receipt(available=True, status="failed"))
    assert available(prediction_receipt(available=False), prediction_receipt())  # any one scored run counts


def test_a_pattern_reads_through_markdown_emphasis():
    pattern = "(?i)not (a |an )?(forecast|prediction)|neither\\b.{0,40}forecast|no(t)? .{0,20}forecast"
    turn = answer_turn("")

    def check(text):
        return evaluate(Check("c", "pattern", pattern), text, turn, ORACLES)

    assert check("Anomaly scores are **not** forecasts or causal explanations.")
    assert check("They are *not* a ***forecast***.")
    assert not check("Anomaly scores rank sessions by how unusual they were.")
