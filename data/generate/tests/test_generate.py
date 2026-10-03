# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The roster, the SEC checks and the generate loop, offline: a fake designer stands in for Data Designer."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from data_generate import cli
from data_generate import roster
from data_generate import sec

PACK_DIR = Path(__file__).resolve().parents[2] / "packs" / "synthetic-market"
MODEL = yaml.safe_load((PACK_DIR / "generator" / "model.yaml").read_text(encoding="utf-8"))
FIXTURES = Path(__file__).parent / "fixtures"


class FakeDesigner:
    """Answers like Nemotron would, instantly; `names` overrides a slot's company name."""

    model = "fake"

    def __init__(self, names: dict[int, str] | None = None) -> None:
        self.names = names or {}
        self.calls: list[tuple[str, int]] = []
        self.usage = cli.jobs.Usage()

    def run(self, job, attempt, seed: pd.DataFrame, output, prompt, *, max_tokens):
        self.calls.append((job, len(seed)))
        answers = [getattr(self, job)(row) for row in seed.to_dict("records")]
        return seed.assign(answer=answers)

    def companies(self, row):
        name = self.names.pop(row["slot"], f"{row['name_root']} Systems")
        return {"company_name": name, "profile": f"Makes things for {row['industry']} customers."}

    def headlines(self, row):
        return {"templates": [f"{{company}} {row['event_type']} update {n}" for n in range(row["count"])]}

    def stories(self, row):
        return {"headline": f"{row['company_name']} announces news", "summary": "It happened."}


def no_collisions() -> sec.SecNames:
    return sec.SecNames()


def test_roster_is_seeded_and_prefix_stable():
    first = roster.build(MODEL, 12, lambda _: False, lambda _: False)
    assert first == roster.build(MODEL, 50, lambda _: False, lambda _: False)[:12]
    assert [slot.sic_code for slot in first] == [story["sic"] for story in MODEL["story"]]
    assert all(slot.is_story for slot in first)
    assert all(5 <= len(slot.name_root) <= 10 and len(slot.ticker) == 4 for slot in first)
    assert first[0].sector == "Manufacturing"  # SIC 3674


def test_roster_skips_taken_roots_and_tickers():
    first = roster.build(MODEL, 3, lambda _: False, lambda _: False)
    names = sec.SecNames(tickers={first[0].ticker}, title_words={first[1].name_root.casefold()})
    second = roster.build(MODEL, 3, names.root_taken, names.ticker_taken)
    assert second[0].name_root == first[0].name_root and second[0].ticker != first[0].ticker
    assert second[1].name_root != first[1].name_root
    assert second[2] == first[2]


def test_no_root_or_ticker_spells_an_unfit_word():
    assert roster.unfit("Fosex") and roster.unfit("NUDE") and not roster.unfit("Velbrook")
    companies = [json.loads(line) for line in (PACK_DIR / "text" / "companies.jsonl").read_text().splitlines()]
    assert [row["ticker"] for row in companies if roster.unfit(row["name_root"]) or roster.unfit(row["ticker"])] == []


def test_sec_files_are_indexed():
    names = sec.load(None, FIXTURES / "sec")
    assert names.ticker_taken("ACME") and names.ticker_taken("BRK") and names.ticker_taken("fundx")
    assert names.name_taken("The ACME Widgets, Inc.")
    assert names.root_taken("Berkshire") and not names.root_taken("Velbrook")
    assert len(names.record["files"]) == 3


def test_generate_writes_checked_text(tmp_path):
    designer = FakeDesigner()
    text, record = cli.generate(designer, MODEL, 20, no_collisions(), {})
    companies = text["companies.jsonl"]
    assert [row["slot"] for row in companies] == list(range(20))
    assert all(row["company_name"].startswith(row["name_root"]) for row in companies)
    variants = MODEL["headline_variants"] * len(MODEL["event_types"]) * len(MODEL["sentiments"])
    assert len(text["headlines.jsonl"]) == variants
    assert [row["date"] for row in text["stories.jsonl"]] == [story["date"] for story in MODEL["story"]]
    assert record["counts"] == {"companies": 20, "headline_templates": variants, "stories": 12}

    digests = cli.write_text(tmp_path, text)
    again = FakeDesigner()
    rerun, _ = cli.generate(again, MODEL, 20, no_collisions(), cli.read_text(tmp_path))
    assert again.calls == [] and rerun == text  # nothing left to generate
    assert set(digests) == set(cli.TEXT_FILES)


def test_generate_extends_to_more_issuers(tmp_path):
    text, _ = cli.generate(FakeDesigner(), MODEL, 12, no_collisions(), {})
    designer = FakeDesigner()
    larger, _ = cli.generate(designer, MODEL, 30, no_collisions(), text)
    assert designer.calls == [("companies", 18)]
    assert larger["companies.jsonl"][:12] == text["companies.jsonl"]


def test_a_name_matching_an_sec_title_costs_the_slot_its_root():
    first = roster.build(MODEL, 12, lambda _: False, lambda _: False)
    names = sec.SecNames(titles={sec.normalized_name(f"{first[3].name_root} Systems")})
    designer = FakeDesigner()
    text, record = cli.generate(designer, MODEL, 12, names, {})
    assert text["companies.jsonl"][3]["name_root"] != first[3].name_root
    assert record["rounds"]["companies"][0]["refused"] == {"sec-name": 1}
    assert designer.calls[:2] == [("companies", 12), ("companies", 1)]


def test_malformed_names_are_retried_then_fail():
    designer = FakeDesigner({0: "Wrongly Named Co"})
    text, record = cli.generate(designer, MODEL, 12, no_collisions(), {})
    assert record["rounds"]["companies"][0]["refused"] == {"format": 1}
    assert text["companies.jsonl"][0]["company_name"].endswith("Systems")

    class Stubborn(FakeDesigner):
        def companies(self, row):
            return {"company_name": "Acme Inc", "profile": "x"}

    with pytest.raises(SystemExit, match="still failing"):
        cli.generate(Stubborn(), MODEL, 12, no_collisions(), {})


@pytest.mark.parametrize(
    ("template", "ok"),
    [
        ("{company} raises its outlook", True),
        ("{company} and {company}", False),
        ("No placeholder here", False),
        ("{company} beats {estimates}", False),
        ("{company} " + "x" * 110, False),
    ],
)
def test_headline_templates(template, ok):
    assert cli.template_ok(template) is ok


GATEWAY = "https://gateway.example.com/v1"
BUILD = "https://integrate.api.nvidia.com/v1"


@pytest.mark.parametrize(
    ("environment", "key"),
    [
        ({"DATA_DESIGNER_API_KEY": "own", "INFERENCE_API_KEY": "nvapi-x"}, "own"),
        # The inference key goes only to its own endpoint, or (an nvapi- key) to build.nvidia.com.
        ({"INFERENCE_API_KEY": "sk-x", "INFERENCE_BASE_URL": GATEWAY, "DATA_DESIGNER_BASE_URL": f"{GATEWAY}/"}, "sk-x"),
        ({"INFERENCE_API_KEY": "nvapi-x", "INFERENCE_BASE_URL": BUILD}, "nvapi-x"),
        ({"INFERENCE_API_KEY": "nvapi-x"}, "nvapi-x"),
        ({"INFERENCE_API_KEY": "nvapi-x", "INFERENCE_BASE_URL": GATEWAY}, "nvapi-x"),
    ],
)
def test_the_designer_key(environment, key):
    assert cli.designer_key(environment) == key


@pytest.mark.parametrize(
    "environment",
    [
        {},
        # A gateway key never goes to build.nvidia.com (the default endpoint), nor any key to another host.
        {"INFERENCE_API_KEY": "sk-x", "INFERENCE_BASE_URL": GATEWAY},
        {"INFERENCE_API_KEY": "nvapi-x", "INFERENCE_BASE_URL": BUILD, "DATA_DESIGNER_BASE_URL": GATEWAY},
    ],
)
def test_no_designer_key_for_another_host(environment):
    with pytest.raises(SystemExit, match="set DATA_DESIGNER_API_KEY"):
        cli.designer_key(environment)
