# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pack validation and resolution, on the real packs and broken copies of them."""

from __future__ import annotations

import json

import pytest
import yaml
from conftest import PACKS
from conftest import edit_yaml

from demo_data.pack import DATA_ROOT
from demo_data.pack import PackError
from demo_data.pack import declared_contract_errors
from demo_data.pack import find_contracts
from demo_data.pack import load_pack


@pytest.mark.parametrize("name", ["synthetic-market", "us-equities"])
def test_the_packs_are_valid_and_satisfy_every_tool_contract(name, contract):
    pack = load_pack(PACKS / name)
    assert pack.database_tables[:5] == [
        "assets",
        "ticker_history",
        "trading_sessions",
        "daily_prices",
        "asset_relationships",
    ]
    assert declared_contract_errors(pack, [contract]) == []
    contracts = find_contracts()
    if contracts:  # tools/*/contract/*.json, when this checkout has them
        assert declared_contract_errors(pack, contracts) == []
    assert sum(question.get("featured", False) for question in pack.questions) == 6


def test_filings_are_a_document_source_in_both_packs_and_never_news():
    for name in ("synthetic-market", "us-equities"):
        pack = load_pack(PACKS / name)
        sources = {source["id"]: source for source in pack.manifest["sources"]}
        filings = [corpus for corpus in pack.manifest["documents"]["corpora"] if corpus["source"] == "sec_filings"]
        assert sources["sec_filings"]["kind"] == "documents"
        assert [corpus["format"] for corpus in filings] == ["edgar-filings"]
        assert pack.manifest["market"]["news"] in (None, "news.parquet")  # a dataset table, never filings


# The documents.jsonl id prefixes of each document source that eval/retrieval.yaml names documents of
DOCUMENT_PREFIXES = {"market_regulations": ("ecfr-title17:", "fr-"), "world_news": ("gdelt:",)}


@pytest.mark.parametrize("name", ["synthetic-market", "us-equities"])
def test_retrieval_answer_checks_name_questions_and_pinned_filings(name):
    pack = load_pack(PACKS / name)
    checks = yaml.safe_load(pack.path("eval/retrieval.yaml").read_text())
    corpus = pack.select_corpora(["sec_filings"])[0]
    pinned = {filing["filing_id"] for filing in json.loads(pack.path(corpus["manifest"]).read_text())["filings"]}
    questions = {question["id"]: question for question in pack.questions}

    for question_id, check in checks.items():
        sources = set(questions[question_id]["sources"])
        assert sources & {"sec_filings", "market_regulations", "world_news"}, question_id
        assert set(check.get("filings", [])) <= pinned, question_id
        prefixes = tuple(prefix for source, found in DOCUMENT_PREFIXES.items() if source in sources for prefix in found)
        assert all(document.startswith(prefixes) for document in check.get("documents", [])), question_id


def test_a_broken_pack_reports_every_problem(pack_copy):
    pack_dir = pack_copy()

    def break_it(manifest):
        manifest["structured"]["database_name"] = "market"
        manifest["provenance"][0]["license"] = "MIT"
        manifest["documents"]["corpora"][1]["manifest"] = "corpus/missing.json"
        manifest["documents"]["corpora"][0]["format"] = "gdelt-parquet"
        manifest["analytics"]["news_table"] = "news_feed"
        manifest["prediction"]["entity"]["table"] = "assets"

    def break_questions(document):
        for question in document["questions"]:
            question["featured"] = False
        document["questions"][0]["sources"] = ["market_prices"]

    edit_yaml(pack_dir / "pack.yaml", break_it)
    edit_yaml(pack_dir / "questions.yaml", break_questions)

    with pytest.raises(PackError) as raised:
        load_pack(pack_dir)

    assert sorted(raised.value.errors) == sorted(
        [
            "structured.database_name must be 'synthetic_market' (the pack id in snake_case)",
            "origin synthetic-market-generator: unknown license 'MIT'",
            "corpus sec_filings: format gdelt-parquet takes files",
            "corpus market_regulations: manifest corpus/missing.json not found",
            "the tables with origin synthetic-market-generator must be exactly "
            "['asset_relationships', 'assets', 'daily_prices', 'news_feed', 'ticker_history', 'trading_sessions']",
            "analytics.news_table 'news_feed' is not a table",
            "prediction.entity.table must be one of prediction.tables",
            "question market-leaders: unknown source 'market_prices'",
            "at least one question must be featured",
        ]
    )


def test_conversations_share_the_question_ids_and_name_known_sources(pack_copy):
    pack_dir = pack_copy("us-equities")

    def break_conversations(document):
        document["conversations"][0]["id"] = "market-leaders"
        document["conversations"][1]["sources"] = ["market_prices"]

    edit_yaml(pack_dir / "questions.yaml", break_conversations)

    with pytest.raises(PackError) as raised:
        load_pack(pack_dir)

    assert sorted(raised.value.errors) == [
        "conversation peer-network-follow-up: unknown source 'market_prices'",
        "question market-leaders is declared twice",
    ]


def test_resolve_serves_only_the_conversations_the_build_can_answer():
    pack = load_pack(PACKS / "us-equities")
    resolved = pack.resolve(pack.resolve_profile(None), pack.select_corpora(["market_regulations"]))

    conversations = {conversation["id"]: conversation for conversation in resolved["conversations"]}
    assert "filings-to-regulations" not in conversations  # needs sec_filings, which is not built
    assert "regulation-fd-follow-up" in conversations
    assert all(len(conversation["turns"]) >= 2 for conversation in conversations.values())


def test_files_are_read_only_from_an_external_dataset(pack_copy):
    pack_dir = pack_copy("us-equities")
    edit_yaml(pack_dir / "pack.yaml", lambda m: m["documents"]["corpora"][3].update(origin="ecfr-title-17"))

    with pytest.raises(PackError) as raised:
        load_pack(pack_dir)

    assert raised.value.errors == [
        "corpus world_news: files are read from an external dataset, so the origin must be external"
    ]


def test_schema_errors_name_the_offending_field(pack_copy):
    pack_dir = pack_copy()
    edit_yaml(
        pack_dir / "pack.yaml", lambda m: m["prediction"]["population"].update(ids=[f"a{i}" for i in range(1001)])
    )

    with pytest.raises(PackError) as raised:
        load_pack(pack_dir)

    assert len(raised.value.errors) == 1
    assert raised.value.errors[0].startswith("pack.yaml: prediction/population/ids: ")


def test_resolve_serves_only_what_the_build_contains(synthetic_pack, tmp_path):
    corpora = synthetic_pack.select_corpora(["market_regulations"])
    resolved = synthetic_pack.resolve("interactive", corpora, market_root=tmp_path)

    assert [source["id"] for source in resolved["sources"]] == ["market_data", "market_regulations"]
    questions = [question["id"] for question in resolved["questions"]]
    assert "market-leaders" in questions
    assert "cyber-disclosure-rules" not in questions  # needs sec_filings, which is not built
    assert "large-universe-scan" not in questions  # standard and large profiles only
    assert "intraday-ranges" not in questions  # 1-minute profiles only
    assert list(resolved["analytics"]["universes"]) == ["top_12", "all_assets"]
    assert resolved["analytics"]["relationship_graph"]["mode"] == "full_correlation"
    assert resolved["structured"]["database"] == "structured/synthetic_market.duckdb"
    queries = resolved["documents"].pop("benchmark_queries")
    assert resolved["documents"] == {
        "collection": "synthetic_market_documents",
        "sources": ["market_regulations"],
        "path": "corpus/documents.jsonl",
    }
    # The Milvus comparison's held-out queries: only those on the corpora the build indexed
    assert len(queries) == 6
    assert all(query["sources"] == ["market_regulations"] for query in queries)


def test_resolve_reports_the_bars_a_profile_writes(synthetic_pack, tmp_path):
    """Every profile's bars are imported as minute bars, but only the 1-minute profiles have more than one a day."""
    daily = synthetic_pack.resolve("standard", [], market_root=tmp_path)["market"]["bars"]
    minutes = synthetic_pack.resolve("intraday", [], market_root=tmp_path)["market"]["bars"]

    assert (daily["frequency"], minutes["frequency"]) == ("1d", "1min")
    assert daily["root"] == str(tmp_path)


def test_default_corpora_skip_opt_in_ones():
    pack = load_pack(PACKS / "us-equities")

    assert [corpus["source"] for corpus in pack.select_corpora(None)] == [
        "sec_filings",
        "market_regulations",
        "market_regulations",  # the eCFR and the Federal Register rule
    ]
    assert [corpus["source"] for corpus in pack.select_corpora(["world_news"])] == ["world_news"]
    with pytest.raises(PackError, match="no corpus for"):
        pack.select_corpora(["market_prices"])


def test_a_source_s_corpora_are_all_opt_in_or_none_are(pack_copy):
    pack_dir = pack_copy("us-equities")
    edit_yaml(pack_dir / "pack.yaml", lambda m: m["documents"]["corpora"][2].update(opt_in=True))

    with pytest.raises(PackError) as raised:
        load_pack(pack_dir)

    assert raised.value.errors == ["source market_regulations has opt-in and default corpora"]


def test_profiles(synthetic_pack):
    assert synthetic_pack.resolve_profile(None) == "standard"
    assert synthetic_pack.resolve_profile("ci") == "ci"
    with pytest.raises(PackError, match="unknown profile"):
        synthetic_pack.resolve_profile("huge")
    assert load_pack(PACKS / "us-equities").resolve_profile(None) == "default"


def test_digest_tracks_build_inputs_only(pack_copy):
    pack = load_pack(pack_copy())
    corpora = pack.select_corpora(None)
    digest = pack.digest("interactive", corpora, "builder")

    assert pack.digest("interactive", corpora, "builder") == digest
    assert pack.digest("standard", corpora, "builder") != digest
    assert pack.digest("interactive", corpora[:1], "builder") != digest
    assert pack.digest("interactive", corpora, "another builder") != digest
    (pack.directory / "README.md").write_text("data card")
    (pack.directory / "eval" / "notes.md").write_text("not an input")
    # data generate's resumable work directory, left by an interrupted run
    (pack.directory / "text" / ".work" / "seeds").mkdir(parents=True)
    (pack.directory / "text" / ".work" / "seeds" / "companies.parquet").write_bytes(b"seed")
    assert pack.digest("interactive", corpora, "builder") == digest
    (pack.directory / "ontology.yaml").write_text(pack.path("ontology.yaml").read_text() + "\n")
    assert pack.digest("interactive", corpora, "builder") != digest


def test_benchmark_queries_must_search_corpora_of_the_pack(pack_copy):
    pack_dir = pack_copy()

    def add_query(manifest):
        manifest["documents"]["benchmark_queries"].append({"query": "anything", "sources": ["market_data"]})

    edit_yaml(pack_dir / "pack.yaml", add_query)

    with pytest.raises(PackError) as raised:
        load_pack(pack_dir)

    assert raised.value.errors == ["benchmark query 16: ['market_data'] have no corpus"]


def test_question_tools_are_the_tool_registry_pills():
    """questions.yaml `tools` and the UI's pills are one vocabulary: Pill in contracts/tool-registry.schema.json."""
    contract = json.loads((DATA_ROOT.parent / "contracts" / "tool-registry.schema.json").read_text())
    schema = json.loads((DATA_ROOT / "schemas" / "questions.schema.json").read_text())
    question = schema["properties"]["questions"]["items"]["properties"]

    assert question["tools"]["items"]["enum"] == contract["$defs"]["Pill"]["enum"]


# The composer's example picker of each pack (questions.yaml `examples`), in order
EXAMPLES = {
    "us-equities": [
        "unusual-sessions",
        "outcome-prediction",
        "peer-network",
        "sector-sql",
        "moves-and-filings",
        "market-leaders",
        "cyber-disclosure-rules",
        "intraday-ranges",
        "beneficial-ownership",
        "large-universe-scan",
        "nvidia-results-and-prices",
        "world-news-cyber",
    ],
    "synthetic-market": [
        "unusual-sessions",
        "outcome-prediction",
        "peer-network",
        "sector-sql",
        "cyber-disclosure-rules",
        "market-leaders",
        "news-and-filings",
        "news-sentiment-reaction",
        "story-event-context",
        "large-universe-scan",
        "intraday-ranges",
    ],
}
ALL_TOOLS = {"cudf", "cuml", "cugraph", "kumo", "retrieval", "ontology"}


@pytest.mark.parametrize("name", ["synthetic-market", "us-equities"])
def test_each_pack_s_examples_cover_its_featured_questions_and_every_tool(name):
    pack = load_pack(PACKS / name)
    questions = {question["id"]: question for question in pack.questions}

    assert pack.examples == EXAMPLES[name]
    assert len(pack.examples) <= 12
    assert len(set(pack.examples)) == len(pack.examples)
    assert {q["id"] for q in pack.questions if q.get("featured")} <= set(pack.examples)
    assert {tool for question_id in pack.examples for tool in questions[question_id]["tools"]} == ALL_TOOLS
    # The picker shows five rows before it scrolls: those five show every tool
    assert {tool for question_id in pack.examples[:5] for tool in questions[question_id]["tools"]} == ALL_TOOLS


def test_us_equities_examples_leave_out_the_weak_recordings():
    """bank-results and filings-to-regulations keep weaker recordings (the pack README), so the picker skips them."""
    assert not {"bank-results", "filings-to-regulations"} & set(load_pack(PACKS / "us-equities").examples)


def test_broken_examples_report_every_problem(pack_copy):
    pack_dir = pack_copy()

    def break_examples(document):
        # No market-leaders (featured), no outcome-prediction (the only kumo question), and a conversation-like id
        document["examples"] = [e for e in document["examples"] if e not in ("market-leaders", "outcome-prediction")]
        document["examples"].append("leaders-follow-up")

    edit_yaml(pack_dir / "questions.yaml", break_examples)

    with pytest.raises(PackError) as raised:
        load_pack(pack_dir)

    assert sorted(raised.value.errors) == [
        "example leaders-follow-up is not a question",
        "featured question market-leaders is not an example",
        "no example declares the tools ['kumo']",
    ]


def test_examples_are_at_most_twelve_unique_questions(pack_copy):
    pack_dir = pack_copy("us-equities")
    questions = [question["id"] for question in yaml.safe_load((pack_dir / "questions.yaml").read_text())["questions"]]

    edit_yaml(pack_dir / "questions.yaml", lambda document: document.update(examples=questions[:13]))
    with pytest.raises(PackError) as raised:
        load_pack(pack_dir)
    assert raised.value.errors == [f"questions.yaml: examples: {questions[:13]} is too long"]

    edit_yaml(pack_dir / "questions.yaml", lambda document: document.update(examples=EXAMPLES["us-equities"][:11] * 2))
    with pytest.raises(PackError) as raised:
        load_pack(pack_dir)
    assert len(raised.value.errors) == 2  # too long, and not unique
    assert any("non-unique elements" in error for error in raised.value.errors)


def test_resolve_offers_the_examples_the_build_serves_in_order(synthetic_pack, pack_copy):
    standard = synthetic_pack.resolve("standard", synthetic_pack.select_corpora(None))
    assert standard["examples"] == EXAMPLES["synthetic-market"][:-1]  # intraday-ranges needs the 1-minute bars

    # ci: the minute bars but not the large universe; no sec_filings, which two of them search
    regulations_only = synthetic_pack.resolve("ci", synthetic_pack.select_corpora(["market_regulations"]))
    assert regulations_only["examples"] == [
        "unusual-sessions",
        "outcome-prediction",
        "peer-network",
        "sector-sql",
        "market-leaders",
        "news-sentiment-reaction",
        "story-event-context",
        "intraday-ranges",
    ]

    # Without the list, pack.json has none, and the API offers its default (the featured questions, then the rest)
    pack_dir = pack_copy()
    edit_yaml(pack_dir / "questions.yaml", lambda document: document.pop("examples"))
    assert "examples" not in load_pack(pack_dir).resolve("standard", [])
