# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The deterministic checks: row references, names, percentages, grounding and each check kind."""

import pytest
from support import market_turn
from support import receipt

from demo_eval.checks import body
from demo_eval.checks import evaluate
from demo_eval.checks import grounding
from demo_eval.checks import has_percent
from demo_eval.checks import named
from demo_eval.checks import report_text
from demo_eval.spec import Check
from demo_eval.spec import RowRef
from demo_eval.spec import SpecError
from demo_eval.spec import select_rows

ROWS = [{"asset_id": "AAA", "n": 1}, {"asset_id": "BBB", "n": 3}, {"asset_id": "CCC", "n": 3}, {"asset_id": "DDD"}]
ORACLES = {
    "leaders": [
        {"asset_id": "PEAX", "total_return": 0.2474},
        {"asset_id": "STIO", "total_return": 0.2092},
        {"asset_id": "VIAS", "total_return": -0.1878},
    ]
}
NAMES = {"PEAX": "Peaxis Urban", "VIAS": "Viasent HomeDirect"}


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
    assert [row["asset_id"] for row in select_rows(ROWS, selector)] == expected


@pytest.mark.parametrize("text", ["leaders.asset_id", "leaders[0]", "leaders[x].asset_id", "leaders[max(].asset_id"])
def test_a_malformed_row_reference_is_refused(text):
    with pytest.raises(SpecError):
        RowRef.parse(text)


def test_a_row_reference_reads_values_and_tolerates_a_short_oracle():
    assert RowRef.parse("leaders[-1].total_return").values(ORACLES) == [-0.1878]
    assert RowRef.parse("leaders[5].asset_id").values(ORACLES) == []
    assert RowRef.parse("missing[0].asset_id").values(ORACLES) == []


def test_an_asset_is_named_by_its_ticker_or_its_company_name():
    assert named("PEAX", "PEAX led the group.", NAMES)
    assert named("VIAS", "viasent homedirect lagged.", NAMES)
    assert not named("PEAX", "PEAXIS rose", {})  # a ticker is matched as a word
    assert not named(None, "anything", NAMES)


@pytest.mark.parametrize(
    ("text", "fraction", "expected"),
    [
        ("up +24.74% over 20 sessions", 0.2474, True),
        ("up 24.7 % over 20 sessions", 0.2474, True),  # display rounding
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
    turn = market_turn("Return −18.78% and – more.\n\n## Sources\n\n- [1] 99%")
    text = report_text(turn)
    assert "-18.78%" in text and "−" not in text
    assert "99%" not in body(text)


def test_grounding_is_the_share_of_percentages_found_in_the_evidence():
    turn = market_turn("PEAX +24.74% and VIAS -18.78%, overall 50%.", payload={"rows": [0.2474, -0.1878]})
    assert grounding(report_text(turn), turn) == pytest.approx(2 / 3)
    assert grounding("no numbers here", turn) is None


def test_each_check_kind():
    text = "PEAX rose +24.74%; Viasent HomeDirect fell. These are not forecasts. Item 1.05 applies."
    turn = market_turn(text)
    turn["receipts"] += [
        receipt(
            "r2",
            "retrieval_evidence",
            "mcp__retrieval__retrieve_evidence",
            {"hits": [{"sourceId": "sec", "documentId": "edgar:0000879764:0001104659-26-050851:ex99-1.htm"}]},
        ),
        receipt(
            "r3",
            "structured_prediction",
            "mcp__kumo__predict_asset_outcomes",
            {"rows": [{"assetId": "VIAS", "probability": 0.4}, {"assetId": "PEAX", "probability": 0.9}]},
        ),
    ]

    def check(kind, value, **options):
        return evaluate(Check("c", kind, value, **options), text, turn, ORACLES, NAMES)

    assert check("named", "leaders[0].asset_id")
    assert not check("named", "leaders[:].asset_id")  # STIO is not named
    assert check("named", "leaders[:].asset_id", at_least=2)
    assert check("named", "leaders[1:].asset_id", any=True)
    assert not check("named", "leaders[9].asset_id", any=True)  # no rows never passes
    assert check("percent", "leaders[0].total_return")
    assert not check("percent", "leaders[-1].total_return")
    assert check("pattern", "(?i)NOT FORECAST")
    assert check("pattern", ["Conduent", "1\\.05"])
    assert not check("pattern", ["Conduent"])
    assert not check("item_105_deadline", True)  # no deadline and no flag
    assert check("retrieved_source", "sec")
    assert not check("retrieved_source", "market_regulations")
    assert check("retrieved_filing", ("0001429937:0001429937-26-000007", "0000879764:0001104659-26-050851"))
    assert not check("retrieved_filing", ("0001429937:0001429937-26-000007",))
    assert check("prediction_named", 2)
    assert check("prediction_first", True)  # PEAX, the most probable, is named before VIAS
    assert not check("percent_grounding", 0.9)  # 24.74% is not in this run's receipts


def test_a_pattern_reads_through_markdown_emphasis():
    pattern = "(?i)not (a |an )?(forecast|prediction)|neither\\b.{0,40}forecast|no(t)? .{0,20}forecast"
    turn = market_turn("")

    def check(text):
        return evaluate(Check("c", "pattern", pattern), text, turn, ORACLES, NAMES)

    assert check("Anomaly scores are **not** forecasts or causal explanations.")
    assert check("They are *not* a ***forecast***.")
    assert not check("Anomaly scores rank sessions by how unusual they were.")


def test_a_filing_is_retrieved_only_by_a_hit_from_one_of_its_documents():
    def turn(*document_ids):
        hits = [{"sourceId": "sec_filings", "documentId": document_id} for document_id in document_ids]
        return {"receipts": [receipt("r", "retrieval_evidence", "mcp__retrieval__retrieve_evidence", {"hits": hits})]}

    listed = ("0000879764:0001104659-26-050851",)
    check = Check("c", "retrieved_filing", listed)

    # Cover pages of other 8-Ks: the source was searched, but no listed filing came back
    covers = turn("edgar:0000720500:0001193125-26-211838:asys-20260507.htm", "ecfr-title17:229.106:24c220ed7a14")
    assert evaluate(Check("s", "retrieved_source", "sec_filings"), "", covers, ORACLES, NAMES)
    assert not evaluate(check, "", covers, ORACLES, NAMES)
    assert evaluate(check, "", turn("edgar:0000879764:0001104659-26-050851:tm2612842d1_ex99-1.htm"), ORACLES, NAMES)
    assert not evaluate(check, "", turn("edgar:0000879764:0001104659-26-050852:x.htm"), ORACLES, NAMES)
    assert not evaluate(check, "", {"receipts": []}, ORACLES, NAMES)
