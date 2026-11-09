# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0

"""Pure conversion of Registry Center change events into TMF639 v5 events.

The TMF639 hub is a view of the native change broadcast: a hub subscription is
an ordinary Subscription with format 'tmf639', and the webhook dispatcher calls
`to_tmf_event` for each event it delivers to one. Native events track
*discoverability*, so the hub announces changes to the published inventory
only (see the event table in the API reference).

No FastAPI, registry or broadcast-service imports here: the dispatcher imports
this module lazily, and anything heavier would pull agent_registry.core in from
inside the broadcast package.
"""

from typing import Any, Dict, FrozenSet, List, Optional
from urllib.parse import parse_qs

from a2a.types import AgentCard
from google.protobuf.json_format import ParseDict, ParseError

from agent_registry.broadcast.events import EventType, RegistryEvent, public_event
from agent_registry.model.agent_layer import UNKNOWN_LAYER
from agent_registry.persistence.base import AgentRecord
from agent_registry.status import DEPRECATED_STATUS, DISCOVERABLE_STATUS, is_discoverable_status
from agent_registry.tmf639 import mapping

SUBSCRIPTION_FORMAT = 'tmf639'
HUB_PATH = mapping.API_BASE_PATH + '/hub'

CREATE_EVENT = 'ResourceCreateEvent'
ATTRIBUTE_VALUE_CHANGE_EVENT = 'ResourceAttributeValueChangeEvent'
STATE_CHANGE_EVENT = 'ResourceStateChangeEvent'
DELETE_EVENT = 'ResourceDeleteEvent'

# TMF639 event type -> the native events it is built from. AGENT_REVIEW_REJECTED
# (the card was never public) and SYNC_REQUIRED (TMF639 has no change cursor)
# are never forwarded, so no hub subscription ever matches them.
TMF_TO_NATIVE: Dict[str, FrozenSet[EventType]] = {
    CREATE_EVENT: frozenset({EventType.AGENT_REGISTERED}),
    ATTRIBUTE_VALUE_CHANGE_EVENT: frozenset({EventType.AGENT_UPDATED}),
    STATE_CHANGE_EVENT: frozenset({EventType.AGENT_REVIEW_DEPRECATED, EventType.AGENT_HEALTH_CHANGED}),
    DELETE_EVENT: frozenset({EventType.AGENT_DEREGISTERED}),
}
_NATIVE_TO_TMF = {native: tmf for tmf, natives in TMF_TO_NATIVE.items() for native in natives}


def native_event_types(query: Optional[str]) -> List[str]:
    """Native event types a hub `query` subscribes to.

    Only `eventType=<TMF639 event>[,<event>...]` is supported (repeating the
    key works too); an empty query means every forwarded event. Raises
    ValueError for anything else, so a filter the hub cannot honour is refused
    instead of silently widened.
    """
    wanted = set(TMF_TO_NATIVE)
    if query and query.strip():
        try:
            params = parse_qs(query.strip(), keep_blank_values=True, strict_parsing=True)
        except ValueError:
            raise ValueError("query must be of the form 'eventType=<event>[,<event>...]'") from None
        unsupported = sorted(set(params) - {'eventType'})
        if unsupported:
            raise ValueError(f"Unsupported hub query attribute(s): {', '.join(unsupported)}; "
                             "only eventType is supported")
        requested = {name.strip() for value in params.get('eventType', [])
                     for name in value.split(',') if name.strip()}
        unknown = sorted(requested - set(TMF_TO_NATIVE))
        if unknown or not requested:
            raise ValueError(f"Unknown eventType {', '.join(unknown) or '(empty)'}; expected one of "
                             f"{', '.join(TMF_TO_NATIVE)}")
        wanted = requested
    return sorted(native.value for name in wanted for native in TMF_TO_NATIVE[name])


def public_href_base(config: Dict[str, Any]) -> str:
    """Resource href base for events (no request to take the host from).

    `registry.public.base_url` makes hrefs absolute; without it they are
    server-relative paths.
    """
    base = str(config.get('registry.public.base_url', '') or '').strip().rstrip('/')
    return base + mapping.RESOURCE_PATH


def _reference(data: Dict[str, Any], href_base: str) -> Dict[str, Any]:
    resource_id = mapping.encode_resource_id(data.get('organization', ''), data.get('name', ''))
    return {'id': resource_id, 'href': f"{href_base.rstrip('/')}/{resource_id}",
            '@type': 'LogicalResource', 'name': data.get('name', '')}


def _snapshot_resource(data: Dict[str, Any], href_base: str) -> Dict[str, Any]:
    """Full resource from the card the event carries (not a re-read: the card
    may have changed or gone by the time the event is delivered)."""
    card_data = data.get('agent_card')
    if not isinstance(card_data, dict):
        return _reference(data, href_base)
    try:
        card = ParseDict(card_data, AgentCard(), ignore_unknown_fields=True)
    except (ParseError, TypeError, ValueError):
        return _reference(data, href_base)
    layer = data.get('layer')
    record = AgentRecord(agent_card=card, status=DISCOVERABLE_STATUS,
                         tags=list(data.get('tags') or []),
                         layer=layer if isinstance(layer, str) else UNKNOWN_LAYER)
    try:
        # Owner is never part of an event: subscribers are not administrators.
        resource = mapping.to_resource(record, href_base, include_owner=False)
    except (AttributeError, TypeError, ValueError):
        # A payload the mapping cannot read must not wedge the delivery queue.
        return _reference(data, href_base)
    # Health is not part of a card change, so do not claim it is unknown.
    resource.pop('unknownStatus', None)
    if not isinstance(layer, str):
        # Some updates (tag changes) do not carry the layer; omit it rather
        # than announce the default.
        resource['resourceCharacteristic'] = [
            entry for entry in resource['resourceCharacteristic']
            if entry['name'] != mapping.CHAR_LAYER]
    return resource


def _event_resource(event: RegistryEvent, href_base: str) -> Optional[Dict[str, Any]]:
    data = event.data
    if event.event_type in (EventType.AGENT_REGISTERED, EventType.AGENT_UPDATED):
        return _snapshot_resource(data, href_base)
    if event.event_type == EventType.AGENT_DEREGISTERED:
        return _reference(data, href_base)
    if event.event_type == EventType.AGENT_REVIEW_DEPRECATED:
        # The native event is not discoverability-gated: deprecating a card
        # that was never published must not announce it here.
        # A missing from_status (never written by core) is treated as not public.
        if not data.get('from_status') or not is_discoverable_status(data['from_status']):
            return None
        administrative_state, lifecycle_state = mapping.review_states(DEPRECATED_STATUS)
        return {**_reference(data, href_base), 'administrativeState': administrative_state,
                'lifecycleState': lifecycle_state}
    if event.event_type == EventType.AGENT_HEALTH_CHANGED:
        resource = _reference(data, href_base)
        states = mapping.health_states(data.get('health_status'))
        if states:
            resource['operationalState'], resource['availabilityStatus'] = states
        else:
            resource['unknownStatus'] = True
        return resource
    return None


def to_tmf_event(event: RegistryEvent, href_base: str) -> Optional[Dict[str, Any]]:
    """The TMF639 event for a native event, or None when it is not forwarded."""
    event = public_event(event)  # legacy payloads of unknown visibility become SYNC_REQUIRED
    event_type = _NATIVE_TO_TMF.get(event.event_type)
    if event_type is None:
        return None
    resource = _event_resource(event, href_base)
    if resource is None:
        return None
    return {
        '@type': event_type,
        '@baseType': 'Event',
        'eventId': event.event_id,
        'eventTime': event.timestamp,
        'eventType': event_type,
        'title': event_type,
        'domain': 'ResourceInventory',
        'event': {'resource': resource},
    }


def to_hub(subscription) -> Dict[str, Any]:
    """TMF639 Hub (EventSubscription) representation; the secret is never returned."""
    hub = {'@type': 'Hub', 'id': subscription.subscription_id, 'callback': subscription.callback_url}
    if subscription.query is not None:
        hub['query'] = subscription.query
    return hub
