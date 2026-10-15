# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
Card status policy.

One definition of "discoverable", shared by the persistence layer, the registry
core and every public read/change surface, so pending ('registered') cards
cannot leak through one path while another hides them.

Review state machine (Requirement 30 / issue #83):

    registered  --approve-->        published
    registered  --reject-->         rejected   (reason required)
    registered  --deprecate-->      deprecated (reason required)
    published   --unpublish-->      registered
    published   --deprecate-->      deprecated (reason required)
    rejected    --resubmit-->       registered (card update by the owner)
    rejected    --deprecate-->      deprecated (reason required)
    deprecated  (terminal; irrecoverable — republish as a new submission)

Stored values stay lowercase legacy strings ('registered' / 'published') so
existing rows and clients keep working; the API-facing canonical names
(PENDING_REVIEW / APPROVED) are accepted as aliases on write via
``STATUS_ALIASES``.
"""

from typing import Optional

#: Only cards in this status are visible to discovery consumers.
DISCOVERABLE_STATUS = 'published'
#: Cards awaiting approval; stored, but not discoverable.
PENDING_STATUS = 'registered'
#: Card failed review with recorded feedback; discoverability blocked.
REJECTED_STATUS = 'rejected'
#: Card withdrawn from discovery permanently; retained for audit only.
DEPRECATED_STATUS = 'deprecated'

#: All statuses a stored record may carry (legacy NULL handled separately).
KNOWN_STATUSES = (DISCOVERABLE_STATUS, PENDING_STATUS, REJECTED_STATUS, DEPRECATED_STATUS)

#: API-facing canonical names mapped onto the stored values. Write paths accept
#: them so clients can speak the Requirement-30 vocabulary; storage keeps the
#: legacy lowercase values.
STATUS_ALIASES = {
    'pending_review': PENDING_STATUS,
    'approved': DISCOVERABLE_STATUS,
}

#: Allowed status transitions. ``deprecated`` is terminal; ``rejected`` may
#: only be left by resubmission (back to pending) or deprecation.
REVIEW_TRANSITIONS = {
    PENDING_STATUS: frozenset({DISCOVERABLE_STATUS, REJECTED_STATUS, DEPRECATED_STATUS}),
    DISCOVERABLE_STATUS: frozenset({PENDING_STATUS, DEPRECATED_STATUS}),
    REJECTED_STATUS: frozenset({PENDING_STATUS, DEPRECATED_STATUS}),
    DEPRECATED_STATUS: frozenset(),
}


def normalize_status(value: Optional[str]) -> str:
    """Map a client-supplied status onto its stored value.

    Case-insensitive, alias-aware; a missing value falls back to the legacy
    published default so pre-status records keep their historical meaning.
    Unknown values are returned lowercased unchanged — callers validate.
    """
    lowered = (value or '').strip().lower()
    if not lowered:
        return DISCOVERABLE_STATUS
    return STATUS_ALIASES.get(lowered, lowered)


def is_known_status(value: Optional[str]) -> bool:
    """Whether the value is a storable status (aliases normalized first)."""
    return normalize_status(value) in KNOWN_STATUSES


def is_terminal_status(value: Optional[str]) -> bool:
    """Whether the normalized status leaves the state machine permanently."""
    return normalize_status(value) == DEPRECATED_STATUS


def can_transition(previous: Optional[str], new_status: Optional[str]) -> bool:
    """Whether the state machine allows moving from ``previous`` to ``new_status``.

    A same-status write is a no-op and always allowed (idempotent API calls);
    an unknown previous status is treated as pending, matching discovery.
    """
    source = normalize_status(previous) if (previous or '').strip() else PENDING_STATUS
    target = normalize_status(new_status)
    if source == target:
        return True
    return target in REVIEW_TRANSITIONS.get(source, frozenset())


def is_discoverable_status(status: Optional[str]) -> bool:
    """Whether a card status is visible to discovery consumers.

    A missing status (records written before status tracking existed) counts as
    discoverable, matching the historical behaviour of every read path.
    Only the published status passes: pending, rejected and deprecated cards
    are all invisible.
    """
    normalized = normalize_status(status) if (status or '').strip() else DISCOVERABLE_STATUS
    return normalized == DISCOVERABLE_STATUS
