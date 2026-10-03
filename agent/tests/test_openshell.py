# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""infra/openshell: one version pin, and a gateway config that matches it."""

import re
import tomllib
from urllib.parse import urlsplit

import pytest
from common import OPENSHELL
from common import ROOT
from common import load_yaml

IMAGE_DIGEST = re.compile(r"^ghcr\.io/nvidia/openshell/[a-z]+:(?P<tag>[^@]+)@sha256:[0-9a-f]{64}$")


@pytest.fixture(scope="module")
def versions() -> dict[str, str]:
    lines = (OPENSHELL / "versions.env").read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))


@pytest.fixture(scope="module")
def gateway() -> dict:
    return tomllib.loads((OPENSHELL / "gateway.toml").read_text(encoding="utf-8"))["openshell"]


def test_images_are_pinned_by_digest_at_the_pinned_version(versions):
    for key in ("OPENSHELL_GATEWAY_IMAGE", "OPENSHELL_SUPERVISOR_IMAGE", "OPENSHELL_SANDBOX_IMAGE"):
        match = IMAGE_DIGEST.match(versions[key])
        assert match and match["tag"] == versions["OPENSHELL_VERSION"], key
    for key in ("OPENSHELL_CLI_SHA256_X86_64", "OPENSHELL_CLI_SHA256_AARCH64"):
        assert re.fullmatch(r"[0-9a-f]{64}", versions[key]), key


def test_gateway_runtime_images_match_the_pin(gateway, versions):
    docker = gateway["drivers"]["docker"]
    assert docker["supervisor_image"] == versions["OPENSHELL_SUPERVISOR_IMAGE"]
    assert docker["sandbox_runtime_image"] == versions["OPENSHELL_SANDBOX_IMAGE"]


def test_supervisors_reach_the_gateway_on_host_loopback(gateway):
    # An IP-literal endpoint is what makes the driver map host.openshell.internal to 127.0.0.1.
    endpoint = urlsplit(gateway["drivers"]["docker"]["grpc_endpoint"])
    assert (endpoint.scheme, endpoint.hostname) == ("https", "127.0.0.1")
    # The host-networked supervisor dials the host port that compose.yaml publishes for the gateway's listener.
    bind_port = gateway["gateway"]["bind_address"].rsplit(":", 1)[1]
    ports = load_yaml(ROOT / "compose.yaml")["services"]["openshell"]["ports"]
    assert f"127.0.0.1:{endpoint.port}:{bind_port}" in ports


def test_sandboxes_get_their_own_namespace_and_no_host_mounts(gateway):
    # The driver only lists, reconciles and deletes containers carrying its own namespace label.
    docker = gateway["drivers"]["docker"]
    assert docker["sandbox_label"] == "knowledge-foundation"
    assert docker["enable_bind_mounts"] is False
