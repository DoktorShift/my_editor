# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Expired drafts stay gone (NIP-40).

Drafts carry an expiration (90 days; imported drafts keep it too).
Relays that honour NIP-40 drop them, but one that does not would hand
the app an expired wrap, on the first fetch or later, which would show
up again or replace what the store holds. Neither may happen; a wrap
whose expiration tag is not a number has no expiration.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nostr import events
from nostr.draft_store import DraftStore
from nostr.draft_sync import DraftSync
from nostr.drafts import DraftWrapMeta, parse_wrap_event
from tests.outbox_fakes import PK, SK

NOW = 1_800_000_000


def wrap(identifier="d1", *, created_at=NOW - 100, expiration=None, content="ct"):
    tags = [["d", identifier], ["k", "30023"]]
    if expiration is not None:
        tags.append(["expiration", str(expiration)])
    return events.sign_event({"kind": 31234, "content": content, "tags": tags,
                              "created_at": created_at}, SK)


def sync_with_store():
    store = DraftStore(clock=lambda: NOW)
    store.bind_profile(PK)
    sync = DraftSync(relay_pool=MagicMock(), relay_directory=MagicMock(),
                     session_pool=MagicMock(), store=store, clock=lambda: NOW)
    profile = MagicMock()
    profile.user_pubkey = PK
    sync._profile = profile
    sync._bunker = MagicMock()
    return sync, store


def test_an_expired_wrap_is_ignored():
    sync, store = sync_with_store()
    sync._on_wrap_event(wrap(expiration=NOW - 1))
    assert store.get("d1") is None
    sync._bunker.nip44_decrypt_self.assert_not_called()


def test_a_wrap_expiring_later_is_shown():
    sync, store = sync_with_store()
    sync._on_wrap_event(wrap(expiration=NOW + 3600))
    assert store.get("d1") is not None


def test_an_expired_wrap_never_replaces_the_stored_version():
    sync, store = sync_with_store()
    sync._on_wrap_event(wrap(created_at=NOW - 100, expiration=NOW + 3600))
    first = store.get("d1").event_id
    sync._on_wrap_event(wrap(created_at=NOW - 50, expiration=NOW - 10, content="newer"))
    assert store.get("d1").event_id == first


def test_an_expired_deletion_deletes_nothing():
    sync, store = sync_with_store()
    sync._on_wrap_event(wrap(created_at=NOW - 100, expiration=NOW + 3600))
    sync._on_wrap_event(wrap(created_at=NOW - 50, expiration=NOW - 10, content=""))
    assert store.get("d1") is not None


def test_a_malformed_expiration_is_no_expiration():
    event = events.sign_event({"kind": 31234, "content": "ct", "created_at": NOW - 100,
                               "tags": [["d", "d1"], ["expiration", "soon"]]}, SK)
    assert parse_wrap_event(event).expiration is None
    sync, store = sync_with_store()
    sync._on_wrap_event(event)
    assert store.get("d1") is not None


def test_the_store_itself_refuses_an_expired_wrap():
    store = DraftStore(clock=lambda: NOW)
    store.bind_profile(PK)
    store.upsert_skeleton(DraftWrapMeta("d1", 30023, "e1", PK, NOW - 100, NOW, "ct"))
    assert store.get("d1") is None
    store.upsert_skeleton(DraftWrapMeta("d1", 30023, "e2", PK, NOW - 100, NOW + 1, "ct"))
    assert store.get("d1") is not None
