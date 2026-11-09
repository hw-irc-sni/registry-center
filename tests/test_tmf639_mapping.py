# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0

"""TMF639 facade: pure AgentRecord -> TMF639 v5 Resource mapping."""

from datetime import datetime, timezone

import pytest
from a2a.types import AgentCard

from agent_registry.health.state import HealthState, HealthStatus
from agent_registry.persistence.base import AgentRecord
from agent_registry.tmf639 import mapping


def _record(status='published', owner='vendor-a', layer='network', tags=('ran',)):
    card = AgentCard(
        name='Ran Optimizer', description='Optimises RAN', version='2.1.0',
        provider={'organization': 'acme.telco/ran', 'url': 'https://acme.example'},
        supported_interfaces=[{'url': 'https://agents.acme.example/ran', 'protocol_version': '1.0.0'}],
        skills=[{'id': 's1', 'name': 'tune-cells', 'description': 'Tune cells'}],
    )
    return AgentRecord(agent_card=card, owner=owner, status=status,
                       created_at='2026-11-01T10:00:00', layer=layer, tags=list(tags))


def _characteristics(resource):
    return {entry['name']: entry for entry in resource['resourceCharacteristic']}


@pytest.mark.parametrize('organization, name', [
    ('org', 'agent'),
    ('acme.telco/ran', 'Ran Optimizer'),     # dots and slashes in the organization
    ('组织', '智能体 one'),                    # non-ASCII
])
def test_resource_id_round_trips_and_is_path_safe(organization, name):
    resource_id = mapping.encode_resource_id(organization, name)
    assert '/' not in resource_id and '=' not in resource_id and resource_id.count('.') == 1
    assert mapping.decode_resource_id(resource_id) == (organization, name)


@pytest.mark.parametrize('resource_id', [
    '', 'abc', 'a.b.c', '.YWdlbnQ', 'b3Jn.', '!!!.???', 'b3Jn.YWdlbnQ=',
    'b3Jn.YWdlbnR',  # non-canonical trailing bits must not alias another id
])
def test_foreign_or_non_canonical_ids_do_not_decode(resource_id):
    assert mapping.decode_resource_id(resource_id) is None


@pytest.mark.parametrize('status, expected', [
    ('published', ('unlocked', 'installed')),
    ('registered', ('locked', 'planned')),
    ('rejected', ('locked', 'planned')),
    ('deprecated', ('shuttingDown', 'pendingRemoval')),
    (None, ('unlocked', 'installed')),        # legacy empty status means published
    ('approved', ('unlocked', 'installed')),  # alias
])
def test_review_status_maps_to_v5_states(status, expected):
    assert mapping.review_states(status) == expected


def test_to_resource_maps_card_and_registry_metadata():
    resource = mapping.to_resource(_record(), 'https://rc.example/tmf-api/resourceInventoryManagement/v5/resource')
    resource_id = mapping.encode_resource_id('acme.telco/ran', 'Ran Optimizer')
    assert resource['id'] == resource_id
    assert resource['href'] == f'https://rc.example/tmf-api/resourceInventoryManagement/v5/resource/{resource_id}'
    assert resource['@type'] == 'LogicalResource' and resource['@baseType'] == 'Resource'
    assert resource['category'] == 'A2AAgent'
    assert (resource['name'], resource['description'], resource['resourceVersion']) == (
        'Ran Optimizer', 'Optimises RAN', '2.1.0')
    assert resource['administrativeState'] == 'unlocked'
    assert resource['lifecycleState'] == 'installed'
    assert resource['creationDate'] == '2026-11-01T10:00:00'
    assert resource['resourceSpecification'] == {
        '@type': 'ResourceSpecificationRef', 'id': 'A2AAgentCard', 'name': 'A2A AgentCard', 'version': '1.0.0'}
    assert resource['relatedParty'] == [{
        '@type': 'RelatedPartyRefOrPartyRoleRef', 'role': 'provider',
        'partyOrPartyRole': {'@type': 'PartyRef', 'id': 'acme.telco/ran', 'name': 'acme.telco/ran'}}]
    chars = _characteristics(resource)
    assert chars['agentCard']['@type'] == 'ObjectCharacteristic'
    assert chars['agentCard']['value']['supportedInterfaces'][0]['url'] == 'https://agents.acme.example/ran'
    assert chars['url']['value'] == 'https://agents.acme.example/ran'
    assert chars['protocolVersion']['value'] == '1.0.0'
    assert chars['skills'] == {'@type': 'StringArrayCharacteristic', 'name': 'skills',
                               'valueType': 'array', 'value': ['tune-cells']}
    assert chars['tags']['value'] == ['ran']
    assert chars['layer']['value'] == 'network'
    assert chars['reviewStatus']['value'] == 'published'
    # no heartbeat information: state is explicitly unknown
    assert resource['unknownStatus'] is True
    assert 'operationalState' not in resource and 'healthStatus' not in chars


def test_owner_party_is_only_shown_to_administrators():
    hidden = mapping.to_resource(_record(), 'https://rc/x')
    shown = mapping.to_resource(_record(), 'https://rc/x', include_owner=True)
    assert [party['role'] for party in hidden['relatedParty']] == ['provider']
    assert shown['relatedParty'][1]['role'] == 'owner'
    assert shown['relatedParty'][1]['partyOrPartyRole']['id'] == 'vendor-a'


@pytest.mark.parametrize('health, operational, availability', [
    (HealthStatus.HEALTHY, 'enabled', 'online'),
    (HealthStatus.SUSPECT, 'enabled', 'degraded'),
    (HealthStatus.OFFLINE, 'disabled', 'offline'),
])
def test_heartbeat_health_maps_to_operational_state(health, operational, availability):
    seen = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    state = HealthState(name='Ran Optimizer', organization='acme.telco/ran',
                        last_heartbeat_at=seen, status=health, status_changed_at=seen)
    resource = mapping.to_resource(_record(), 'https://rc/x', health=state)
    assert resource['operationalState'] == operational
    assert resource['availabilityStatus'] == availability
    assert 'unknownStatus' not in resource
    chars = _characteristics(resource)
    assert chars['healthStatus']['value'] == health.value
    assert chars['lastHeartbeat']['value'] == seen.isoformat()


def test_unknown_health_is_reported_as_unknown_status():
    seen = datetime(2026, 11, 9, tzinfo=timezone.utc)
    state = HealthState(name='n', organization='o', last_heartbeat_at=seen,
                        status=HealthStatus.UNKNOWN, status_changed_at=seen)
    resource = mapping.to_resource(_record(), 'https://rc/x', health=state)
    assert resource['unknownStatus'] is True and 'operationalState' not in resource


def test_select_fields_keeps_mandatory_attributes():
    resource = mapping.to_resource(_record(), 'https://rc/x')
    assert set(mapping.select_fields(resource, 'name,administrativeState')) == {
        'id', 'href', '@type', 'name', 'administrativeState'}
    assert set(mapping.select_fields(resource, 'none')) == {'id', 'href', '@type'}
    assert mapping.select_fields(resource, None) is resource


def test_attribute_filters_are_exact_with_comma_as_or():
    resource = {'name': 'a', 'administrativeState': 'locked'}
    assert mapping.matches_filters(resource, {'administrativeState': 'locked'})
    assert mapping.matches_filters(resource, {'administrativeState': 'unlocked,locked'})
    assert not mapping.matches_filters(resource, {'name': 'A'})
    assert not mapping.matches_filters(resource, {'operationalState': 'enabled'})


@pytest.mark.parametrize('characteristics', [
    [{'value': 1}],
    ['agentCard'],
    [{'name': 'agentCard', 'value': {}}, {'name': 'agentCard', 'value': {}}],
])
def test_characteristic_map_rejects_malformed_entries(characteristics):
    with pytest.raises(ValueError):
        mapping.characteristic_map(characteristics)


def test_tmf_error_body_follows_tmf630():
    assert mapping.tmf_error(404, 'Not Found', 'Resource not found') == {
        '@type': 'Error', 'code': '404', 'reason': 'Not Found', 'status': '404',
        'message': 'Resource not found'}
