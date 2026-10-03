# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The Hermes config and how render_config.py narrows it to one feature set."""

import copy
import re
import subprocess
import sys

import pytest
import yaml
from common import AGENT
from common import ROOT
from common import SKILL_DIRS
from common import exposed_tools
from common import load_yaml
from render_config import FEATURES
from render_config import parse_features
from render_config import render

DEFAULT_FEATURES = {"retrieval", "tables"}  # the core profile: documents and tables


def test_config_is_at_the_image_schema_version(config):
    # The latest _config_version of Hermes 0.21.5, the version in the pinned image.
    assert config["_config_version"] == 46


def test_tool_search_is_off(config):
    assert config["tools"]["tool_search"]["enabled"] == "off"  # the string, not YAML false


def test_runs_api_offers_only_skills_and_mcp_tools(config):
    assert config["platform_toolsets"]["api_server"] == ["skills", *config["mcp_servers"]]


def test_runs_api_listens_on_sandbox_loopback(config):
    extra = config["platforms"]["api_server"]["extra"]
    assert (extra["host"], extra["port"]) == ("127.0.0.1", 8642)


def test_side_tasks_stay_off_the_escalation_route(config):
    auxiliary = config["auxiliary"]
    assert auxiliary["title_generation"]["enabled"] is False
    assert auxiliary["background_review"]["enabled"] is False
    assert auxiliary["compression"]["model"] == "knowledge-aux"
    assert config["memory"]["memory_enabled"] is False


def test_an_overloaded_model_falls_back_to_a_switchyard_route(config):
    # The chain entry must name the configured provider and one of its models, or Hermes skips it.
    [fallback] = config["fallback_providers"]
    assert fallback == {"provider": config["model"]["provider"], "model": "knowledge-fallback"}
    assert fallback["model"] in config["providers"]["switchyard"]["models"]


def test_skill_writes_are_staged_not_applied(config):
    # The skills toolset includes skill_manage; a staged write never reaches a later job.
    assert config["skills"]["write_approval"] is True


def test_soul_md_is_not_distribution_owned():
    # Hermes treats a distribution-owned SOUL.md as third-party text and replaces it whole on any
    # injection-scanner match, silently dropping the answer policy; otherwise a match only logs a
    # warning. An empty list would own every file, SOUL.md included.
    owned = load_yaml(AGENT / "profile" / "distribution.yaml")["distribution_owned"]
    assert owned and "SOUL.md" not in owned


def test_every_feature_has_its_skill():
    assert {skill for _, skill in FEATURES.values()} == {path.name for path in SKILL_DIRS}


def test_image_default_features_are_valid():
    default = re.search(r"^ARG AGENT_FEATURES=(\S+)$", (AGENT / "Dockerfile").read_text(), re.M).group(1)
    assert parse_features(default) == DEFAULT_FEATURES


def test_compose_and_demo_sh_build_the_same_default_features():
    compose = load_yaml(ROOT / "compose.yaml")["services"]["agent"]["build"]["args"]["AGENT_FEATURES"]
    assert parse_features(re.fullmatch(r"\$\{AGENT_FEATURES-([^}]+)\}", compose).group(1)) == DEFAULT_FEATURES
    env_sh = (ROOT / "scripts" / "lib" / "env.sh").read_text(encoding="utf-8")
    assert parse_features(re.search(r"^\s*local features=(\S+)$", env_sh, re.M).group(1)) == DEFAULT_FEATURES


@pytest.mark.parametrize(
    ("features", "toolsets", "disabled"),
    [
        (
            "retrieval,tables",
            ["skills", "retrieval", "tables"],
            ["predicting-with-kumo", "querying-auto-ontology"],
        ),
        (
            "retrieval,tables,kumo,ontology",
            ["skills", "retrieval", "tables", "prediction", "auto_ontology"],
            [],
        ),
        (
            "tables,kumo",
            ["skills", "tables", "prediction"],
            ["querying-auto-ontology", "searching-documents"],
        ),
    ],
)
def test_render_keeps_only_the_selected_features(config, features, toolsets, disabled):
    rendered = render(config, parse_features(features))
    assert rendered["platform_toolsets"]["api_server"] == toolsets
    assert list(rendered["mcp_servers"]) == toolsets[1:]
    assert rendered["skills"]["disabled"] == disabled


@pytest.mark.parametrize(
    ("feature", "tool"),
    [
        ("retrieval", "mcp__retrieval__retrieve_evidence"),
        ("tables", "mcp__tables__query_tables"),
        ("kumo", "mcp__prediction__predict"),
        ("ontology", "mcp__auto_ontology__ask_question"),
    ],
)
def test_each_feature_adds_exactly_its_tool(config, feature, tool):
    others = ",".join(name for name in FEATURES if name != feature)
    without = exposed_tools(render(config, parse_features(others)))
    with_feature = exposed_tools(render(config, parse_features(f"{others},{feature}")))
    assert with_feature - without == {tool}


def test_render_leaves_its_input_untouched(config):
    before = copy.deepcopy(config)
    render(config, parse_features("retrieval"))
    assert config == before


# analytics was the market tools' feature; it is gone with them.
@pytest.mark.parametrize(("features", "error"), [("retrieval,web", "unknown"), ("retrieval,analytics", "unknown")])
def test_bad_feature_sets_are_rejected(features, error):
    with pytest.raises(ValueError, match=error):
        parse_features(features)


def test_cli_writes_a_config_hermes_can_read():
    result = subprocess.run(
        [sys.executable, "render_config.py", "--features", "retrieval,tables", "profile/config.yaml"],
        cwd=AGENT,
        capture_output=True,
        text=True,
        check=True,
    )
    rendered = yaml.safe_load(result.stdout)
    assert rendered["_config_version"] == 46
    assert rendered["tools"]["tool_search"]["enabled"] == "off"
