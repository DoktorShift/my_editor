"""Tests for MuteListCache: read, write, and local-state-update flow.

We don't touch real relays. A ``_FakeRelayPool`` records every
publish + subscribe so we can introspect the wire shape. A
``_FakeBunkerPool`` short-circuits NIP-46 signing by stamping
``id`` and ``sig`` onto the event template.
"""

from __future__ import annotations

import time
from typing import List

import pytest

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from plugin_marketplace.social import MuteListCache
from plugin_marketplace.social.parser import NIP51_MUTE_LIST_KIND


OWNER = "a" * 64
TARGET_A = "b" * 64
TARGET_B = "c" * 64


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakePublishJob(QObject):
    """Stand-in for ``nostr.relay.PublishJob``."""
    first_accept = Signal(str)
    all_done = Signal(list)


class _FakeSubscription(QObject):
    event = Signal(dict)
    eose = Signal()
    closed = Signal(str)

    def __init__(self, *, urls, filters) -> None:
        super().__init__()
        self.urls = urls
        self.filters = filters
        self.closed_flag = False

    def close(self) -> None:
        self.closed_flag = True


class _FakeRelayPool:
    def __init__(self):
        self.subscriptions: List[_FakeSubscription] = []
        self.publishes: List[tuple] = []

    def subscribe(self, urls, filters, sub_id=None):
        sub = _FakeSubscription(urls=urls, filters=filters)
        self.subscriptions.append(sub)
        return sub

    def publish(self, urls, event):
        self.publishes.append((list(urls), event))
        return _FakePublishJob()


class _FakeProfile:
    def __init__(self) -> None:
        self.user_pubkey = OWNER
        self.bunker_relays = ()


class _FakeBunkerClient:
    def __init__(self):
        self.signed: List[dict] = []

    def sign_event(self, unsigned, *, on_success, on_failure):
        signed = dict(unsigned)
        signed["id"] = "0" * 64
        signed["sig"] = "0" * 128
        self.signed.append(signed)
        on_success(signed)


class _FakeBunkerPool:
    def __init__(self):
        self.client = _FakeBunkerClient()

    def get(self, profile, on_success, on_failure):
        on_success(self.client)


# ──────────────────────────────────────────────────────────────────────
# Read

def test_initial_state_empty(qapp):
    cache = MuteListCache(relay_pool=_FakeRelayPool())
    assert cache.muted_pubkeys(OWNER) == frozenset()


def test_watch_opens_subscription_with_kind_10000(qapp):
    pool = _FakeRelayPool()
    cache = MuteListCache(relay_pool=pool)
    cache.watch(OWNER)
    assert len(pool.subscriptions) == 1
    sub = pool.subscriptions[0]
    assert sub.filters == [{"kinds": [NIP51_MUTE_LIST_KIND], "authors": [OWNER], "limit": 1}]


def test_watch_refcounts(qapp):
    pool = _FakeRelayPool()
    cache = MuteListCache(relay_pool=pool)
    cache.watch(OWNER)
    cache.watch(OWNER)
    assert len(pool.subscriptions) == 1
    cache.unwatch(OWNER)
    assert pool.subscriptions[0].closed_flag is False
    cache.unwatch(OWNER)
    assert pool.subscriptions[0].closed_flag is True


# ──────────────────────────────────────────────────────────────────────
# Write

def test_mute_publishes_kind_10000_with_p_tag(qapp):
    pool = _FakeRelayPool()
    bunker = _FakeBunkerPool()
    cache = MuteListCache(relay_pool=pool, bunker_pool=bunker)
    cache.mute(_FakeProfile(), TARGET_A)
    assert len(pool.publishes) == 1
    _urls, event = pool.publishes[0]
    assert event["kind"] == NIP51_MUTE_LIST_KIND
    assert ["p", TARGET_A] in event["tags"]


def test_mute_updates_local_set_immediately(qapp):
    pool = _FakeRelayPool()
    bunker = _FakeBunkerPool()
    cache = MuteListCache(relay_pool=pool, bunker_pool=bunker)
    captured: List[str] = []
    cache.mute_list_updated.connect(captured.append)
    cache.mute(_FakeProfile(), TARGET_A)
    assert TARGET_A in cache.muted_pubkeys(OWNER)
    assert captured == [OWNER]


def test_unmute_removes_from_local_set(qapp):
    pool = _FakeRelayPool()
    bunker = _FakeBunkerPool()
    cache = MuteListCache(relay_pool=pool, bunker_pool=bunker)
    profile = _FakeProfile()
    cache.mute(profile, TARGET_A)
    cache.mute(profile, TARGET_B)
    cache.unmute(profile, TARGET_A)
    assert cache.muted_pubkeys(OWNER) == frozenset({TARGET_B})


def test_unmute_unknown_target_no_op(qapp):
    pool = _FakeRelayPool()
    bunker = _FakeBunkerPool()
    cache = MuteListCache(relay_pool=pool, bunker_pool=bunker)
    captured = []
    cache.mute_list_updated.connect(captured.append)
    cache.unmute(_FakeProfile(), TARGET_A)
    assert pool.publishes == []
    assert captured == []


def test_mute_dedups_idempotent_calls(qapp):
    pool = _FakeRelayPool()
    bunker = _FakeBunkerPool()
    cache = MuteListCache(relay_pool=pool, bunker_pool=bunker)
    profile = _FakeProfile()
    cache.mute(profile, TARGET_A)
    cache.mute(profile, TARGET_A)
    assert len(pool.publishes) == 1


def test_publish_skipped_when_no_bunker_configured(qapp):
    """An offline cache (no signer) still updates the local set so the
    UI feels responsive — it just doesn't reach the relays."""
    pool = _FakeRelayPool()
    cache = MuteListCache(relay_pool=pool, bunker_pool=None)
    captured = []
    cache.mute(_FakeProfile(), TARGET_A, on_status=lambda msg, ms: captured.append(msg))
    assert pool.publishes == []
    assert TARGET_A in cache.muted_pubkeys(OWNER)
    assert any("Sign in" in m for m in captured)


# ──────────────────────────────────────────────────────────────────────
# Incoming event handling

def test_handle_event_keeps_newer_version(qapp):
    pool = _FakeRelayPool()
    cache = MuteListCache(relay_pool=pool)
    older = _make_mute_event(OWNER, [TARGET_A], created_at=1000)
    newer = _make_mute_event(OWNER, [TARGET_A, TARGET_B], created_at=2000)
    # The synthetic signer derives a real pubkey; use that as the owner
    # since the verifier checks event.pubkey == owner.
    derived_owner = older["pubkey"]
    cache._handle_event(derived_owner, older)   # type: ignore[attr-defined]
    cache._handle_event(derived_owner, newer)   # type: ignore[attr-defined]
    assert cache.muted_pubkeys(derived_owner) == frozenset({TARGET_A, TARGET_B})


def test_handle_event_rejects_wrong_pubkey(qapp):
    pool = _FakeRelayPool()
    cache = MuteListCache(relay_pool=pool)
    foreign = _make_mute_event(TARGET_A, [TARGET_B], created_at=1000)
    cache._handle_event(OWNER, foreign)   # type: ignore[attr-defined]
    assert cache.muted_pubkeys(OWNER) == frozenset()


def _make_mute_event(owner_pubkey: str, muted: List[str], *, created_at: int) -> dict:
    """Build a signed-looking kind:10000 event for tests.

    The cache calls ``verify_event`` which checks BIP-340 schnorr;
    we sign with a real key so the verification passes.
    """
    from nostr import crypto
    from nostr.events import build_event, sign_event
    sk_int = int(owner_pubkey[:8], 16) or 1
    sk = sk_int.to_bytes(32, "big")
    pubkey_hex = crypto.get_public_key(sk).hex()
    unsigned = build_event(
        pubkey_hex=pubkey_hex,
        kind=NIP51_MUTE_LIST_KIND,
        content="",
        tags=[["p", pk] for pk in muted],
        created_at=created_at,
    )
    return sign_event(unsigned, sk)
