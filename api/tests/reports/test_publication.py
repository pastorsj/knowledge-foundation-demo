# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Publishing the agent's draft: citations, the Sources list, and mechanical Markdown clean-up."""

from __future__ import annotations

from support import load_contract

from demo_api.reports.markdown import normalize_web_markdown
from demo_api.reports.publication import citations_from_receipts
from demo_api.reports.publication import publish_report

DIGEST = "6f6369003e7c34b6d766a3fea488d8113f41dcb6d9a1ecf0833ce2b5566d8433"


def test_citations_are_numbered_by_first_use_and_listed_once(tool_registry):
    evidence = citations_from_receipts(load_contract("receipts.json"), tool_registry)
    scan, retrieval = evidence[0].evidence_id, evidence[1].evidence_id
    draft = (
        f"Filings mention outages [evidence:{retrieval}]. Anomalies spiked [evidence:{scan}] "
        f"and again [evidence:{DIGEST}] [2].\n\n## Sources\n\n1. made up by the model"
    )

    report = publish_report(draft, evidence)

    assert report.markdown.splitlines()[0] == "Filings mention outages [1]. Anomalies spiked [2] and again [1]."
    assert report.markdown.endswith(
        f"## Sources\n\n- [1] Unstructured Retrieval evidence — 3 documents — evidence `{retrieval}`\n"
        f"- [2] Structured retrieval result — retail_sales — evidence `{scan}`"
    )
    assert [c["number"] for c in report.citations] == [1, 2]
    assert report.citations[1]["capabilityId"] == "structured_retrieval"
    assert report.invalid_evidence_ids == []


def test_lenticular_tokens_and_bracketed_receipt_ids_are_citations(tool_registry):
    evidence = citations_from_receipts(load_contract("receipts.json"), tool_registry)
    scan, retrieval = evidence[0].evidence_id, evidence[1].evidence_id
    draft = (
        f"Anomalies spiked【evidence:{scan}】 and filings agree 【evidence:{DIGEST}】. "
        f"Both again 【{scan}】 [{retrieval}], and unbracketed hermes-receipt:{DIGEST}."
    )

    report = publish_report(draft, evidence)

    assert report.markdown.splitlines()[0] == (
        "Anomalies spiked [1] and filings agree [2]. Both again [1] [2], and unbracketed [2]."
    )
    assert [c["evidenceId"] for c in report.citations] == [scan, retrieval]
    assert report.invalid_evidence_ids == []


def test_a_receipt_id_in_code_or_bold_is_a_citation(tool_registry):
    evidence = citations_from_receipts(load_contract("receipts.json"), tool_registry)
    scan = evidence[0].evidence_id
    draft = f"PEAX led (evidence `{scan}`), again (**{scan}**); `hermes-receipt:{'0' * 64}` is not this run's."

    report = publish_report(draft, evidence)

    assert report.markdown.splitlines()[0] == "PEAX led [1], again [1]; is not this run's."
    assert [c["evidenceId"] for c in report.citations] == [scan]
    assert report.invalid_evidence_ids == [f"hermes-receipt:{'0' * 64}"]


def test_raw_receipt_ids_written_any_way_become_numbered_markers(tool_registry):
    """The ways models have written receipt ids in recorded answers, follow-up turns above all."""
    evidence = citations_from_receipts(load_contract("receipts.json"), tool_registry)
    scan, retrieval = evidence[0].evidence_id, evidence[1].evidence_id
    draft = "\n".join(
        [
            f"Plans need a cooling-off period [evidence:{retrieval}, rank 4]; at most 120 days [{DIGEST}, ranks 9–10].",
            f"- Top 20: `evidence:{scan}`",
            f"**Source**: the anomaly scan (evidence `{scan}`).",
            f"Centrality from the tool `[evidence:{scan}]`, returns[evidence:{retrieval}].",
            f"Both [evidence:{scan}; evidence:{retrieval}], twice [evidence:{scan}, rank 1] [evidence:{scan}, rank 6].",
            f"Evidence ID: **{scan}**, and a checksum {'a' * 64} the filing quotes.",
        ]
    )

    report = publish_report(draft, evidence)

    assert report.markdown.split("\n\n## Sources")[0].splitlines() == [
        "Plans need a cooling-off period [1]; at most 120 days [1].",
        "- Top 20: [2]",
        "**Source**: the anomaly scan [2].",
        "Centrality from the tool [2], returns [1].",
        "Both [2][1], twice [2].",
        f"[2], and a checksum {'a' * 64} the filing quotes.",
    ]
    assert [c["evidenceId"] for c in report.citations] == [retrieval, scan]
    assert report.invalid_evidence_ids == []


def test_a_hash_the_answer_quotes_in_brackets_is_not_a_citation(tool_registry):
    """Only this run's receipt digests are citations without a prefix, in brackets as in prose."""
    evidence = citations_from_receipts(load_contract("receipts.json"), tool_registry)
    quoted = f"The file hash is (see {'c' * 64}), and its SHA-256 [{'D' * 64}] matches."
    report = publish_report(f"{quoted} Filings mention outages ({DIGEST}).", evidence)

    assert report.markdown.splitlines()[0] == f"{quoted} Filings mention outages [1]."
    assert report.invalid_evidence_ids == []
    assert report.status == "reference_ids_resolved"


def test_a_receipt_id_cut_short_still_names_its_receipt(tool_registry):
    evidence = citations_from_receipts(load_contract("receipts.json"), tool_registry)
    scan = evidence[0].evidence_id
    report = publish_report(
        f"Sector medians (evidence\u202f`{scan[:-1]}`), not `hermes-receipt:{scan[15:31]}`.", evidence
    )

    assert report.markdown.split("\n\n## ")[0] == "Sector medians [1], not."
    assert [c["evidenceId"] for c in report.citations] == [scan]
    assert report.invalid_evidence_ids == [f"hermes-receipt:{scan[15:31]}"]


def test_an_earlier_turns_receipt_id_is_removed_in_any_form(tool_registry):
    """A follow-up must cite this turn's evidence; an id from an earlier turn never reaches the reader."""
    evidence = citations_from_receipts(load_contract("receipts.json"), tool_registry)
    earlier = f"hermes-receipt:{'b' * 64}"
    draft = f"Cooling-off applies [evidence:{earlier}, rank 4] and (evidence `{earlier}`); see {earlier} too."

    report = publish_report(draft, evidence)

    body = report.markdown.split("\n\n## Evidence limitation")[0]
    assert body == "Cooling-off applies and; see too."
    assert "b" * 64 not in report.markdown
    assert report.invalid_evidence_ids == [earlier]
    assert report.status == "partial"


def test_unknown_evidence_is_removed_and_flagged(tool_registry):
    report = publish_report("A claim [evidence:hermes-receipt:unknown].", [])

    assert report.markdown.startswith("A claim.\n\n## Evidence limitation")
    assert report.invalid_evidence_ids == ["hermes-receipt:unknown"]


def test_the_evidence_limitation_comes_before_the_sources_list(tool_registry):
    evidence = citations_from_receipts(load_contract("receipts.json"), tool_registry)
    scan = evidence[0].evidence_id
    report = publish_report(f"Valid [evidence:{scan}], invalid [evidence:hermes-receipt:unknown].", evidence)

    body, _, sources = report.markdown.partition("\n\n## Sources\n\n")
    assert body.startswith("Valid [1], invalid.\n\n## Evidence limitation\n\n")
    assert sources == f"- [1] Structured retrieval result — retail_sales — evidence `{scan}`"
    assert report.invalid_evidence_ids == ["hermes-receipt:unknown"]


def test_the_resolution_counts_cited_uncited_and_unresolved_evidence(tool_registry):
    evidence = citations_from_receipts(load_contract("receipts.json"), tool_registry)
    scan = evidence[0].evidence_id
    cited = publish_report(f"Spiked [evidence:{scan}].", evidence)
    partial = publish_report(f"Spiked [evidence:{scan}] [evidence:hermes-receipt:unknown].", evidence)

    assert cited.resolution() == {
        "status": "reference_ids_resolved",
        "total_citations": 1,
        "uncited_evidence_count": len(evidence) - 1,
        "invalid_evidence_count": 0,
    }
    assert partial.resolution()["status"] == "partial"
    assert publish_report("No citations.", evidence).status == "evidence_uncited"
    assert publish_report("No evidence at all.", []).status == "no_evidence"


def test_failed_receipts_cannot_be_cited(tool_registry):
    failed = [r for r in load_contract("receipts.json") if r["status"] == "failed"]
    assert citations_from_receipts(failed, tool_registry) == []


def test_each_receipt_kind_has_a_sources_label(tool_registry):
    receipts = [r for r in load_contract("receipts.json") if r["status"] == "completed"]
    untemplated = next(r for r in receipts if r["artifactKind"] == "structured_prediction")
    untemplated = untemplated | {"content": untemplated["content"] | {"templateId": None}}

    labels = [citation.label for citation in citations_from_receipts([*receipts, untemplated], tool_registry)]

    assert labels == [
        "Structured retrieval result — retail_sales",
        "Unstructured Retrieval evidence — 3 documents",
        "Unstructured Retrieval evidence — Board update, third quarter",
        "Structured retrieval result — retail_sales",
        "NVIDIA Kumo prediction result — churn_90d",
        "NVIDIA Kumo prediction result — retail.sales",
    ]


def test_credentials_in_a_draft_are_masked():
    report = publish_report("Use api_key=abc123 with Bearer abcdefghijkl here.", [])
    assert report.markdown == "Use api_key=[redacted] with Bearer [redacted] here."


def test_markdown_is_unwrapped_and_tables_get_blank_lines():
    draft = "~~~markdown\nIntro\n| a | b |\n| --- | --- |\n| 1 | 2 |\nAfter   \n\n\n\n```code\nkeep   \n```\n~~~"
    assert normalize_web_markdown(draft) == (
        "Intro\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\nAfter\n\n```code\nkeep   \n```"
    )
