# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0

"""Optional external path prefix (path.prefix), e.g. the ODA Canvas /<componentName>."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from agent_registry import server
from agent_registry.middleware import PathPrefixMiddleware, normalize_path_prefix


@pytest.mark.parametrize('value, expected', [
    ('', ''), (None, ''), ('/', ''), ('/rc', '/rc'), ('/rc/', '/rc'), (' /a/b-c.d ', '/a/b-c.d'),
])
def test_prefix_normalization(value, expected):
    assert normalize_path_prefix(value) == expected


@pytest.mark.parametrize('value', ['rc', '/a//b', '/a/../b', '/a b', '/a?b', '/%2e%2e'])
def test_malformed_prefix_is_a_config_error(value):
    with pytest.raises(ValueError):
        normalize_path_prefix(value)


def _echo_app():
    inner = FastAPI()

    @inner.get('/{full:path}')
    async def echo(full: str, request: Request):
        return {'path': request.url.path, 'raw': request.scope['raw_path'].decode()}

    return inner


@pytest.mark.parametrize('url, path', [
    ('/rc/rest/v1/x', '/rest/v1/x'),     # prefixed: stripped
    ('/rest/v1/x', '/rest/v1/x'),        # in-cluster callers keep the bare path
    ('/rcx/rest', '/rcx/rest'),          # only whole segments match
    ('/rc', '/'),
])
def test_prefix_is_stripped_only_on_a_segment_boundary(url, path):
    client = TestClient(PathPrefixMiddleware(_echo_app(), '/rc'))
    body = client.get(url).json()
    assert body['path'] == path and body['raw'] == path


def test_prefix_is_the_outermost_middleware_of_the_service():
    # Authentication and route matching must only ever see the unprefixed path.
    assert server.app.user_middleware[0].cls is PathPrefixMiddleware


def test_prefixed_requests_reach_the_service_routes():
    client = TestClient(PathPrefixMiddleware(server.app, '/openan-registrycenter'))
    tmf = client.get('/openan-registrycenter/tmf-api/resourceInventoryManagement/v5/resource')
    assert tmf.json().get('@type') == 'Error'   # the TMF639 guard answered, not a bare 404
    native = '/rest/v1/registry-center/agent-cards/acme/ghost'
    direct = TestClient(server.app).delete(native)
    prefixed = client.delete('/openan-registrycenter' + native)
    assert direct.status_code != 404 or 'errors' in direct.json()   # a real route answered
    assert (prefixed.status_code, prefixed.content) == (direct.status_code, direct.content)
