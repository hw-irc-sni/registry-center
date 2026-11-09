# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0

"""Pure mapping between Registry Center records and TMF639 v5 Resources.

No FastAPI or registry dependencies live here: the router supplies the stored
AgentRecord, the caller's view (admin or not) and the agent's health state,
and gets back the TMF639 representation. Keeping the mapping pure keeps the
state tables testable without a running service.

Field values follow the TMF639 v5.0.0 OAS enums:
- administrativeState: locked | unlocked | shuttingDown
- operationalState:    enabled | disabled
- lifecycleState:      planned | installed | pendingRemoval
- availabilityStatus:  online | degraded | offline | ...
"""

import base64
import binascii
from typing import Any, Dict, Iterable, List, Optional, Tuple

from google.protobuf.json_format import MessageToDict

from agent_registry.model.agent_layer import default_layer
from agent_registry.status import (
    DEPRECATED_STATUS,
    DISCOVERABLE_STATUS,
    PENDING_STATUS,
    REJECTED_STATUS,
    normalize_status,
)

API_BASE_PATH = '/tmf-api/resourceInventoryManagement/v5'
RESOURCE_PATH = API_BASE_PATH + '/resource'

CATEGORY = 'A2AAgent'
SPECIFICATION_ID = 'A2AAgentCard'
SPECIFICATION_NAME = 'A2A AgentCard'

# Characteristic names. agentCard and layer are the only writable ones.
CHAR_AGENT_CARD = 'agentCard'
CHAR_LAYER = 'layer'
CHAR_URL = 'url'
CHAR_PROTOCOL_VERSION = 'protocolVersion'
CHAR_SKILLS = 'skills'
CHAR_TAGS = 'tags'
CHAR_REVIEW_STATUS = 'reviewStatus'
CHAR_HEALTH_STATUS = 'healthStatus'
CHAR_LAST_HEARTBEAT = 'lastHeartbeat'
WRITABLE_CHARACTERISTICS = frozenset({CHAR_AGENT_CARD, CHAR_LAYER})

# Review status -> (administrativeState, lifecycleState).
_REVIEW_STATES = {
    DISCOVERABLE_STATUS: ('unlocked', 'installed'),
    PENDING_STATUS: ('locked', 'planned'),
    REJECTED_STATUS: ('locked', 'planned'),
    # Withdrawn from discovery for good: existing users may continue, no new ones.
    DEPRECATED_STATUS: ('shuttingDown', 'pendingRemoval'),
}

# Heartbeat health -> (operationalState, availabilityStatus).
_HEALTH_STATES = {
    'healthy': ('enabled', 'online'),
    'suspect': ('enabled', 'degraded'),
    'offline': ('disabled', 'offline'),
}

# Attribute filters accepted on GET /resource (exact match).
FILTERABLE_ATTRIBUTES = (
    'name', 'category', 'administrativeState', 'operationalState', 'lifecycleState',
    'availabilityStatus',
)
# Always kept when ?fields= selects a subset (TMF630 attribute selection).
_MANDATORY_FIELDS = ('id', 'href', '@type')


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode('utf-8')).decode('ascii').rstrip('=')


def _unb64(value: str) -> str:
    padded = value + '=' * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode('ascii')).decode('utf-8')


def encode_resource_id(organization: str, name: str) -> str:
    """Opaque, path-segment-safe TMF id for the (organization, name) key.

    '.' is not in the base64url alphabet, so the split is unambiguous.
    """
    return f"{_b64(organization)}.{_b64(name)}"


def decode_resource_id(resource_id: str) -> Optional[Tuple[str, str]]:
    """Return (organization, name), or None when the id is not one of ours."""
    parts = (resource_id or '').split('.')
    if len(parts) != 2 or not all(parts):
        return None
    try:
        organization, name = _unb64(parts[0]), _unb64(parts[1])
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if not organization or not name or encode_resource_id(organization, name) != resource_id:
        # Reject non-canonical encodings so every resource has exactly one id.
        return None
    return organization, name


def review_states(status: Optional[str]) -> Tuple[str, str]:
    """(administrativeState, lifecycleState) for a stored review status."""
    return _REVIEW_STATES.get(normalize_status(status), ('locked', 'planned'))


def _characteristic(name: str, value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        kind, value_type = 'ObjectCharacteristic', 'object'
    elif isinstance(value, list):
        kind, value_type = 'StringArrayCharacteristic', 'array'
    else:
        kind, value_type = 'StringCharacteristic', 'string'
    return {'@type': kind, 'name': name, 'valueType': value_type, 'value': value}


def _primary_interface(card) -> Tuple[str, str]:
    interfaces = list(getattr(card, 'supported_interfaces', []) or [])
    if not interfaces:
        return '', ''
    first = interfaces[0]
    return first.url or '', first.protocol_version or ''


def health_states(health_status: Optional[str]) -> Optional[Tuple[str, str]]:
    """(operationalState, availabilityStatus) for a heartbeat status, None when unknown."""
    return _HEALTH_STATES.get(health_status)


def to_resource(record, href_base: str, *, health=None, include_owner: bool = False) -> Dict[str, Any]:
    """Build the TMF639 LogicalResource for a stored AgentRecord.

    `health` is an optional HealthState (None when heartbeat detection is off or
    the agent never reported). `include_owner` adds the owner party; only
    administrators may see who owns a card.
    """
    card = record.agent_card
    organization = card.provider.organization
    resource_id = encode_resource_id(organization, card.name)
    url, protocol_version = _primary_interface(card)
    review_status = normalize_status(getattr(record, 'status', None))
    administrative_state, lifecycle_state = review_states(review_status)

    related_party = [{
        '@type': 'RelatedPartyRefOrPartyRoleRef',
        'role': 'provider',
        'partyOrPartyRole': {'@type': 'PartyRef', 'id': organization, 'name': organization},
    }]
    owner = getattr(record, 'owner', None)
    if include_owner and owner:
        related_party.append({
            '@type': 'RelatedPartyRefOrPartyRoleRef',
            'role': 'owner',
            'partyOrPartyRole': {'@type': 'PartyRef', 'id': owner, 'name': owner},
        })

    characteristics = [
        _characteristic(CHAR_AGENT_CARD, MessageToDict(card)),
        _characteristic(CHAR_LAYER, default_layer(getattr(record, 'layer', None))),
        _characteristic(CHAR_REVIEW_STATUS, review_status),
        _characteristic(CHAR_SKILLS, [skill.name for skill in card.skills]),
        _characteristic(CHAR_TAGS, list(getattr(record, 'tags', None) or [])),
    ]
    if url:
        characteristics.append(_characteristic(CHAR_URL, url))
    if protocol_version:
        characteristics.append(_characteristic(CHAR_PROTOCOL_VERSION, protocol_version))

    resource = {
        'id': resource_id,
        'href': f"{href_base.rstrip('/')}/{resource_id}",
        '@type': 'LogicalResource',
        '@baseType': 'Resource',
        'name': card.name,
        'description': card.description,
        'resourceVersion': card.version,
        'category': CATEGORY,
        'administrativeState': administrative_state,
        'lifecycleState': lifecycle_state,
        'resourceSpecification': {
            '@type': 'ResourceSpecificationRef',
            'id': SPECIFICATION_ID,
            'name': SPECIFICATION_NAME,
            **({'version': protocol_version} if protocol_version else {}),
        },
        'relatedParty': related_party,
    }
    created_at = getattr(record, 'created_at', None)
    if created_at:
        resource['creationDate'] = created_at

    health_status = None
    if health is not None:
        health_status = getattr(health.status, 'value', health.status)
    states = health_states(health_status)
    if states:
        resource['operationalState'], resource['availabilityStatus'] = states
        characteristics.append(_characteristic(CHAR_HEALTH_STATUS, health_status))
        last_seen = getattr(health, 'last_heartbeat_at', None)
        if last_seen is not None:
            characteristics.append(_characteristic(
                CHAR_LAST_HEARTBEAT, last_seen.isoformat() if hasattr(last_seen, 'isoformat') else str(last_seen)))
    else:
        resource['unknownStatus'] = True

    resource['resourceCharacteristic'] = characteristics
    return resource


def select_fields(resource: Dict[str, Any], fields: Optional[str]) -> Dict[str, Any]:
    """Apply TMF630 ?fields= attribute selection (top-level attributes)."""
    if not fields:
        return resource
    wanted = {field.strip() for field in fields.split(',') if field.strip()}
    if wanted == {'none'}:
        wanted = set()
    return {key: value for key, value in resource.items()
            if key in wanted or key in _MANDATORY_FIELDS}


def matches_filters(resource: Dict[str, Any], filters: Dict[str, str]) -> bool:
    """Exact-match attribute filtering; a comma means OR within one attribute."""
    for attribute, expected in filters.items():
        allowed = {value.strip() for value in expected.split(',')}
        if str(resource.get(attribute, '')) not in allowed:
            return False
    return True


def characteristic_map(characteristics: Iterable[Any]) -> Dict[str, Any]:
    """name -> value for a request's resourceCharacteristic array.

    Raises ValueError for a malformed entry or a repeated name.
    """
    if characteristics is None:
        return {}
    if not isinstance(characteristics, list):
        raise ValueError('resourceCharacteristic must be an array')
    result: Dict[str, Any] = {}
    for entry in characteristics:
        if not isinstance(entry, dict) or not isinstance(entry.get('name'), str):
            raise ValueError('resourceCharacteristic entries must be objects with a name')
        name = entry['name']
        if name in result:
            raise ValueError(f"resourceCharacteristic '{name}' is repeated")
        result[name] = entry.get('value')
    return result


def tmf_error(status_code: int, reason: str, message: str = '') -> Dict[str, Any]:
    """TMF630 Error body."""
    body = {'@type': 'Error', 'code': str(status_code), 'reason': reason, 'status': str(status_code)}
    if message:
        body['message'] = message
    return body


def paginate(items: List[Any], offset: int, limit: Optional[int]) -> List[Any]:
    end = None if limit is None else offset + limit
    return items[offset:end]
