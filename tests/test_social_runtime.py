"""Integration tests for fetcher + verifier + publisher.

We don't spin up real WebSockets or HTTPS endpoints here. The tests
stand fake objects in front of the runtime pieces so we can verify
the wiring (cache writes, signal emissions, NIP-05 normalization)
without leaving the process.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from plugin_marketplace.social import (
    EngagementCache,
    EngagementFetcher,
    Nip05Verifier,
    PluginAnchor,
    RATING_LABEL_NAMESPACE,
)
from plugin_marketplace.social.models import Nip05Verification
from plugin_marketplace.social.parser import (
    NIP09_DELETION_KIND,
    NIP22_COMMENT_KIND,
    NIP32_LABEL_KIND,
    NIP57_ZAP_RECEIPT_KIND,
    PLUGIN_LISTING_KIND,
)


AUTHOR_PUBKEY = "a" * 64
RATER_PUBKEY = "b" * 64
ANCHOR = PluginAnchor(PLUGIN_LISTING_KIND, AUTHOR_PUBKEY, "plugin")


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def cache(tmp_path):
    return EngagementCache(path=tmp_path / "engagement.sqlite")


# ──────────────────────────────────────────────────────────────────────
# Fetcher tests (no live relay; we directly call _handle_event)
# ──────────────────────────────────────────────────────────────────────

class _FakeRelayPool:
    """Stand-in for nostr.relay.RelayPool.

    ``subscribe`` returns an object with ``event`` / ``eose`` / ``closed``
    signals and a ``close()`` method, mimicking the real
    ``Subscription`` enough for the fetcher to wire up.
    """

    def __init__(self) -> None:
        self.subs: list[_FakeSubscription] = []

    def subscribe(self, urls, filters, sub_id=None):
        sub = _FakeSubscription(urls=urls, filters=filters)
        self.subs.append(sub)
        return sub


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


def _signed_rating(stars: int) -> dict:
    """Build a fully-signed rating using the editor's own crypto.

    The fetcher rejects events whose signature doesn't verify; using
    the real signer guarantees the fake events pass that gate.
    """
    from nostr import crypto
    from nostr.events import build_event, sign_event
    sk = bytes(range(1, 33))   # avoid the all-zero secret which is invalid
    pubkey = crypto.get_public_key(sk).hex()
    unsigned = build_event(
        pubkey_hex=pubkey,
        kind=NIP32_LABEL_KIND,
        content="",
        tags=[
            ["L", RATING_LABEL_NAMESPACE],
            ["l", str(stars), RATING_LABEL_NAMESPACE],
            ["a", ANCHOR.coord, ""],
        ],
        created_at=int(time.time()),
    )
    return sign_event(unsigned, sk)


def test_fetcher_persists_valid_event_into_cache(qapp, cache):
    fetcher = EngagementFetcher(cache, relay_pool=_FakeRelayPool())
    event = _signed_rating(5)
    fetcher._handle_event(ANCHOR, event)
    stored = cache.load_events(ANCHOR, kinds=[NIP32_LABEL_KIND])
    assert len(stored) == 1
    assert stored[0]["id"] == event["id"]


def test_fetcher_rejects_unsigned_event(qapp, cache):
    """A relay serving an event with an invalid signature must not
    pollute the cache."""
    fetcher = EngagementFetcher(cache, relay_pool=_FakeRelayPool())
    bogus = _signed_rating(5)
    bogus["sig"] = "00" * 64  # invalid
    fetcher._handle_event(ANCHOR, bogus)
    assert cache.load_events(ANCHOR) == []


def test_fetcher_rejects_unexpected_kind(qapp, cache):
    """Filters narrow the wire but the fetcher double-checks the kind
    so a hostile relay can't sneak in unrelated events."""
    fetcher = EngagementFetcher(cache, relay_pool=_FakeRelayPool())
    fetcher._handle_event(ANCHOR, {"kind": 1, "id": "x" * 64, "pubkey": "0" * 64,
                                   "created_at": 1, "tags": [], "content": "",
                                   "sig": "0" * 128})
    assert cache.load_events(ANCHOR) == []


def test_fetcher_watch_unwatch_refcounts(qapp, cache):
    """Two watchers means one ``unwatch`` doesn't close the sub."""
    pool = _FakeRelayPool()
    fetcher = EngagementFetcher(cache, relay_pool=pool)
    fetcher.watch(ANCHOR)
    fetcher.watch(ANCHOR)
    assert len(pool.subs) == 1   # second watch reuses the existing sub
    fetcher.unwatch(ANCHOR)
    assert not pool.subs[0].closed_flag
    fetcher.unwatch(ANCHOR)
    assert pool.subs[0].closed_flag


def test_fetcher_emits_snapshot_changed(qapp, cache):
    fetcher = EngagementFetcher(cache, relay_pool=_FakeRelayPool())
    captured = []
    fetcher.snapshot_changed.connect(captured.append)
    fetcher._handle_event(ANCHOR, _signed_rating(4))
    assert captured == [ANCHOR]


def test_fetcher_close_all_idempotent(qapp, cache):
    pool = _FakeRelayPool()
    fetcher = EngagementFetcher(cache, relay_pool=pool)
    fetcher.watch(ANCHOR)
    fetcher.close_all()
    fetcher.close_all()    # double-close must not raise


# ──────────────────────────────────────────────────────────────────────
# NIP-05 verifier tests
# ──────────────────────────────────────────────────────────────────────

def test_verifier_immediate_negative_for_malformed_identifier(qapp, cache):
    verifier = Nip05Verifier(cache)
    out = verifier.request(RATER_PUBKEY, "not-an-identifier")
    assert out is not None
    assert out.verified is False
    # Future calls don't re-fetch because we cached the negative.
    cached = cache.get_nip05(RATER_PUBKEY, "not-an-identifier")
    assert cached.verified is False


def test_verifier_returns_cached_fresh_result(qapp, cache):
    cache.store_nip05(Nip05Verification(
        pubkey=RATER_PUBKEY, identifier="alice@example.com",
        verified=True, checked_at=int(time.time()),
    ))
    verifier = Nip05Verifier(cache)
    out = verifier.request(RATER_PUBKEY, "alice@example.com")
    assert out is not None and out.verified is True


def test_verifier_coalesces_concurrent_requests(qapp, cache, monkeypatch):
    """Two requests for the same key should fire one worker."""
    started: list[str] = []
    real_start = None

    class _CountingPool:
        def start(self, runnable):
            started.append("x")
            # Don't actually run the runnable; we'd block on the network.

    verifier = Nip05Verifier(cache, thread_pool=_CountingPool())
    verifier.request(RATER_PUBKEY, "alice@example.com")
    verifier.request(RATER_PUBKEY, "alice@example.com")
    assert len(started) == 1


# ──────────────────────────────────────────────────────────────────────
# Anchor decode (controller side)
# ──────────────────────────────────────────────────────────────────────

def test_controller_anchor_from_naddr(qapp):
    from nostr.bech32 import encode_naddr
    from plugin_marketplace.controller import _anchor_from_naddr_or_coord
    naddr = encode_naddr("hello_world", AUTHOR_PUBKEY, PLUGIN_LISTING_KIND)
    anchor = _anchor_from_naddr_or_coord(naddr)
    assert anchor is not None
    assert anchor.author_pubkey == AUTHOR_PUBKEY
    assert anchor.plugin_id == "hello_world"
    assert anchor.kind == PLUGIN_LISTING_KIND


def test_controller_anchor_from_coord(qapp):
    from plugin_marketplace.controller import _anchor_from_naddr_or_coord
    coord = f"{PLUGIN_LISTING_KIND}:{AUTHOR_PUBKEY}:hello_world"
    anchor = _anchor_from_naddr_or_coord(coord)
    assert anchor is not None
    assert anchor.author_pubkey == AUTHOR_PUBKEY


def test_controller_anchor_rejects_garbage(qapp):
    from plugin_marketplace.controller import _anchor_from_naddr_or_coord
    assert _anchor_from_naddr_or_coord("") is None
    assert _anchor_from_naddr_or_coord("naddr1bogus") is None
    assert _anchor_from_naddr_or_coord("not:a:coord") is None
