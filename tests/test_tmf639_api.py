# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0

"""TMF639 facade HTTP surface against a real sqlite-backed core.

Signature verification and the token credential provider are faked; the
registration, update, owner and visibility rules are the real main-port ones.
"""

import copy
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from agent_registry import server
from agent_registry.broadcast.event_bus import EventBus
from agent_registry.broadcast.outbox import SqlOutbox
from agent_registry.core import RegistryCore
from agent_registry.errors import AuthoritativeStoreUnavailable
from agent_registry.signature.agent_card_signature_validator import (
    AgentCardSignatureValidator, ValidationResult)
from agent_registry.tmf639 import mapping
from agent_registry.tmf639 import router as tmf_router
from common.util.authenticate_util import CallerRole, Principal

BASE = '/tmf-api/resourceInventoryManagement/v5/resource'
MERGE_PATCH = {'Content-Type': 'application/merge-patch+json'}

CARD = {
    'name': 'RanOptimizer',
    'provider': {'organization': 'acme', 'url': 'https://acme.example'},
    'description': 'Optimises the radio access network',
    'version': '1.0.0',
    'supportedInterfaces': [{'url': 'https://agents.acme.example/ran', 'protocolVersion': '1.0.0'}],
    'capabilities': {'streaming': False},
    'defaultInputModes': ['text/plain'],
    'defaultOutputModes': ['text/plain'],
    'skills': [{'id': 's1', 'name': 'tune-cells', 'description': 'Tune cells',
                'tags': [], 'inputModes': ['text/plain'], 'outputModes': ['text/plain']}],
}
RESOURCE_ID = mapping.encode_resource_id('acme', 'RanOptimizer')


def card(**changes):
    result = copy.deepcopy(CARD)
    result.update(changes)
    return result


def resource_body(agent_card=None, **extra):
    body = {'@type': 'LogicalResource',
            'resourceCharacteristic': [{'name': 'agentCard', 'value': agent_card or card()}]}
    body.update(extra)
    return body


def chars(resource):
    return {entry['name']: entry['value'] for entry in resource['resourceCharacteristic']}


@pytest.fixture
def env(tmp_path, monkeypatch):
    core = RegistryCore(use_vectordb=False, persistence_mode='sqlite',
                        persistence_conf={'sqlite.path': str(tmp_path / 'registry.db')})
    bus = EventBus(SqlOutbox(core.storage))
    monkeypatch.setattr('agent_registry.core.get_event_bus', lambda: bus)
    monkeypatch.setattr('common.custom.custom_handle.get_registry', lambda: core)
    core._llm = MagicMock()
    conf = {'tmf639.enabled': 'true'}
    monkeypatch.setattr(tmf_router, 'get_conf', lambda: conf)
    monkeypatch.setattr(server, 'OWNER_ISOLATION_ENABLED', False)
    validator = MagicMock(spec=AgentCardSignatureValidator)
    validator.validate_agent_card_async = AsyncMock(return_value=ValidationResult(is_valid=True))
    monkeypatch.setattr(server, 'get_signature_validator', lambda: validator)
    monkeypatch.setattr(server, 'get_registry_signer', lambda: None)
    monkeypatch.setitem(server.config, 'agent_approval_enabled', 'false')
    server.app.dependency_overrides[server.get_registry] = lambda: core
    yield TestClient(server.app), core, conf, validator
    server.app.dependency_overrides.clear()
    core.close()


@pytest.fixture
def token(monkeypatch):
    """Token identity mode; the caller is chosen per request with X-Test-Role / X-Test-Identity."""
    monkeypatch.setitem(server.config, 'owner.identity.mode', 'token')
    monkeypatch.setattr('agent_registry.main_auth.main_token_policy.validate_configuration', lambda: None)

    async def authenticate(request, handler):
        role = CallerRole(request.headers.get('x-test-role', 'vendor_agent'))
        return Principal('127.0.0.1', identity=request.headers.get('x-test-identity', 'vendor-a'), role=role)

    monkeypatch.setattr('agent_registry.integration.app.authenticate_request', authenticate)


def as_role(role, identity='vendor-a'):
    return {'X-Test-Role': role, 'X-Test-Identity': identity}


# ---------- feature flag and error shape ----------

def test_disabled_api_answers_404_with_tmf630_body(env):
    http, _, conf, _ = env
    conf['tmf639.enabled'] = 'false'
    response = http.get(BASE)
    assert response.status_code == 404
    assert response.json() == {'@type': 'Error', 'code': '404', 'reason': 'Not Found',
                               'status': '404', 'message': 'TMF639 API is disabled'}


def test_native_api_keeps_its_own_error_shape(env):
    http, _, _, _ = env
    response = http.delete('/rest/v1/registry-center/agent-cards/acme/ghost')
    assert response.status_code == 404
    assert response.json() == {'errors': {'error': [{'errorMessage': 'Agent not found'}]}}


def test_without_authoritative_store_every_operation_is_503(env, monkeypatch):
    http, core, _, _ = env

    def unavailable(operation):
        raise AuthoritativeStoreUnavailable(f'{operation} unavailable')

    monkeypatch.setattr(core, 'require_authoritative_store', unavailable)
    for response in (http.get(BASE), http.get(f'{BASE}/{RESOURCE_ID}'), http.post(BASE, json=resource_body()),
                     http.patch(f'{BASE}/{RESOURCE_ID}', json=resource_body(), headers=MERGE_PATCH),
                     http.delete(f'{BASE}/{RESOURCE_ID}')):
        assert response.status_code == 503, response.text
        assert response.json()['@type'] == 'Error'


# ---------- create ----------

def test_create_registers_the_card_and_returns_the_resource(env):
    http, core, _, validator = env
    response = http.post(BASE, json=resource_body(card(), name='RanOptimizer', category='A2AAgent'))
    assert response.status_code == 201, response.text
    resource = response.json()
    assert resource['id'] == RESOURCE_ID
    assert response.headers['location'] == resource['href'] == f'http://testserver{BASE}/{RESOURCE_ID}'
    assert resource['administrativeState'] == 'unlocked'
    assert resource['lifecycleState'] == 'installed'
    assert resource['unknownStatus'] is True
    values = chars(resource)
    assert values['agentCard']['name'] == 'RanOptimizer'
    assert values['url'] == 'https://agents.acme.example/ran'
    assert values['reviewStatus'] == 'published'
    assert core.get_status('RanOptimizer', 'acme') == 'published'
    validator.validate_agent_card_async.assert_awaited_once()


def test_create_honours_the_layer_characteristic(env):
    http, core, _, _ = env
    body = resource_body()
    body['resourceCharacteristic'].append({'name': 'layer', 'value': 'network'})
    response = http.post(BASE, json=body)
    assert response.status_code == 201, response.text
    assert chars(response.json())['layer'] == 'network'
    assert core.get_by_key_with_owner('RanOptimizer', 'acme').layer == 'network'


def test_public_base_url_is_used_for_href(env):
    http, _, conf, _ = env
    conf['registry.public.base_url'] = 'https://gw.example/r1-registrycenter/'
    resource = http.post(BASE, json=resource_body()).json()
    assert resource['href'] == f'https://gw.example/r1-registrycenter{BASE}/{RESOURCE_ID}'


@pytest.mark.parametrize('body, status_code', [
    ({'resourceCharacteristic': []}, 400),                                     # no agentCard
    ({'resourceCharacteristic': [{'name': 'agentCard', 'value': 'x'}]}, 400),  # not an object
    (resource_body(name='SomethingElse'), 400),                                # contradicts the card
    (resource_body(category='Router'), 400),
    (resource_body(**{'@type': 'PhysicalResource'}), 400),
    (resource_body(card(name='bad name!')), 422),                              # main-port content checks
])
def test_create_rejects_invalid_bodies(env, body, status_code):
    http, core, _, _ = env
    response = http.post(BASE, json=body)
    assert response.status_code == status_code, response.text
    assert response.json()['code'] == str(status_code)
    assert core.count() == 0


def test_create_duplicate_is_409(env):
    http, _, _, _ = env
    assert http.post(BASE, json=resource_body()).status_code == 201
    response = http.post(BASE, json=resource_body())
    assert response.status_code == 409
    assert response.json()['reason'] == 'Conflict'


def test_create_with_invalid_signature_is_401(env):
    http, core, _, validator = env
    validator.validate_agent_card_async.return_value = ValidationResult(
        is_valid=False, error_message='Signature verification failed')
    response = http.post(BASE, json=resource_body())
    assert response.status_code == 401
    assert 'Signature verification failed' in response.json()['message']
    assert core.count() == 0


# ---------- read and visibility ----------

def test_list_and_retrieve_published_resources(env):
    http, _, _, _ = env
    http.post(BASE, json=resource_body())
    http.post(BASE, json=resource_body(card(name='FaultAgent', provider={'organization': 'other', 'url': 'https://o.example'})))
    response = http.get(BASE)
    assert response.status_code == 200
    assert response.headers['x-total-count'] == '2' and response.headers['x-result-count'] == '2'
    assert {resource['name'] for resource in response.json()} == {'RanOptimizer', 'FaultAgent'}
    single = http.get(f'{BASE}/{RESOURCE_ID}')
    assert single.status_code == 200 and single.json()['name'] == 'RanOptimizer'


def test_list_filters_fields_and_pagination(env):
    http, _, _, _ = env
    for index in range(3):
        http.post(BASE, json=resource_body(card(name=f'Agent{index}')))
    assert [r['name'] for r in http.get(BASE, params={'name': 'Agent1'}).json()] == ['Agent1']
    assert http.get(BASE, params={'relatedParty.name': 'nobody'}).json() == []
    assert len(http.get(BASE, params={'relatedParty.name': 'acme'}).json()) == 3
    assert len(http.get(BASE, params={'administrativeState': 'locked'}).json()) == 0
    page = http.get(BASE, params={'offset': 1, 'limit': 1, 'fields': 'name'})
    assert page.headers['x-total-count'] == '3' and page.headers['x-result-count'] == '1'
    assert set(page.json()[0]) == {'id', 'href', '@type', 'name'}
    assert set(http.get(f'{BASE}/{mapping.encode_resource_id("acme", "Agent0")}',
                        params={'fields': 'category'}).json()) == {'id', 'href', '@type', 'category'}


@pytest.mark.parametrize('params, status_code', [
    ({'filter': 'name[?(@=="x")]'}, 501), ({'sort': 'name'}, 501),
    ({'after': 'cursor'}, 501), ({'offset': '-1'}, 400), ({'limit': 'ten'}, 400),
])
def test_list_rejects_unsupported_or_invalid_query(env, params, status_code):
    http, _, _, _ = env
    response = http.get(BASE, params=params)
    assert response.status_code == status_code
    assert response.json()['code'] == str(status_code)


def test_unknown_and_malformed_ids_are_404(env):
    http, _, _, _ = env
    for resource_id in (mapping.encode_resource_id('acme', 'ghost'), 'not-an-id'):
        assert http.get(f'{BASE}/{resource_id}').status_code == 404
        assert http.delete(f'{BASE}/{resource_id}').status_code == 404


def test_pending_cards_are_hidden_from_non_administrators(env, monkeypatch):
    http, core, _, _ = env
    monkeypatch.setitem(server.config, 'agent_approval_enabled', 'true')
    created = http.post(BASE, json=resource_body())
    assert created.status_code == 201
    assert created.json()['administrativeState'] == 'locked'
    assert created.json()['lifecycleState'] == 'planned'
    assert core.get_status('RanOptimizer', 'acme') == 'registered'
    assert http.get(BASE).json() == []
    assert http.get(BASE).headers['x-total-count'] == '0'   # counts do not leak pending cards
    assert http.get(f'{BASE}/{RESOURCE_ID}').status_code == 404


def test_administrators_see_every_review_state_and_the_owner(env, token):
    http, core, _, _ = env
    core.register_with_status(_agent('Pending'), initial_status='registered', owner='vendor-a')
    core.register_with_status(_agent('Old'), initial_status='published', owner='vendor-a')
    core.update_status('Old', 'acme', 'deprecated', reason='replaced', operator='test')

    assert http.get(BASE, headers=as_role('partner_service')).json() == []
    resources = {r['name']: r for r in http.get(BASE, headers=as_role('nms_oss', 'oss')).json()}
    assert resources['Pending']['administrativeState'] == 'locked'
    assert resources['Old']['administrativeState'] == 'shuttingDown'
    assert resources['Old']['lifecycleState'] == 'pendingRemoval'
    owner = [p for p in resources['Pending']['relatedParty'] if p['role'] == 'owner']
    assert owner[0]['partyOrPartyRole']['id'] == 'vendor-a'
    locked = http.get(BASE, headers=as_role('nms_oss', 'oss'), params={'administrativeState': 'locked'})
    assert [r['name'] for r in locked.json()] == ['Pending']


def test_health_state_is_exposed(env, monkeypatch):
    http, _, _, _ = env
    http.post(BASE, json=resource_body())
    health = MagicMock(status='suspect', last_heartbeat_at=None)
    monkeypatch.setattr(tmf_router, '_health_of', lambda name, organization: health)
    resource = http.get(f'{BASE}/{RESOURCE_ID}').json()
    assert resource['operationalState'] == 'enabled'
    assert resource['availabilityStatus'] == 'degraded'
    assert http.get(BASE, params={'availabilityStatus': 'online'}).json() == []


# ---------- patch ----------

def test_patch_replaces_the_signed_card(env):
    http, core, _, _ = env
    http.post(BASE, json=resource_body())
    response = http.patch(f'{BASE}/{RESOURCE_ID}', headers=MERGE_PATCH,
                          json=resource_body(card(description='Now with energy saving')))
    assert response.status_code == 200, response.text
    assert response.json()['description'] == 'Now with energy saving'
    assert core.get_by_key('RanOptimizer', 'acme').description == 'Now with energy saving'


@pytest.mark.parametrize('headers, body, status_code', [
    ({'Content-Type': 'application/json-patch+json'}, [{'op': 'replace'}], 415),
    ({'Content-Type': 'text/plain'}, None, 415),
    (MERGE_PATCH, {'administrativeState': 'unlocked'}, 400),        # review changes use the native API
    (MERGE_PATCH, {'resourceCharacteristic': [{'name': 'reviewStatus', 'value': 'published'},
                                              {'name': 'agentCard', 'value': CARD}]}, 400),
    (MERGE_PATCH, {'resourceCharacteristic': [{'name': 'layer', 'value': 'x'}]}, 400),  # card required
    (MERGE_PATCH, resource_body(card(name='Renamed')), 422),        # must match the resource id
])
def test_patch_rejects_unsupported_changes(env, headers, body, status_code):
    http, core, _, _ = env
    http.post(BASE, json=resource_body())
    response = http.request('PATCH', f'{BASE}/{RESOURCE_ID}', headers=headers,
                            json=body if body is not None else None,
                            content=None if body is not None else b'x')
    assert response.status_code == status_code, response.text
    assert core.get_by_key('RanOptimizer', 'acme').description == CARD['description']


def test_patch_unknown_resource_is_404(env):
    http, _, _, _ = env
    response = http.patch(f'{BASE}/{RESOURCE_ID}', headers=MERGE_PATCH, json=resource_body())
    assert response.status_code == 404


# ---------- delete ----------

def test_delete_deregisters_and_returns_204(env):
    http, core, _, _ = env
    http.post(BASE, json=resource_body())
    response = http.delete(f'{BASE}/{RESOURCE_ID}')
    assert response.status_code == 204 and response.content == b''
    assert core.get_by_key('RanOptimizer', 'acme') is None
    assert http.get(f'{BASE}/{RESOURCE_ID}').status_code == 404


# ---------- token mode: roles and owner isolation ----------

def test_token_mode_read_only_roles_cannot_write(env, token):
    http, core, _, _ = env
    response = http.post(BASE, json=resource_body(), headers=as_role('partner_service', 'partner'))
    assert response.status_code == 403
    assert response.json()['reason'] == 'Forbidden'
    assert http.get(BASE, headers=as_role('partner_service', 'partner')).status_code == 200
    assert core.count() == 0


def test_token_mode_owner_isolation_applies_to_patch_and_delete(env, token, monkeypatch):
    http, core, _, _ = env
    monkeypatch.setattr(server, 'OWNER_ISOLATION_ENABLED', True)
    assert http.post(BASE, json=resource_body(), headers=as_role('vendor_agent', 'vendor-a')).status_code == 201
    assert core.get_by_key_with_owner('RanOptimizer', 'acme').owner == 'vendor-a'

    other = as_role('vendor_agent', 'vendor-b')
    assert http.delete(f'{BASE}/{RESOURCE_ID}', headers=other).status_code == 403
    assert http.patch(f'{BASE}/{RESOURCE_ID}', json=resource_body(),
                      headers={**other, **MERGE_PATCH}).status_code == 403
    assert http.delete(f'{BASE}/{RESOURCE_ID}', headers=as_role('vendor_agent', 'vendor-a')).status_code == 204


def _agent(name):
    from a2a.types import AgentCard
    return AgentCard(name=name, description='test', version='1',
                     provider={'organization': 'acme', 'url': 'https://acme.example'})


# ---------- configuration ----------

def test_env_override_enables_the_api_through_the_public_template(monkeypatch):
    from pathlib import Path
    from common.util.app_config import _declared_keys, apply_env_overrides

    template = Path(__file__).resolve().parents[1] / 'etc' / 'conf' / 'server.conf.example'
    assert 'tmf639.enabled' in _declared_keys(str(template))
    conf = {}
    apply_env_overrides(conf, _declared_keys(str(template)), env={'REGISTRY_TMF639_ENABLED': 'true'})
    assert conf == {'tmf639.enabled': 'true'}


def test_raw_env_spelling_without_template_still_enables_the_api(env):
    http, _, conf, _ = env
    conf.clear()
    conf['tmf639_enabled'] = 'true'   # REGISTRY_TMF639_ENABLED with no template declaration
    assert http.get(BASE).status_code == 200


# ---------- review fixes: no existence/owner leak, owner sees own cards ----------

def test_writes_on_unpublished_cards_of_others_look_like_missing(env, token, monkeypatch):
    http, core, _, _ = env
    monkeypatch.setattr(server, 'OWNER_ISOLATION_ENABLED', True)
    monkeypatch.setitem(server.config, 'agent_approval_enabled', 'true')
    assert http.post(BASE, json=resource_body(), headers=as_role('vendor_agent', 'vendor-a')).status_code == 201
    other = as_role('vendor_agent', 'vendor-b')
    for response in (http.delete(f'{BASE}/{RESOURCE_ID}', headers=other),
                     http.patch(f'{BASE}/{RESOURCE_ID}', json=resource_body(), headers={**other, **MERGE_PATCH})):
        assert response.status_code == 404
        assert 'vendor-a' not in response.text
    assert core.get_by_key('RanOptimizer', 'acme') is not None


def test_forbidden_write_does_not_name_the_owner(env, token, monkeypatch):
    http, _, _, _ = env
    monkeypatch.setattr(server, 'OWNER_ISOLATION_ENABLED', True)
    http.post(BASE, json=resource_body(), headers=as_role('vendor_agent', 'vendor-a'))
    response = http.delete(f'{BASE}/{RESOURCE_ID}', headers=as_role('vendor_agent', 'vendor-b'))
    assert response.status_code == 403
    assert response.json()['message'] == 'Not the owner of this resource'


def test_owner_reads_and_resubmits_its_own_pending_card(env, token, monkeypatch):
    http, core, _, _ = env
    monkeypatch.setattr(server, 'OWNER_ISOLATION_ENABLED', True)
    monkeypatch.setitem(server.config, 'agent_approval_enabled', 'true')
    owner = as_role('vendor_agent', 'vendor-a')
    created = http.post(BASE, json=resource_body(), headers=owner)
    assert http.get(created.headers['location'].replace('http://testserver', ''), headers=owner).status_code == 200
    assert [r['name'] for r in http.get(BASE, headers=owner).json()] == ['RanOptimizer']
    assert http.get(BASE, headers=as_role('vendor_agent', 'vendor-b')).json() == []
    # owner party stays administrator-only, even for the owner
    assert [p['role'] for p in http.get(f'{BASE}/{RESOURCE_ID}', headers=owner).json()['relatedParty']] == ['provider']

    core.update_status('RanOptimizer', 'acme', 'rejected', reason='fix skills', operator='test')
    patched = http.patch(f'{BASE}/{RESOURCE_ID}', headers={**owner, **MERGE_PATCH},
                         json=resource_body(card(description='Fixed skills')))
    assert patched.status_code == 200, patched.text
    assert patched.json()['administrativeState'] == 'locked'
    assert core.get_status('RanOptimizer', 'acme') == 'registered'   # resubmitted for review


@pytest.mark.parametrize('value', [5, True, 'agentCard', {'name': 'agentCard'}])
def test_non_array_characteristics_are_400(env, value):
    http, _, _, _ = env
    response = http.post(BASE, json={'resourceCharacteristic': value})
    assert response.status_code == 400
    assert response.json()['@type'] == 'Error'


# ---------- hub (event subscriptions) ----------

HUB = '/tmf-api/resourceInventoryManagement/v5/hub'


@pytest.fixture
def broadcast(monkeypatch):
    from agent_registry.broadcast.subscriptions import MemorySubscriptionStore
    service = MagicMock()
    service.broadcast_enabled = True
    service.subscription_store = MemorySubscriptionStore()
    monkeypatch.setattr('agent_registry.broadcast.get_broadcast_service', lambda: service)
    monkeypatch.setattr('agent_registry.broadcast.callback_policy.get_conf',
                        lambda: {'broadcast.callback.allowlist': 'hooks.example'})
    return service


def test_hub_subscription_is_a_tmf639_format_subscription(env, broadcast):
    http, _, _, _ = env
    response = http.post(HUB, json={'callback': 'https://hooks.example/tmf',
                                    'query': 'eventType=ResourceCreateEvent', 'secret': 's3cret'})
    assert response.status_code == 201, response.text
    hub = response.json()
    assert hub == {'@type': 'Hub', 'id': hub['id'], 'callback': 'https://hooks.example/tmf',
                   'query': 'eventType=ResourceCreateEvent'}
    assert response.headers['location'].endswith(f"{HUB}/{hub['id']}")
    stored = broadcast.subscription_store.get(hub['id'])
    assert (stored.format, stored.event_types, stored.secret) == ('tmf639', ['AGENT_REGISTERED'], 's3cret')
    broadcast.dispatcher.add_subscription.assert_called_once_with(stored)

    assert http.get(f"{HUB}/{hub['id']}").json() == hub
    assert http.delete(f"{HUB}/{hub['id']}").status_code == 204
    broadcast.dispatcher.remove_subscription.assert_called_once_with(hub['id'])
    assert http.get(f"{HUB}/{hub['id']}").status_code == 404


@pytest.mark.parametrize('body, message', [
    ({}, "'callback' is required"),
    ({'callback': 'https://evil.example/tmf'}, 'allowlist'),
    ({'callback': 'https://hooks.example/tmf', 'query': 'eventType=Nope'}, 'Unknown eventType'),
    ({'callback': 'https://hooks.example/tmf', 'query': 'name=x'}, 'only eventType'),
    ({'callback': 'https://hooks.example/tmf', 'query': 3}, "'query' must be a string"),
    ({'callback': 'https://hooks.example/tmf', 'secret': ''}, "'secret'"),
])
def test_invalid_hub_requests_are_400(env, broadcast, body, message):
    http, _, _, _ = env
    response = http.post(HUB, json=body)
    assert response.status_code == 400, response.text
    assert message in response.json()['message']
    assert broadcast.subscription_store.list_all() == []


def test_hub_needs_change_broadcast(env, broadcast):
    http, _, _, _ = env
    broadcast.broadcast_enabled = False
    response = http.post(HUB, json={'callback': 'https://hooks.example/tmf'})
    assert response.status_code == 503
    assert response.json()['message'] == 'Change broadcast is disabled'


def test_native_subscriptions_are_not_hubs(env, broadcast):
    from agent_registry.broadcast.subscriptions import Subscription
    http, _, _, _ = env
    native = broadcast.subscription_store.create(Subscription('', 'https://hooks.example/native'))
    assert http.get(f'{HUB}/{native.subscription_id}').status_code == 404
    assert http.delete(f'{HUB}/{native.subscription_id}').status_code == 404
    assert broadcast.subscription_store.get(native.subscription_id) is not None


def test_any_authenticated_role_may_subscribe_but_not_write_resources(env, token, broadcast):
    http, _, _, _ = env
    partner = as_role('partner_service', 'partner-a')
    assert http.post(BASE, json=resource_body(), headers=partner).status_code == 403
    response = http.post(HUB, json={'callback': 'https://hooks.example/tmf'}, headers=partner)
    assert response.status_code == 201, response.text
    assert http.delete(f"{HUB}/{response.json()['id']}", headers=partner).status_code == 204


def test_tmf639_lifecycle_produces_the_documented_hub_events(env):
    """Real core events (proto field names, discoverability rules) -> TMF639 events."""
    from agent_registry.broadcast.outbox import SqlOutbox
    from agent_registry.tmf639.events import to_tmf_event
    http, core, _, _ = env
    assert http.post(BASE, json=resource_body(card(), resourceCharacteristic=[
        {'name': 'agentCard', 'value': card()}, {'name': 'layer', 'value': 'network'}])).status_code == 201
    assert http.patch(f'{BASE}/{RESOURCE_ID}', json=resource_body(card(description='v2')),
                      headers=MERGE_PATCH).status_code == 200
    assert http.delete(f'{BASE}/{RESOURCE_ID}').status_code == 204

    converted = [to_tmf_event(event, BASE) for event in SqlOutbox(core.storage).list_after(0, 10)]
    assert [body['eventType'] for body in converted] == [
        'ResourceCreateEvent', 'ResourceAttributeValueChangeEvent', 'ResourceDeleteEvent']
    created = converted[0]['event']['resource']
    assert created['id'] == RESOURCE_ID and chars(created)['layer'] == 'network'
    assert chars(created)['url'] == 'https://agents.acme.example/ran'
    assert converted[1]['event']['resource']['description'] == 'v2'


def test_pending_registration_produces_no_hub_event(env):
    from agent_registry.broadcast.outbox import SqlOutbox
    http, core, _, _ = env
    server.config['agent_approval_enabled'] = 'true'
    assert http.post(BASE, json=resource_body()).status_code == 201
    assert SqlOutbox(core.storage).list_after(0, 10) == []


def test_published_profile_documents_exactly_the_implemented_operations():
    import pathlib
    import yaml
    profile = yaml.safe_load((pathlib.Path(__file__).parents[1] / 'docs/openapi/tmf639-a2a-agent-v5.yaml')
                             .read_text(encoding='utf-8'))
    documented = {(path.replace('{id}', '{x}'), method.upper())
                  for path, item in profile['paths'].items()
                  for method in item if method != 'parameters'}
    prefix = profile['servers'][0]['url']
    implemented = {(route.path[len(prefix):].replace('{resource_id}', '{x}').replace('{hub_id}', '{x}'), method)
                   for route in tmf_router.tmf639_router.routes for method in route.methods}
    assert documented == implemented
