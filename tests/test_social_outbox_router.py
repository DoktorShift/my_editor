"""Tests for the marketplace's NIP-65 OutboxRouter.

The router never opens sockets directly — it consults the editor's
existing ``RelayListCache`` (which we mock here) and assembles
deduped, capped relay lists for reads and writes.
"""

from __future__ import annotations

from typing import Dict, Optional

import pytest

from nostr.outbox import RelayList
from plugin_marketplace.social import OutboxRouter


SEEDS = (
    "wss://seed.example.com",
    "wss://seed2.example.com",
)

ALICE = "a" * 64
BOB = "b" * 64
CAROL = "c" * 64


class _StubCache:
    """In-memory replacement for ``nostr.outbox.RelayListCache``.

    Only the methods the router actually calls are implemented.
    """

    def __init__(self, lists: Optional[Dict[str, RelayList]] = None) -> None:
        self._lists: Dict[str, RelayList] = dict(lists or {})
        self.prewarmed: list[str] = []

    def get_cached(self, pubkey: str) -> Optional[RelayList]:
        return self._lists.get(pubkey)

    def fetch(self, pubkey, relays, on_done) -> None:
        # Record but don't run callback — we're only verifying the
        # router asked for a refresh, not the cache's own behaviour.
        self.prewarmed.append(pubkey)


# ──────────────────────────────────────────────────────────────────────
# Reads

def test_read_uses_only_author_write_relays_when_resolved():
    """Per NIP-65 author intent: when all authors have a published
    relay list, do NOT union seeds. The author chose their relays
    deliberately; sending REQs to seeds defeats the routing model.
    """
    cache = _StubCache({
        ALICE: RelayList(write=["wss://alice.example/write"], read=[]),
    })
    router = OutboxRouter(cache)
    plan = router.relays_for_read([ALICE], seeds=SEEDS)
    assert plan.relays == ["wss://alice.example/write"]
    # Seeds are NOT unioned when the author's choice is honoured.
    assert "wss://seed.example.com" not in plan.relays


def test_read_unions_seeds_when_at_least_one_author_unresolved():
    """Mixed sets fall back to seeds so the unresolved author can
    still be discovered. Once their NIP-65 lands, the next call
    switches to author-only routing.
    """
    cache = _StubCache({
        ALICE: RelayList(write=["wss://alice.example/write"], read=[]),
    })
    router = OutboxRouter(cache)
    plan = router.relays_for_read([ALICE, BOB], seeds=SEEDS)
    assert "wss://alice.example/write" in plan.relays
    assert any(url in SEEDS for url in plan.relays)
    assert plan.unresolved_pubkeys == [BOB]


def test_read_falls_back_to_seeds_when_no_cached_list():
    router = OutboxRouter(_StubCache())
    plan = router.relays_for_read([ALICE], seeds=SEEDS)
    assert plan.relays == list(SEEDS)
    assert plan.unresolved_pubkeys == [ALICE]


def test_read_dedupes_across_authors():
    cache = _StubCache({
        ALICE: RelayList(write=["wss://shared.example"], read=[]),
        BOB:   RelayList(write=["wss://shared.example", "wss://bob.example"], read=[]),
    })
    router = OutboxRouter(cache)
    plan = router.relays_for_read([ALICE, BOB], seeds=())
    # Each url appears at most once, in deterministic order.
    assert plan.relays.count("wss://shared.example") == 1
    assert "wss://bob.example" in plan.relays


def test_read_caps_fan_out_at_ten():
    many = [f"wss://r{i}.example" for i in range(20)]
    cache = _StubCache({ALICE: RelayList(write=many, read=[])})
    router = OutboxRouter(cache)
    plan = router.relays_for_read([ALICE], seeds=())
    assert len(plan.relays) <= 10


def test_read_normalizes_trailing_slash_and_case():
    cache = _StubCache({
        ALICE: RelayList(write=["wss://Example.com/", "wss://example.com"], read=[]),
    })
    router = OutboxRouter(cache)
    plan = router.relays_for_read([ALICE], seeds=())
    # Dedup case-insensitively despite minor URL variation.
    assert len([r for r in plan.relays if "example.com" in r.lower()]) == 1


def test_read_empty_when_no_cache_and_no_seeds_and_no_authors():
    router = OutboxRouter(_StubCache())
    plan = router.relays_for_read([], seeds=())
    assert plan.relays == []


def test_anonymous_discovery_uses_seeds():
    router = OutboxRouter(_StubCache())
    plan = router.relays_for_anonymous_discovery(seeds=SEEDS)
    assert plan.relays == list(SEEDS)


# ──────────────────────────────────────────────────────────────────────
# Writes

def test_write_uses_viewer_write_plus_target_read():
    cache = _StubCache({
        ALICE: RelayList(write=["wss://alice-write.example"], read=[]),
        BOB:   RelayList(write=[], read=["wss://bob-read.example"]),
    })
    router = OutboxRouter(cache)
    plan = router.relays_for_write(ALICE, [BOB], seeds=SEEDS)
    assert "wss://alice-write.example" in plan.relays
    assert "wss://bob-read.example" in plan.relays
    # Order: viewer first, then targets, then seeds.
    assert plan.relays[0] == "wss://alice-write.example"


def test_write_includes_seeds_as_backstop():
    """Publishing must never end up with zero relays."""
    router = OutboxRouter(_StubCache())
    plan = router.relays_for_write(ALICE, [BOB], seeds=SEEDS)
    for url in SEEDS:
        assert url in plan.relays


def test_write_ignores_self_in_target_set():
    cache = _StubCache({
        ALICE: RelayList(write=["wss://alice.example"], read=["wss://alice-in.example"]),
    })
    router = OutboxRouter(cache)
    plan = router.relays_for_write(ALICE, [ALICE], seeds=())
    # The viewer's read relays don't bleed in via a self-tag.
    assert "wss://alice-in.example" not in plan.relays


def test_write_records_unresolved_pubkeys():
    cache = _StubCache({
        ALICE: RelayList(write=["wss://alice.example"], read=[]),
    })
    router = OutboxRouter(cache)
    plan = router.relays_for_write(ALICE, [BOB, CAROL], seeds=SEEDS)
    assert set(plan.unresolved_pubkeys) == {BOB, CAROL}


def test_write_caps_at_ten():
    cache = _StubCache({
        ALICE: RelayList(
            write=[f"wss://aw{i}.example" for i in range(7)], read=[],
        ),
        BOB: RelayList(
            write=[], read=[f"wss://br{i}.example" for i in range(7)],
        ),
    })
    router = OutboxRouter(cache)
    plan = router.relays_for_write(ALICE, [BOB], seeds=SEEDS)
    assert len(plan.relays) <= 10


# ──────────────────────────────────────────────────────────────────────
# Prewarm

def test_prewarm_only_fetches_pubkeys_missing_from_cache():
    cache = _StubCache({
        ALICE: RelayList(write=["wss://alice.example"], read=[]),
    })
    router = OutboxRouter(cache)
    router.prewarm([ALICE, BOB, CAROL], seeds=SEEDS)
    assert set(cache.prewarmed) == {BOB, CAROL}


def test_prewarm_no_op_without_seeds():
    """Without a seed relay we have nowhere to ask, so do nothing."""
    cache = _StubCache()
    router = OutboxRouter(cache)
    router.prewarm([ALICE], seeds=())
    assert cache.prewarmed == []


def test_router_safe_with_no_cache():
    """A router built before the cache is ready degrades to seed-only.

    The router still reports the requested pubkeys as unresolved so a
    later prewarm picks them up once the cache attaches; callers that
    don't have a cache simply ignore the field.
    """
    router = OutboxRouter(None)
    plan = router.relays_for_read([ALICE], seeds=SEEDS)
    assert plan.relays == list(SEEDS)
    assert plan.unresolved_pubkeys == [ALICE]


def test_router_safe_with_no_cache_for_write():
    router = OutboxRouter(None)
    plan = router.relays_for_write(ALICE, [BOB], seeds=SEEDS)
    assert plan.relays == list(SEEDS)
    assert set(plan.unresolved_pubkeys) == {ALICE, BOB}
