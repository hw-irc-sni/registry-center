# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0

"""TMF639 hub: native event -> TMF639 event conversion, hub subscriptions in the
stores, and per-event delivery by the webhook dispatcher."""

import json

import httpx
import pytest

from agent_registry.broadcast.dispatcher import TMF639_FORMAT, TokenBucket, WebhookDispatcher
from agent_registry.broadcast.events import EventType, RegistryEvent, build_event, build_sync_required
from agent_registry.broadcast.outbox import MemoryOutbox
from agent_registry.broadcast.subscriptions import (
    FileSubscriptionStore, MemorySubscriptionStore, SqlSubscriptionStore, Subscription)
from agent_registry.persistence.sqlite_storage import SQLiteStorage
from agent_registry.tmf639 import events as tmf_events
from agent_registry.tmf639 import mapping

HREF = '/tmf-api/resourceInventoryManagement/v5/resource'
RESOURCE_ID = mapping.encode_resource_id('acme', 'RanOptimizer')

# Event payloads carry the card with proto field names (core.py).
CARD = {
    'name': 'RanOptimizer', 'description': 'Optimises RAN', 'version': '1.0.0',
    'provider': {'organization': 'acme', 'url': 'https://acme.example'},
    'supported_interfaces': [{'url': 'https://agents.acme.example/ran', 'protocol_version': '1.0.0'}],
    'skills': [{'id': 's1', 'name': 'tune-cells', 'description': 'Tune cells'}],
}


def _event(event_type, **data):
    payload = {'name': 'RanOptimizer', 'organization': 'acme', 'tags': ['ran'], 'discovery_public': True}
    payload.update(data)
    return build_event(event_type, payload, 7)


def _chars(resource):
    return {entry['name']: entry['value'] for entry in resource['resourceCharacteristic']}


# ---------- hub query ----------

@pytest.mark.parametrize('query, expected', [
    (None, ['AGENT_DEREGISTERED', 'AGENT_HEALTH_CHANGED', 'AGENT_REGISTERED',
            'AGENT_REVIEW_DEPRECATED', 'AGENT_UPDATED']),
    ('', ['AGENT_DEREGISTERED', 'AGENT_HEALTH_CHANGED', 'AGENT_REGISTERED',
          'AGENT_REVIEW_DEPRECATED', 'AGENT_UPDATED']),
    ('eventType=ResourceCreateEvent', ['AGENT_REGISTERED']),
    ('eventType=ResourceCreateEvent,ResourceDeleteEvent', ['AGENT_DEREGISTERED', 'AGENT_REGISTERED']),
    ('eventType=ResourceDeleteEvent&eventType=ResourceStateChangeEvent',
     ['AGENT_DEREGISTERED', 'AGENT_HEALTH_CHANGED', 'AGENT_REVIEW_DEPRECATED']),
])
def test_hub_query_selects_native_event_types(query, expected):
    assert tmf_events.native_event_types(query) == expected


@pytest.mark.parametrize('query', [
    'eventType=ResourceBogusEvent', 'eventType=', 'name=RanOptimizer',
    'eventType=ResourceCreateEvent&event.resource.name=x', 'garbage',
])
def test_hub_query_rejects_what_it_cannot_honour(query):
    with pytest.raises(ValueError):
        tmf_events.native_event_types(query)


def test_no_hub_query_ever_matches_rejections_or_sync_signals():
    every = tmf_events.native_event_types(None)
    assert 'AGENT_REVIEW_REJECTED' not in every and 'SYNC_REQUIRED' not in every


# ---------- event conversion ----------

def test_registered_card_becomes_a_create_event_with_the_card_snapshot():
    event = _event(EventType.AGENT_REGISTERED, agent_card=CARD, layer='network')
    body = tmf_events.to_tmf_event(event, 'https://rc.example' + HREF)
    assert body['@type'] == body['eventType'] == 'ResourceCreateEvent'
    assert body['eventId'] == event.event_id and body['eventTime'] == event.timestamp
    resource = body['event']['resource']
    assert resource['id'] == RESOURCE_ID
    assert resource['href'] == f'https://rc.example{HREF}/{RESOURCE_ID}'
    assert resource['administrativeState'] == 'unlocked'
    assert resource['lifecycleState'] == 'installed'
    assert [party['role'] for party in resource['relatedParty']] == ['provider']  # never the owner
    assert 'unknownStatus' not in resource
    chars = _chars(resource)
    assert chars['url'] == 'https://agents.acme.example/ran'
    assert chars['layer'] == 'network' and chars['tags'] == ['ran']


def test_update_without_layer_does_not_announce_a_default_layer():
    body = tmf_events.to_tmf_event(_event(EventType.AGENT_UPDATED, agent_card=CARD), HREF)
    assert body['eventType'] == 'ResourceAttributeValueChangeEvent'
    assert 'layer' not in _chars(body['event']['resource'])


def test_unparseable_card_falls_back_to_a_reference():
    body = tmf_events.to_tmf_event(_event(EventType.AGENT_UPDATED, agent_card={'skills': 'x'}), HREF)
    assert body['event']['resource'] == {'id': RESOURCE_ID, 'href': f'{HREF}/{RESOURCE_ID}',
                                         '@type': 'LogicalResource', 'name': 'RanOptimizer'}


def test_deregistration_is_a_delete_event_with_a_reference_only():
    body = tmf_events.to_tmf_event(_event(EventType.AGENT_DEREGISTERED), HREF)
    assert body['eventType'] == 'ResourceDeleteEvent'
    assert body['event']['resource'] == {'id': RESOURCE_ID, 'href': f'{HREF}/{RESOURCE_ID}',
                                         '@type': 'LogicalResource', 'name': 'RanOptimizer'}


def test_deprecating_a_published_card_is_a_state_change():
    event = _event(EventType.AGENT_REVIEW_DEPRECATED, reason='eol', from_status='published',
                   to_status='deprecated')
    resource = tmf_events.to_tmf_event(event, HREF)['event']['resource']
    assert resource['administrativeState'] == 'shuttingDown'
    assert resource['lifecycleState'] == 'pendingRemoval'
    assert 'reason' not in json.dumps(resource)


@pytest.mark.parametrize('from_status', ['registered', 'rejected', None])
def test_deprecating_a_card_that_was_never_public_is_not_announced(from_status):
    event = _event(EventType.AGENT_REVIEW_DEPRECATED, from_status=from_status, to_status='deprecated')
    assert tmf_events.to_tmf_event(event, HREF) is None


@pytest.mark.parametrize('health, operational, availability', [
    ('healthy', 'enabled', 'online'), ('suspect', 'enabled', 'degraded'), ('offline', 'disabled', 'offline'),
])
def test_health_change_is_a_state_change(health, operational, availability):
    body = tmf_events.to_tmf_event(_event(EventType.AGENT_HEALTH_CHANGED, health_status=health), HREF)
    resource = body['event']['resource']
    assert body['eventType'] == 'ResourceStateChangeEvent'
    assert (resource['operationalState'], resource['availabilityStatus']) == (operational, availability)


@pytest.mark.parametrize('event', [
    _event(EventType.AGENT_REVIEW_REJECTED, from_status='registered', to_status='rejected'),
    build_sync_required(3),
    # legacy outbox payload whose visibility was never recorded
    RegistryEvent('e1', EventType.AGENT_REGISTERED, 't', 1, {'name': 'n', 'organization': 'o',
                                                              'agent_card': CARD}),
])
def test_events_that_are_not_forwarded(event):
    assert tmf_events.to_tmf_event(event, HREF) is None


def test_event_hrefs_are_absolute_only_with_a_public_base_url():
    assert tmf_events.public_href_base({}) == HREF
    assert tmf_events.public_href_base({'registry.public.base_url': 'https://rc.example/'}) == \
        'https://rc.example' + HREF


# ---------- hub subscriptions in the stores ----------

@pytest.fixture(params=['memory', 'file', 'sqlite'])
def store_factory(request, tmp_path):
    storage = None
    if request.param == 'sqlite':
        storage = SQLiteStorage.init({'sqlite.path': str(tmp_path / 'registry.db')})

    def build():
        if request.param == 'memory':
            return build.memory
        if request.param == 'file':
            return FileSubscriptionStore(str(tmp_path / 'subscriptions.json'))
        return SqlSubscriptionStore(storage)

    build.memory = MemorySubscriptionStore()
    yield build
    if storage is not None:
        storage.close()


def test_hub_format_and_query_survive_a_reload(store_factory):
    created = store_factory().create(Subscription(
        '', 'https://hooks.example/tmf', event_types=['AGENT_REGISTERED'],
        format=TMF639_FORMAT, query='eventType=ResourceCreateEvent', creator='partner-a'))
    native = store_factory().create(Subscription('', 'https://hooks.example/native'))
    reloaded = store_factory()
    hub = reloaded.get(created.subscription_id)
    assert (hub.format, hub.query, hub.event_types, hub.creator) == (
        TMF639_FORMAT, 'eventType=ResourceCreateEvent', ['AGENT_REGISTERED'], 'partner-a')
    plain = reloaded.get(native.subscription_id)
    assert plain.format is None and plain.query is None
    assert plain.to_dict()['filters'] == {'organizations': None, 'tags': None}


# ---------- dispatcher ----------

def _dispatcher(monkeypatch, handler, store, outbox):
    monkeypatch.setattr('agent_registry.broadcast.callback_policy.get_conf',
                        lambda: {'broadcast.callback.allowlist': 'hooks.example'})
    monkeypatch.setattr('agent_registry.broadcast.dispatcher.get_conf',
                        lambda: {'registry.public.base_url': 'https://rc.example'})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WebhookDispatcher(store, outbox, client=client, debounce_window=0.01,
                             max_events_per_second=1000.0, max_retries=0,
                             backoff_base=0.01, backoff_max=0.02, webhook_timeout=1.0)


def _hub(store, secret=None):
    return store.create(Subscription('', 'https://hooks.example/tmf', format=TMF639_FORMAT,
                                     event_types=tmf_events.native_event_types(None), secret=secret))


@pytest.mark.asyncio
async def test_hub_receives_one_signed_tmf_event_per_post(monkeypatch):
    posts = []

    def handler(request):
        posts.append(request)
        return httpx.Response(201)

    store, outbox = MemorySubscriptionStore(), MemoryOutbox()
    hub = _hub(store, secret='s3cret')
    dispatcher = _dispatcher(monkeypatch, handler, store, outbox)
    dispatcher.add_subscription(hub)
    created = outbox.append(_event(EventType.AGENT_REGISTERED, agent_card=CARD))
    removed = outbox.append(_event(EventType.AGENT_DEREGISTERED))
    rejected = outbox.append(_event(EventType.AGENT_REVIEW_REJECTED, from_status='registered'))
    for event in (created, removed, rejected):
        dispatcher.submit(event)

    # The hub's event types exclude rejections: no delivery is even owed.
    assert outbox.delivery_status(rejected.event_id, hub.subscription_id) != 'pending'
    dispatcher._flush_buffers()
    await dispatcher._process_batch(hub.subscription_id,
                                    dispatcher._workers[hub.subscription_id].queue.get_nowait())

    bodies = [json.loads(request.content) for request in posts]
    assert [body['eventType'] for body in bodies] == ['ResourceCreateEvent', 'ResourceDeleteEvent']
    assert bodies[0]['event']['resource']['href'] == f'https://rc.example{HREF}/{RESOURCE_ID}'
    assert all(str(request.url) == 'https://hooks.example/tmf' for request in posts)
    assert all(request.headers['X-Registry-Signature'].startswith('sha256=') for request in posts)
    assert [request.headers['X-Registry-Event-Id'] for request in posts] == [created.event_id, removed.event_id]
    for event in (created, removed):
        assert outbox.delivery_status(event.event_id, hub.subscription_id) == 'dispatched'
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_unforwarded_events_are_settled_without_a_post(monkeypatch):
    posts = []
    store, outbox = MemorySubscriptionStore(), MemoryOutbox()
    hub = _hub(store)
    dispatcher = _dispatcher(monkeypatch, lambda r: posts.append(r) or httpx.Response(200), store, outbox)
    dispatcher.add_subscription(hub)
    never_public = outbox.append(_event(EventType.AGENT_REVIEW_DEPRECATED, from_status='registered'))
    dispatcher.submit(never_public)
    await dispatcher._deliver_batch(hub.subscription_id, [never_public])
    assert posts == []
    assert outbox.delivery_status(never_public.event_id, hub.subscription_id) == 'dispatched'
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_rate_limited_hub_events_stay_pending_instead_of_degrading(monkeypatch):
    posts = []
    store, outbox = MemorySubscriptionStore(), MemoryOutbox()
    hub = _hub(store)
    dispatcher = _dispatcher(monkeypatch, lambda r: posts.append(r) or httpx.Response(200), store, outbox)
    dispatcher.add_subscription(hub)
    batch = [outbox.append(_event(EventType.AGENT_HEALTH_CHANGED, name=f'a{i}', health_status='healthy'))
             for i in range(3)]
    for event in batch:
        dispatcher.submit(event)
    bucket = TokenBucket(rate=0.1, capacity=1.0)
    await dispatcher._deliver_tmf639(hub, batch, bucket)
    assert len(posts) == 1
    assert json.loads(posts[0].content)['eventType'] == 'ResourceStateChangeEvent'  # never SYNC_REQUIRED
    assert [outbox.delivery_status(e.event_id, hub.subscription_id) for e in batch] == [
        'dispatched', 'pending', 'pending']
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_failed_hub_delivery_stops_the_batch(monkeypatch):
    posts = []
    store, outbox = MemorySubscriptionStore(), MemoryOutbox()
    hub = _hub(store)
    dispatcher = _dispatcher(monkeypatch, lambda r: posts.append(r) or httpx.Response(503), store, outbox)
    dispatcher.add_subscription(hub)
    batch = [outbox.append(_event(EventType.AGENT_DEREGISTERED, name=f'a{i}')) for i in range(2)]
    for event in batch:
        dispatcher.submit(event)
    await dispatcher._deliver_batch(hub.subscription_id, batch)
    assert len(posts) == 1
    assert [outbox.delivery_status(e.event_id, hub.subscription_id) for e in batch] == [
        'delivery_failed', 'pending']
    await dispatcher.stop()
