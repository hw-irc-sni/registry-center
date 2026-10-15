# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Requirement 30 (issue #83): explicit, queryable review state machine.

Covers the status policy (single point in agent_registry.status), the core
transition matrix with recorded reasons and history, the two new public
review events, rejected-card resubmission, terminal deprecation, and the
file-storage history sidecar.
"""

import json
from unittest.mock import MagicMock

import pytest
from a2a.types import AgentCard
from google.protobuf.json_format import MessageToDict

from agent_registry import status as status_policy
from agent_registry.broadcast.event_bus import EventBus
from agent_registry.broadcast.events import EventType
from agent_registry.broadcast.outbox import SqlOutbox
from agent_registry.core import RegistryCore


def card(name='agent', org='org', description='test'):
    return AgentCard(name=name, description=description, version='1',
                     provider={'organization': org, 'url': 'https://example.com'})


# ==================== status policy ====================


class TestStatusPolicy:
    def test_aliases_normalize_to_stored_values(self):
        assert status_policy.normalize_status('pending_review') == 'registered'
        assert status_policy.normalize_status('APPROVED') == 'published'
        assert status_policy.normalize_status(' Rejected ') == 'rejected'
        assert status_policy.normalize_status('') == 'published'
        assert status_policy.normalize_status(None) == 'published'

    def test_known_and_terminal(self):
        assert status_policy.is_known_status('deprecated')
        assert status_policy.is_known_status('pending_review')
        assert not status_policy.is_known_status('archived')
        assert status_policy.is_terminal_status('deprecated')
        assert not status_policy.is_terminal_status('rejected')

    def test_only_published_is_discoverable(self):
        for hidden in ('registered', 'rejected', 'deprecated'):
            assert not status_policy.is_discoverable_status(hidden)
        assert status_policy.is_discoverable_status('published')
        # legacy NULL/empty keeps the historical meaning
        assert status_policy.is_discoverable_status(None)
        assert status_policy.is_discoverable_status('')

    def test_transition_matrix(self):
        allowed = [
            ('registered', 'published'), ('registered', 'rejected'),
            ('registered', 'deprecated'), ('published', 'registered'),
            ('published', 'deprecated'), ('rejected', 'registered'),
            ('rejected', 'deprecated'),
        ]
        for prev, new in allowed:
            assert status_policy.can_transition(prev, new), f'{prev} -> {new}'
        denied = [
            ('published', 'rejected'), ('rejected', 'published'),
            ('deprecated', 'registered'), ('deprecated', 'published'),
            ('deprecated', 'rejected'),
        ]
        for prev, new in denied:
            assert not status_policy.can_transition(prev, new), f'{prev} -> {new}'

    def test_same_status_is_idempotent(self):
        for value in status_policy.KNOWN_STATUSES:
            assert status_policy.can_transition(value, value)


# ==================== core transitions (sqlite backend) ====================


@pytest.fixture
def core(tmp_path, monkeypatch):
    core = RegistryCore(use_vectordb=False, persistence_mode='sqlite',
                        persistence_conf={'sqlite.path': str(tmp_path / 'registry.db')})
    bus = EventBus(SqlOutbox(core.storage))
    monkeypatch.setattr('agent_registry.core.get_event_bus', lambda: bus)
    core._llm = MagicMock()
    yield core, bus._outbox
    core.close()


def _events_of(outbox, event_type):
    return [e for e in outbox.list_after(0, 10000) if e.event_type == event_type]


def test_register_alias_is_accepted(core):
    core, _ = core
    core.register_with_status(card(), initial_status=status_policy.normalize_status('pending_review'))
    assert core.get_status('agent', 'org') == 'registered'


def test_reject_records_reason_history_and_event(core):
    core, outbox = core
    core.register_with_status(card(), initial_status='registered')

    assert core.update_status('agent', 'org', 'rejected',
                              reason='missing input schema', operator='127.0.0.1')
    assert core.get_status('agent', 'org') == 'rejected'
    assert not status_policy.is_discoverable_status(core.get_status('agent', 'org'))

    history = core.get_status_history('agent', 'org')
    assert len(history) == 1
    assert history[0]['from_status'] == 'registered'
    assert history[0]['to_status'] == 'rejected'
    assert history[0]['reason'] == 'missing input schema'
    assert history[0]['operator'] == '127.0.0.1'
    assert history[0]['at']

    rejected_events = _events_of(outbox, EventType.AGENT_REVIEW_REJECTED)
    assert len(rejected_events) == 1
    data = rejected_events[0].data
    assert data['name'] == 'agent'
    assert data['reason'] == 'missing input schema'
    assert data['from_status'] == 'registered'
    assert data['to_status'] == 'rejected'
    # no card payload: a review event must not leak card content
    assert 'agent_card' not in data


def test_approving_a_rejected_card_is_refused(core):
    core, _ = core
    core.register_with_status(card(), initial_status='registered')
    core.update_status('agent', 'org', 'rejected', reason='bad')
    with pytest.raises(ValueError, match="Invalid status transition 'rejected' -> 'published'"):
        core.update_status('agent', 'org', 'published', reason='attempt')


def test_resubmission_via_card_update_returns_to_pending(core):
    core, outbox = core
    core.register_with_status(card(), initial_status='registered')
    core.update_status('agent', 'org', 'rejected', reason='bad schema')

    changed = card(description='fixed schema')
    assert core.update('agent', 'org',
                       MessageToDict(changed, preserving_proto_field_name=True))
    assert core.get_status('agent', 'org') == 'registered'

    history = core.get_status_history('agent', 'org')
    assert history[0]['from_status'] == 'rejected'
    assert history[0]['to_status'] == 'registered'
    assert history[0]['reason'] == 'resubmitted'

    # resubmission itself is silent (pending cards stay invisible); the later
    # approval is the first public announcement
    assert _events_of(outbox, EventType.AGENT_REGISTERED) == []
    assert core.update_status('agent', 'org', 'published', reason='ok now')
    assert len(_events_of(outbox, EventType.AGENT_REGISTERED)) == 1


def test_deprecate_is_terminal_from_any_state(core):
    core, outbox = core
    core.register(card())  # published directly
    assert core.update_status('agent', 'org', 'deprecated', reason='superseded by v2')
    assert core.get_status('agent', 'org') == 'deprecated'
    assert not status_policy.is_discoverable_status('deprecated')

    for target in ('published', 'registered', 'rejected'):
        with pytest.raises(ValueError, match='Invalid status transition'):
            core.update_status('agent', 'org', target, reason='undo')
    # deprecating twice is the only legal transition and it is a no-op
    assert core.update_status('agent', 'org', 'deprecated', reason='again') is True

    events = _events_of(outbox, EventType.AGENT_REVIEW_DEPRECATED)
    assert len(events) == 1
    assert events[0].data['reason'] == 'superseded by v2'
    assert events[0].data['from_status'] == 'published'
    assert 'agent_card' not in events[0].data


def test_unpublish_path_keeps_deregister_event(core):
    core, outbox = core
    core.register(card())
    before = outbox.max_version()
    assert core.update_status('agent', 'org', 'registered', reason='hold')
    events = outbox.list_after(before, 100)
    assert any(e.event_type == EventType.AGENT_DEREGISTERED for e in events)


def test_same_status_write_is_a_no_op_without_history_or_event(core):
    core, outbox = core
    core.register_with_status(card(), initial_status='registered')
    before = outbox.max_version()
    assert core.update_status('agent', 'org', 'registered', reason='noop') is True
    assert core.get_status_history('agent', 'org') == []
    assert outbox.max_version() == before


def test_unknown_status_value_is_rejected(core):
    core, _ = core
    core.register(card())
    with pytest.raises(ValueError, match='Invalid status'):
        core.update_status('agent', 'org', 'archived', reason='x')


def test_history_is_newest_first_and_capped(core):
    core, _ = core
    core.register_with_status(card(), initial_status='registered')
    core.update_status('agent', 'org', 'rejected', reason='r1')
    core.update_status('agent', 'org', 'registered', reason='resubmitted')
    core.update_status('agent', 'org', 'rejected', reason='r2')
    history = core.get_status_history('agent', 'org')
    assert [h['reason'] for h in history] == ['r2', 'resubmitted', 'r1']
    assert core.get_status_history('agent', 'org', limit=1)[0]['reason'] == 'r2'


def test_status_history_of_unknown_card_is_empty(core):
    core, _ = core
    assert core.get_status_history('ghost', 'org') == []


# ==================== file backend history sidecar ====================


@pytest.fixture
def file_core(tmp_path, monkeypatch):
    conf = {'file.path': str(tmp_path / 'agents.json'),
            'metadata.file': str(tmp_path / 'agentregistry.json')}
    core = RegistryCore(use_vectordb=False, persistence_mode='file', persistence_conf=conf)
    bus = MagicMock()
    monkeypatch.setattr('agent_registry.core.get_event_bus', lambda: bus)
    core._llm = MagicMock()
    yield core, conf
    core.close()


def test_file_backend_history_survives_reopen(file_core, tmp_path):
    core, conf = file_core
    core.register_with_status(card(), initial_status='registered')
    core.update_status('agent', 'org', 'rejected', reason='needs work', operator='op1')
    core.close()

    reopened = RegistryCore(use_vectordb=False, persistence_mode='file',
                            persistence_conf=dict(conf))
    try:
        assert reopened.get_status('agent', 'org') == 'rejected'
        history = reopened.get_status_history('agent', 'org')
        assert len(history) == 1
        assert history[0]['reason'] == 'needs work'
        assert history[0]['operator'] == 'op1'
    finally:
        reopened.close()


def test_file_backend_history_cleared_on_delete(file_core):
    core, _ = file_core
    core.register(card())
    core.update_status('agent', 'org', 'deprecated', reason='gone')
    assert core.deregister('agent', 'org') is True
    assert core.get_status_history('agent', 'org') == []
