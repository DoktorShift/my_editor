# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Draft deletions announced as NIP-09 requests, read and sent.

EINUNDZWANZIG STANDUP deletes a draft with a kind 5 request naming its
address instead of a blanked wrap. A draft deleted there must leave
this app's drafts list (and stay gone when a refresh brings its old wrap
back), a draft saved after the request must stay, and a request a relay
made up must change nothing. Deleting an imported draft here sends one
too, so STANDUP never imports that post again.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtCore import QObject, Signal

from nostr import events
from nostr.draft_deletions import (
    DraftDeletions,
    build_deletion_request,
    deleted_identifiers,
    draft_address,
)
from nostr.draft_store import DraftStore
from nostr.drafts import DraftWrapMeta, INNER_KIND_LONG_FORM
from nostr.publisher import DraftBulkDeleteJob
from tests.outbox_fakes import OTHER_SK, PK, SK, FakeRelayDirectory, Profile, settle

D = "rss-0123456789abcdef"


def request(identifier=D, created_at=200, sk=SK, pubkey=PK):
    unsigned = build_deletion_request(pubkey_hex=pubkey, identifier=identifier,
                                      wrap_id="f" * 64, created_at=created_at)
    return events.sign_event(unsigned, sk)


def skeleton(created_at, identifier=D):
    return DraftWrapMeta(identifier=identifier, inner_kind=INNER_KIND_LONG_FORM,
                         event_id=f"w{created_at}", pubkey=PK, created_at=created_at,
                         expiration=None, ciphertext="ct")


class FakeSubscription(QObject):
    event = Signal(dict)

    def close(self):
        pass


class FakePool:
    def __init__(self):
        self.filters = []
        self.subscription = FakeSubscription()

    def subscribe(self, relays, filters):
        self.filters.append((list(relays), filters))
        return self.subscription


def watcher():
    store = DraftStore()
    store.bind_profile(PK)
    pool = FakePool()
    deletions = DraftDeletions(relay_pool=pool, relay_directory=FakeRelayDirectory(),
                               store=store)
    deletions.start_for(Profile())
    settle()
    return deletions, store, pool


def test_request_shape():
    unsigned = build_deletion_request(pubkey_hex=PK, identifier=D, wrap_id="e" * 64,
                                      created_at=5)
    assert unsigned["kind"] == 5
    assert unsigned["content"] == ""
    assert unsigned["tags"] == [["a", f"31234:{PK}:{D}"], ["e", "e" * 64], ["k", "31234"]]
    assert draft_address(PK.upper(), D) == f"31234:{PK}:{D}"


def test_only_a_request_signed_by_the_account_counts():
    assert deleted_identifiers(request(), PK) == {D: 200}
    forged = dict(request())
    forged["sig"] = "0" * 128
    assert deleted_identifiers(forged, PK) == {}
    stranger = request(sk=OTHER_SK, pubkey=events.sign_event(
        {"kind": 1, "content": "", "tags": [], "created_at": 1}, OTHER_SK)["pubkey"])
    assert deleted_identifiers(stranger, PK) == {}


def test_it_subscribes_to_the_accounts_draft_deletions():
    _deletions, _store, pool = watcher()
    relays, filters = pool.filters[0]
    assert relays
    assert filters == [{"kinds": [5], "authors": [PK], "#k": ["31234"]}]


def test_a_request_removes_an_older_draft():
    deletions, store, pool = watcher()
    store.upsert_skeleton(skeleton(100))
    removed = []
    deletions.removed.connect(removed.append)
    pool.subscription.event.emit(request(created_at=200))
    assert store.get(D) is None
    assert removed == [D]
    assert deletions.deleted_at(D) == 200


def test_a_draft_saved_after_the_request_stays():
    deletions, store, pool = watcher()
    store.upsert_skeleton(skeleton(300))
    pool.subscription.event.emit(request(created_at=200))
    assert store.get(D) is not None


def test_the_old_wrap_coming_back_is_removed_again():
    deletions, store, pool = watcher()
    pool.subscription.event.emit(request(created_at=200))
    store.upsert_skeleton(skeleton(100))
    assert store.get(D) is None


def test_a_forged_request_changes_nothing():
    deletions, store, pool = watcher()
    store.upsert_skeleton(skeleton(100))
    forged = dict(request())
    forged["sig"] = "0" * 128
    pool.subscription.event.emit(forged)
    assert store.get(D) is not None


def test_deleting_an_imported_draft_also_sends_a_request():
    signed, published = [], []

    def sign(unsigned, on_success, on_failure):
        signed.append(unsigned["kind"])
        on_success(events.sign_event(dict(unsigned), SK))

    client = MagicMock()
    client.sign_event.side_effect = sign
    sessions = MagicMock()
    sessions.get.side_effect = lambda profile, on_ready, on_error: on_ready(client)
    pool = MagicMock()

    def publish(relays, event, **_kw):
        published.append(event)
        job = MagicMock()
        job.all_done.connect.side_effect = lambda cb: cb([("wss://r/", True, "")])
        return job

    pool.publish.side_effect = publish
    job = DraftBulkDeleteJob(
        relay_pool=pool, relay_directory=FakeRelayDirectory(), session_pool=sessions,
        profile=Profile(), targets=[(D, INNER_KIND_LONG_FORM), ("note-1", 1)],
        announce={D: "e" * 64})
    finished = []
    job.finished.connect(lambda deleted, failures: finished.append(deleted))
    job.start()
    settle()
    # Tombstone, request (imported draft), then the note's tombstone alone.
    assert signed == [31234, 5, 31234]
    kinds = [event["kind"] for event in published]
    assert kinds == [5, 31234, 31234]
    assert ["a", f"31234:{PK}:{D}"] in published[0]["tags"]
    assert ["e", "e" * 64] in published[0]["tags"]
    assert finished == [2]
