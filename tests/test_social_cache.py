"""SQLite engagement cache: round-trip, freshness, parsed-for-anchor."""

from __future__ import annotations

import json
import time

import pytest

from plugin_marketplace.social import (
    EngagementAggregator,
    EngagementCache,
    PluginAnchor,
    RATING_LABEL_NAMESPACE,
    PLUGIN_LISTING_KIND,
    NIP22_COMMENT_KIND,
    NIP32_LABEL_KIND,
)
from plugin_marketplace.social.models import Nip05Verification


AUTHOR = "a" * 64
RATER = "b" * 64
ANCHOR = PluginAnchor(PLUGIN_LISTING_KIND, AUTHOR, "plugin")


def _id(prefix: str) -> str:
    return (prefix * 64)[:64]


@pytest.fixture
def cache(tmp_path):
    return EngagementCache(path=tmp_path / "engagement.sqlite")


# ──────────────────────────────────────────────────────────────────────
# Raw event round-trip
# ──────────────────────────────────────────────────────────────────────

def test_upsert_and_load_event(cache):
    event = {
        "id": _id("1"),
        "kind": NIP32_LABEL_KIND,
        "pubkey": RATER,
        "created_at": 1_700_000_000,
        "tags": [],
        "content": "",
    }
    cache.upsert_event(anchor=ANCHOR, event=event)
    loaded = cache.load_events(ANCHOR)
    assert len(loaded) == 1
    assert loaded[0]["id"] == event["id"]


def test_upsert_is_idempotent(cache):
    event = {
        "id": _id("1"),
        "kind": NIP32_LABEL_KIND,
        "pubkey": RATER,
        "created_at": 1_700_000_000,
        "tags": [],
        "content": "",
    }
    cache.upsert_event(anchor=ANCHOR, event=event)
    cache.upsert_event(anchor=ANCHOR, event=event)
    assert len(cache.load_events(ANCHOR)) == 1


def test_load_events_filters_by_kind(cache):
    cache.upsert_event(anchor=ANCHOR, event={
        "id": _id("1"), "kind": NIP32_LABEL_KIND,
        "pubkey": RATER, "created_at": 1_700_000_000,
        "tags": [], "content": "",
    })
    cache.upsert_event(anchor=ANCHOR, event={
        "id": _id("2"), "kind": NIP22_COMMENT_KIND,
        "pubkey": RATER, "created_at": 1_700_000_100,
        "tags": [], "content": "x",
    })
    only_ratings = cache.load_events(ANCHOR, kinds=[NIP32_LABEL_KIND])
    assert [e["kind"] for e in only_ratings] == [NIP32_LABEL_KIND]


def test_upsert_rejects_malformed_event(cache):
    """Missing fields shouldn't crash, just no-op."""
    cache.upsert_event(anchor=ANCHOR, event={"id": _id("1")})
    assert cache.load_events(ANCHOR) == []


# ──────────────────────────────────────────────────────────────────────
# Parsed-for-anchor pipeline
# ──────────────────────────────────────────────────────────────────────

def test_parsed_for_anchor_returns_typed_records(cache):
    """Round-trip: store a valid rating event, parsed_for_anchor returns
    it as a RatingEvent."""
    event = {
        "id": _id("1"),
        "kind": NIP32_LABEL_KIND,
        "pubkey": RATER,
        "created_at": 1_700_000_000,
        "tags": [
            ["L", RATING_LABEL_NAMESPACE],
            ["l", "4", RATING_LABEL_NAMESPACE],
            ["a", ANCHOR.coord, ""],
            ["p", AUTHOR, ""],
        ],
        "content": "",
    }
    cache.upsert_event(anchor=ANCHOR, event=event)
    ratings, comments, zaps, deletions = cache.parsed_for_anchor(ANCHOR)
    assert len(ratings) == 1
    assert ratings[0].stars.value == 4
    assert comments == [] and zaps == [] and deletions == []


def test_parsed_for_anchor_silently_drops_malformed(cache):
    """An event that's the right kind but the wrong shape doesn't
    surface as a parsed record."""
    cache.upsert_event(anchor=ANCHOR, event={
        "id": _id("1"),
        "kind": NIP32_LABEL_KIND,
        "pubkey": RATER,
        "created_at": 1_700_000_000,
        "tags": [],  # no namespace tag, no label, no anchor
        "content": "",
    })
    ratings, comments, _z, _d = cache.parsed_for_anchor(ANCHOR)
    assert ratings == [] and comments == []


# ──────────────────────────────────────────────────────────────────────
# Aggregate snapshot
# ──────────────────────────────────────────────────────────────────────

def test_snapshot_round_trip(cache):
    """Store -> aggregate(...) returns headline figures."""
    from plugin_marketplace.social.models import Stars
    from plugin_marketplace.social.models import RatingEvent
    aggregator = EngagementAggregator(anchor=ANCHOR)
    snap = aggregator.snapshot(ratings=[
        RatingEvent(_id("1"), RATER, ANCHOR, Stars(5), 100),
    ])
    cache.store_snapshot(snap)
    agg = cache.aggregate(ANCHOR)
    assert agg is not None
    assert agg["rating_count"] == 1
    assert agg["rating_avg"] == 5.0
    assert agg["distribution"] == [0, 0, 0, 0, 1]


def test_aggregate_is_fresh_uses_ttl(cache, monkeypatch):
    """``time.time`` returns the wall clock. We freeze it before patching
    so the patched callable doesn't end up calling itself."""
    from plugin_marketplace.social.models import Stars
    from plugin_marketplace.social.models import RatingEvent
    aggregator = EngagementAggregator(anchor=ANCHOR)
    snap = aggregator.snapshot(ratings=[
        RatingEvent(_id("1"), RATER, ANCHOR, Stars(5), 100),
    ])
    cache.store_snapshot(snap)
    assert cache.aggregate_is_fresh(ANCHOR)
    # Travel 10 minutes into the future. Capture the real time.time
    # callable BEFORE patching so the replacement doesn't recurse.
    original_time = time.time
    frozen = original_time() + 600
    monkeypatch.setattr(
        "plugin_marketplace.social.cache.time.time",
        lambda: frozen,
    )
    assert not cache.aggregate_is_fresh(ANCHOR)


def test_aggregate_returns_none_when_not_stored(cache):
    assert cache.aggregate(ANCHOR) is None
    assert not cache.aggregate_is_fresh(ANCHOR)


# ──────────────────────────────────────────────────────────────────────
# NIP-05 verification cache
# ──────────────────────────────────────────────────────────────────────

def test_nip05_round_trip(cache):
    verification = Nip05Verification(
        pubkey=RATER, identifier="alice@example.com",
        verified=True, checked_at=int(time.time()),
    )
    cache.store_nip05(verification)
    out = cache.get_nip05(RATER, "alice@example.com")
    assert out is not None
    assert out.verified is True


def test_nip05_pending_record_returns_verified_none(cache):
    cache.store_nip05(Nip05Verification(
        pubkey=RATER, identifier="alice@example.com",
        verified=None, checked_at=None,
    ))
    out = cache.get_nip05(RATER, "alice@example.com")
    assert out is not None and out.verified is None


def test_verified_pubkeys_only_returns_verified(cache):
    cache.store_nip05(Nip05Verification(
        pubkey=RATER, identifier="alice@a.com",
        verified=True, checked_at=int(time.time()),
    ))
    cache.store_nip05(Nip05Verification(
        pubkey="f" * 64, identifier="bob@b.com",
        verified=False, checked_at=int(time.time()),
    ))
    assert cache.verified_pubkeys() == [RATER]


# ──────────────────────────────────────────────────────────────────────
# Profile metadata
# ──────────────────────────────────────────────────────────────────────

def test_profile_round_trip(cache):
    cache.store_profile(RATER, display_name="Alice", picture_url="https://x/a.png", nip05="alice@x.com")
    profile = cache.get_profile(RATER)
    assert profile is not None
    assert profile["display_name"] == "Alice"
    assert profile["picture_url"] == "https://x/a.png"
    assert profile["nip05"] == "alice@x.com"


# ──────────────────────────────────────────────────────────────────────
# Purge
# ──────────────────────────────────────────────────────────────────────

def test_purge_anchor_drops_events_and_snapshot(cache):
    from plugin_marketplace.social.models import RatingEvent, Stars
    cache.upsert_event(anchor=ANCHOR, event={
        "id": _id("1"), "kind": NIP32_LABEL_KIND,
        "pubkey": RATER, "created_at": 100,
        "tags": [], "content": "",
    })
    agg = EngagementAggregator(anchor=ANCHOR)
    cache.store_snapshot(agg.snapshot(ratings=[
        RatingEvent(_id("1"), RATER, ANCHOR, Stars(5), 100),
    ]))
    cache.purge_anchor(ANCHOR)
    assert cache.load_events(ANCHOR) == []
    assert cache.aggregate(ANCHOR) is None
