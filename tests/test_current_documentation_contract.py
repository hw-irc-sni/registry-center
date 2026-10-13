# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Current signing/caller contract and links must be consistent in both languages."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("relative", [
    "docs/en/Registry Center Security Guide.md", "docs/zh/注册中心安全能力指南.md"
])
def test_signing_uses_independent_material_and_never_silently_degrades(relative):
    text = (ROOT / relative).read_text(encoding="utf-8")
    assert "jwk_cert_path=etc/sign_cert/sign.cer" in text
    assert "jwk_private_key_path=etc/sign_cert/sign_key.pem" in text
    assert "dataSigning" in text
    assert "503" in text
    assert "AgentCard 会被无签名发出" not in text


@pytest.mark.parametrize("relative", [
    "docs/en/Registry Center API Reference.md", "docs/zh/注册中心API参考.md"
])
def test_main_token_roles_and_jwks_tls_exception_are_documented(relative):
    text = (ROOT / relative).read_text(encoding="utf-8")
    assert "owner.identity.mode=token" in text
    assert "vendor_agent" in text and "nms_oss" in text
    assert "verify_client=true" in text and "JWKS" in text
    assert "503" in text and "403" in text


@pytest.mark.parametrize("relative", [
    "docs/en/Registry Center Development Guide.md", "docs/zh/注册中心开发指南.md"
])
def test_public_guides_do_not_link_unpublished_private_design(relative):
    text = (ROOT / relative).read_text(encoding="utf-8")
    assert "../../design/AgentCard_Security_Specification.md" not in text


@pytest.mark.parametrize("relative, heading", [
    ("docs/en/Registry Center API Reference.md", "## Get Public Key Information"),
    ("docs/zh/注册中心API参考.md", "## 获取公钥信息"),
])
def test_keys_reference_has_no_request_body_or_model_status(relative, heading):
    text = (ROOT / relative).read_text(encoding="utf-8")
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    assert "| 422 |" not in section
    assert "| 503 |" in section
    assert "model is unavailable" not in section
    assert "模型不可用" not in section


@pytest.mark.parametrize("relative", [
    "docs/en/Registry Center API Reference.md", "docs/zh/注册中心API参考.md",
])
def test_layer_metadata_routes_and_administrator_requirement_are_paired(relative):
    import ast

    text = (ROOT / relative).read_text(encoding="utf-8")
    source = ast.parse((ROOT / "agent_registry/server.py").read_text(encoding="utf-8"))
    routes = {
        node.args[0].value for node in ast.walk(source)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"get", "post"} and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and "/agent-cards-with-layer" in node.args[0].value
    }
    assert len(routes) == 3
    for route in routes:
        assert route.removeprefix("/rest/v1/registry-center") in text
    assert "nms_oss" in text and "hasMore" in text and "topN" in text
