# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Signer provisioning contract at startup.

The documented provisioning contract (jwk_private_key_path in
etc/conf/server.conf) says a missing signing material file is logged and the
signer is skipped, so the service still starts with unsigned cards. A
present-but-broken material set still fails fast, and so does enabling
signing without configuring material paths at all. Legacy deployments
upgraded before materials were provisioned rely on the first behavior.
"""

import pytest
from fastapi import HTTPException

from agent_registry import server as server_module
from agent_registry.agent_registry.agent_card_signer import AgentCardSigner


@pytest.fixture
def signer_env(tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, '_registry_signer', None)
    conf = {
        'registry.sign.enabled': 'true',
        'registry.sign.jwks_url': '',
        'registry.public.base_url': '',
        'ip': '127.0.0.1',
        'enable_https': 'true',
        'port': 5000,
    }
    monkeypatch.setattr(server_module, 'config', conf)
    return conf


def test_missing_material_files_start_with_disabled_signer(signer_env, tmp_path):
    signer_env['jwk_private_key_path'] = str(tmp_path / 'missing' / 'sign_key.pem')
    signer_env['jwk_cert_path'] = str(tmp_path / 'missing' / 'sign.cer')
    signer_env['jwk_private_key_password'] = str(tmp_path / 'missing' / 'cert_pwd')

    signer = server_module.get_registry_signer()

    assert isinstance(signer, AgentCardSigner)
    assert signer.is_enabled() is False


def test_broken_material_still_fails_fast(signer_env, tmp_path):
    (tmp_path / 'sign_key.pem').write_text('not a pem key', encoding='utf-8')
    (tmp_path / 'sign.cer').write_text('not a certificate', encoding='utf-8')
    signer_env['jwk_private_key_path'] = str(tmp_path / 'sign_key.pem')
    signer_env['jwk_cert_path'] = str(tmp_path / 'sign.cer')
    signer_env['jwk_private_key_password'] = ''

    with pytest.raises(HTTPException) as exc:
        server_module.get_registry_signer()
    assert exc.value.status_code == 503


def test_enabled_without_paths_still_fails_fast(signer_env):
    signer_env['jwk_private_key_path'] = ''
    signer_env['jwk_cert_path'] = ''

    with pytest.raises(HTTPException) as exc:
        server_module.get_registry_signer()
    assert exc.value.status_code == 503
