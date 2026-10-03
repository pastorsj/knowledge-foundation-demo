# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Everything the sandbox calls is wired the same way in every file that names it.

A new tool or endpoint touches the Hermes config, the sandbox policy or a
provider profile, contracts/tool-registry.json and compose.yaml. These checks
fail when one of them is missed.
"""

import json
import re
import tomllib
from urllib.parse import SplitResult
from urllib.parse import urlsplit

from common import AGENT
from common import ROOT
from common import exposed_tools
from common import load_yaml
from render_config import FEATURES
from render_config import KUMO_TOOL

SANDBOX_HOST = "host.openshell.internal"
HERMES_PYTHON = "/usr/bin/python3.13"  # agent/Dockerfile asserts this is Hermes' real interpreter
REGISTRY = ROOT / "contracts" / "tool-registry.json"
COMPOSE = ROOT / "compose.yaml"


def sandbox_urls(config: dict) -> dict[str, SplitResult]:
    """Every URL the agent calls from inside the sandbox, by name."""
    relay = tomllib.loads((AGENT / "profile" / "relay-plugins.toml").read_text(encoding="utf-8"))
    dockerfile = (AGENT / "Dockerfile").read_text(encoding="utf-8")
    urls = {
        "switchyard": config["providers"]["switchyard"]["api"],
        "receipts": re.search(r"HERMES_RECEIPT_API_URL=(\S+)", dockerfile).group(1),
        "phoenix": relay["components"][0]["config"]["opentelemetry"]["endpoints"][0]["endpoint"],
    } | {name: spec["url"] for name, spec in config["mcp_servers"].items()}
    return {name: urlsplit(url) for name, url in urls.items()}


def rules(policy: dict, providers: dict) -> list[dict]:
    """Network rules in the effective policy: the baked policy plus the attached provider profiles."""
    return [*policy["network_policies"].values(), *providers.values()]


def endpoint_for(url: SplitResult, policy: dict, providers: dict) -> dict:
    matches = [
        endpoint
        for rule in rules(policy, providers)
        for endpoint in rule["endpoints"]
        if (endpoint["host"], endpoint["port"]) == (url.hostname, url.port)
    ]
    assert len(matches) == 1, f"{url.geturl()} matches {len(matches)} allowed endpoints"
    return matches[0]


def allowed_calls(endpoint: dict) -> set[tuple[str, str]]:
    return {(rule["allow"]["method"], rule["allow"]["path"]) for rule in endpoint["rules"]}


def allowed_tools(endpoint: dict) -> set[str]:
    (call,) = [rule["allow"] for rule in endpoint["rules"] if rule["allow"]["method"] == "tools/call"]
    tool = call["tool"]
    return set(tool["any"]) if isinstance(tool, dict) else {tool}


def test_sandbox_urls_use_the_gateway_host_alias(config):
    for name, url in sandbox_urls(config).items():
        assert (url.scheme, url.hostname) == ("http", SANDBOX_HOST), name


def test_every_sandbox_url_is_allowed_exactly_once(config, policy, providers):
    for url in sandbox_urls(config).values():
        endpoint_for(url, policy, providers)


def test_mcp_allowlists_match_the_config(config, policy, providers):
    for name, spec in config["mcp_servers"].items():
        url = urlsplit(spec["url"])
        endpoint = endpoint_for(url, policy, providers)
        assert (endpoint["protocol"], endpoint["path"]) == ("mcp", url.path), name
        assert allowed_tools(endpoint) == set(spec["tools"]["include"]), name


def test_model_calls_and_traces_are_allowed(config, policy, providers):
    urls = sandbox_urls(config)
    assert ("POST", urls["switchyard"].path + "/chat/completions") in allowed_calls(
        endpoint_for(urls["switchyard"], policy, providers)
    )
    assert ("POST", urls["phoenix"].path) in allowed_calls(endpoint_for(urls["phoenix"], policy, providers))


def test_receipt_key_is_bound_to_the_internal_routes(providers):
    (credential,) = providers["receipts"]["credentials"]
    assert credential["env_vars"] == ["HERMES_RECEIPT_API_KEY"]
    assert (credential["auth_style"], credential["header_name"]) == ("header", "x-receipt-key")
    (endpoint,) = providers["receipts"]["endpoints"]
    assert allowed_calls(endpoint) == {
        ("GET", "/internal/hermes/jobs/*/execution-scope"),
        ("POST", "/internal/hermes/jobs/*/tool-receipts"),
        ("POST", "/internal/hermes/jobs/*/llm-calls"),
    }


def test_every_rule_names_only_the_hermes_interpreter(policy, providers):
    for rule in policy["network_policies"].values():
        assert [binary["path"] for binary in rule["binaries"]] == [HERMES_PYTHON]
    for profile in providers.values():
        assert profile["binaries"] == [HERMES_PYTHON]


def test_no_model_keys_reach_the_agent():
    # Only Switchyard holds the inference key; NVIDIA_*/OPENAI_* are global defaults for Hermes.
    for path in [AGENT / "Dockerfile", *AGENT.glob("profile/*.*")]:
        assert not re.search(r"\b(NVIDIA|OPENAI)_[A-Z_]+", path.read_text(encoding="utf-8")), path.name


def test_tool_registry_matches_the_config_and_policy(config, policy, providers):
    tools = json.loads(REGISTRY.read_text(encoding="utf-8"))["tools"]
    assert {tool["hermes_name"] for tool in tools} == exposed_tools(config)
    for tool in tools:
        assert tool["hermes_name"] == f"mcp__{tool['server']}__{tool['id']}"
        assert FEATURES[tool["profile"]][0] == tool["server"], tool["id"]
        assert (tool["profile"] == "kumo") == (tool["id"] == KUMO_TOOL), tool["id"]
        endpoint = endpoint_for(urlsplit(config["mcp_servers"][tool["server"]]["url"]), policy, providers)
        assert tool["id"] in allowed_tools(endpoint)


def test_switchyard_serves_every_configured_model(config):
    routes = sorted((ROOT / "infra" / "switchyard" / "routes").glob("*.toml.tmpl"))
    assert routes, "no infra/switchyard/routes/*.toml.tmpl"
    models = set(config["providers"]["switchyard"]["models"])
    for template in routes:
        text = template.read_text(encoding="utf-8")
        route_ids = set(re.findall(r'^id = "([^"]+)"', text, re.M))
        # A template without a capable model (passthrough) has no capable baseline route.
        expected = models if "${AGENT_CAPABLE_MODEL}" in text else models - {"market-research-capable"}
        assert expected <= route_ids, template.name


def test_compose_publishes_every_sandbox_port_on_loopback(config):
    # The supervisor maps host.openshell.internal to 127.0.0.1 on the Docker host.
    published = set()
    for service in load_yaml(COMPOSE)["services"].values():
        for port in service.get("ports", []):
            if isinstance(port, dict) and port.get("host_ip") == "127.0.0.1":
                published.add(int(port["published"]))
            elif match := re.match(r"127\.0\.0\.1:(\d+):", str(port)):
                published.add(int(match.group(1)))
    assert {url.port for url in sandbox_urls(config).values()} <= published


def test_no_service_waits_on_the_gpu_milvus_comparison():
    """The Benchmark tab's GPU Milvus and its one-shot are optional: `demo.sh up` runs them after the stack, so a GPU
    Milvus that never turns healthy cannot hold back the retrieval server, the API or `up --wait`."""
    services = load_yaml(COMPOSE)["services"]
    optional = {"milvus-gpu", "retrieval-benchmark"}

    def needs(name: str, seen: frozenset[str] = frozenset()) -> set[str]:
        direct = set(services[name].get("depends_on") or {})
        return direct | {found for dep in direct - seen if dep in services for found in needs(dep, seen | {name})}

    waiting = {name: needs(name) & optional for name in services.keys() - optional}
    assert {name: deps for name, deps in waiting.items() if deps} == {}
    assert needs("retrieval-benchmark") >= {"milvus-gpu"}  # the one-shot itself still waits for it
