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

import pytest

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
    assert filters == [{"kinds": [5], "authors": [PK], "#k": ["31234"]},
                       {"kinds": [5], "authors": [PK], "limit": 500}]


def test_a_request_removes_an_older_draft():
    deletions, store, pool = watcher()
    store.upsert_skeleton(skeleton(100))
    removed = []
    deletions.removed.connect(removed.append)
    pool.subscription.event.emit(request(created_at=200))
    assert store.get(D) is None
    assert removed == [D]
    assert D in store.deleted_identifiers()


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
    # The tombstone goes out first, then the request (engine review L3).
    assert kinds == [31234, 5, 31234]
    tombstone, request = published[0], published[1]
    assert ["a", f"31234:{PK}:{D}"] in request["tags"]
    assert ["e", "e" * 64] in request["tags"]
    # One second before the tombstone: a relay that follows NIP-09 keeps it.
    assert request["created_at"] == tombstone["created_at"] - 1
    assert finished == [2]


@pytest.mark.parametrize("request_id", ["0" * 64, "f" * 64])
def test_a_request_in_the_same_second_deletes_the_draft(request_id):
    """Engine review L1: NIP-09 deletes every version up to the request's
    time; of four same-second requests, only those with the lower id
    deleted the draft."""
    from nostr.draft_store import DraftStore
    from nostr.drafts import DraftWrapMeta
    store = DraftStore()
    store.bind_profile(PK)
    store.upsert_skeleton(DraftWrapMeta("d1", 30023, "8" * 64, PK, 1000, None, "ct"))
    assert store.apply_deletion("d1", 1000, request_id, request=True)
    assert store.get("d1") is None
    # The same second's copy from another relay stays out; a later save is new.
    store.upsert_skeleton(DraftWrapMeta("d1", 30023, "9" * 64, PK, 1000, None, "ct"))
    assert store.get("d1") is None
    store.upsert_skeleton(DraftWrapMeta("d1", 30023, "a" * 64, PK, 1001, None, "ct"))
    assert store.get("d1") is not None


# --------------------------------------------------------------------- #
# Where the requests are followed, and which (engine review L13)         #
# --------------------------------------------------------------------- #

class ClosingSubscription(FakeSubscription):
    def __init__(self):
        super().__init__()
        self.closed = False

    def close(self):
        self.closed = True


class RoutingPool:
    def __init__(self):
        self.opened = []        # (relays, filters, subscription)

    def subscribe(self, relays, filters):
        subscription = ClosingSubscription()
        self.opened.append((list(relays), filters, subscription))
        return subscription


def routed(entitled=None):
    store = DraftStore()
    store.bind_profile(PK)
    pool = RoutingPool()
    directory = FakeRelayDirectory()
    directory.draft_lists[PK] = ["wss://drafts-one.example"]
    deletions = DraftDeletions(relay_pool=pool, relay_directory=directory, store=store,
                               entitled_relays=entitled)
    deletions.start_for(Profile())
    settle()
    return deletions, store, pool, directory


def test_a_request_without_the_k_tag_counts_too():
    from nostr.deletion import build_deletion
    deletions, store, pool = watcher()
    store.upsert_skeleton(skeleton(100))
    unsigned = build_deletion(PK, addresses=[draft_address(PK, D)], created_at=200)
    assert not any(tag[0] == "k" for tag in unsigned["tags"])
    pool.subscription.event.emit(events.sign_event(unsigned, SK))
    assert store.get(D) is None


def test_a_changed_relay_list_moves_the_requests():
    deletions, _store, pool, directory = routed()
    (relays, _filters, first), = pool.opened
    assert "wss://drafts-one.example" in relays
    directory.draft_lists[PK] = ["wss://drafts-two.example"]
    directory.changed.emit(PK)
    settle()
    assert first.closed
    moved = pool.opened[-1][0]
    assert "wss://drafts-two.example" in moved and "wss://drafts-one.example" not in moved
    # Another account's list changing moves nothing.
    directory.changed.emit("b" * 64)
    settle()
    assert len(pool.opened) == 2


def test_the_same_relays_are_not_asked_again():
    deletions, _store, pool, directory = routed()
    directory.changed.emit(PK)
    deletions.reroute()
    settle()
    assert len(pool.opened) == 1 and not pool.opened[0][2].closed


def test_a_membership_relay_is_followed_when_it_comes():
    entitled = []
    deletions, _store, pool, _directory = routed(entitled=lambda: list(entitled))
    entitled.append("wss://members.example")
    deletions.reroute()
    settle()
    assert "wss://members.example" in pool.opened[-1][0]


def test_refresh_asks_again():
    deletions, _store, pool, _directory = routed()
    deletions.refresh()
    settle()
    assert len(pool.opened) == 2 and pool.opened[0][2].closed


def test_a_draft_deleted_here_stays_deleted_until_saved_again(monkeypatch):
    """The app's own deletion only removed the draft: an older copy a
    slower relay still sent brought it back until the blanked wrap came
    round (L13)."""
    import main_window
    from types import SimpleNamespace
    store = DraftStore()
    store.bind_profile(PK)
    store.upsert_skeleton(skeleton(1_000))
    monkeypatch.setattr(main_window.time, "time", lambda: 2_000.4)
    host = SimpleNamespace(_draft_store=store)
    main_window.MainWindow._on_draft_tombstoned(host, D, "t" * 64)
    assert store.get(D) is None
    store.upsert_skeleton(skeleton(1_000))       # the older copy, from a slow relay
    assert store.get(D) is None
    store.upsert_skeleton(skeleton(2_000))       # the same second: still that version
    assert store.get(D) is None
    store.upsert_skeleton(skeleton(2_500))       # saved again later, on another device
    assert store.get(D) is not None


def test_the_drafts_refresh_asks_for_the_requests_too():
    """MainWindow cannot be built in a test (see tests/test_membership_
    wiring.py): what the drafts panel's Refresh is connected to is read
    from its code. The requests other apps send came with the drafts at
    the start only (L13)."""
    import ast
    import inspect

    import main_window
    tree = ast.parse(inspect.getsource(main_window.MainWindow))
    targets = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "connect"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "refresh_requested" and node.args):
            targets.add(ast.unparse(node.args[0]))
    assert {"self._draft_sync.refresh_by_hand", "self._draft_deletions.refresh"} <= targets
