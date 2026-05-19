"""EngagementAggregator: dedup, deletion, trust filtering, thread building."""

from __future__ import annotations

import pytest

from plugin_marketplace.social import (
    EngagementAggregator,
    PluginAnchor,
    Stars,
    TrustPolicy,
)
from plugin_marketplace.social.models import (
    CommentEvent,
    DeletionRequest,
    RatingEvent,
    ZapReceipt,
)
from plugin_marketplace.social.parser import PLUGIN_LISTING_KIND


AUTHOR = "a" * 64
ALICE = "b" * 64
BOB = "c" * 64
CAROL = "d" * 64
MALICE = "e" * 64

ANCHOR = PluginAnchor(PLUGIN_LISTING_KIND, AUTHOR, "plugin")
OTHER_ANCHOR = PluginAnchor(PLUGIN_LISTING_KIND, AUTHOR, "other")


def _rating(eid, pubkey, stars, created_at, *, anchor=ANCHOR):
    return RatingEvent(
        event_id=eid, author_pubkey=pubkey,
        anchor=anchor, stars=Stars(stars), created_at=created_at,
    )


def _comment(eid, pubkey, content, created_at, *, parent_id=None, parent_author=None):
    return CommentEvent(
        event_id=eid, author_pubkey=pubkey, anchor=ANCHOR,
        content=content, created_at=created_at,
        parent_event_id=parent_id, parent_author_pubkey=parent_author,
    )


def _zap(eid, pubkey, sats, created_at, *, anchor=ANCHOR):
    return ZapReceipt(
        event_id=eid, zapper_pubkey=pubkey, anchor=anchor,
        amount_sats=sats, created_at=created_at,
    )


@pytest.fixture
def aggregator():
    return EngagementAggregator(anchor=ANCHOR)


# ──────────────────────────────────────────────────────────────────────
# Rating dedup + distribution
# ──────────────────────────────────────────────────────────────────────

def test_ratings_dedup_last_write_wins(aggregator):
    """Alice's later 3-star rating replaces her earlier 5-star one."""
    snap = aggregator.snapshot(ratings=[
        _rating("r1", ALICE, 5, 100),
        _rating("r2", ALICE, 3, 200),
    ])
    assert snap.distribution.total == 1
    assert snap.distribution.three == 1
    assert snap.distribution.five == 0


def test_ratings_aggregate_across_authors(aggregator):
    snap = aggregator.snapshot(ratings=[
        _rating("r1", ALICE, 5, 100),
        _rating("r2", BOB, 4, 100),
        _rating("r3", CAROL, 5, 100),
    ])
    assert snap.distribution.total == 3
    assert snap.distribution.five == 2
    assert snap.distribution.four == 1
    assert snap.distribution.average == pytest.approx((5 + 4 + 5) / 3)


def test_ratings_for_other_plugin_dropped(aggregator):
    """An accidentally-included rating for a different plugin must not
    pollute this plugin's average."""
    snap = aggregator.snapshot(ratings=[
        _rating("r1", ALICE, 5, 100),
        _rating("r2", BOB, 1, 100, anchor=OTHER_ANCHOR),
    ])
    assert snap.distribution.total == 1


# ──────────────────────────────────────────────────────────────────────
# Deletion handling
# ──────────────────────────────────────────────────────────────────────

def test_deletion_removes_own_rating(aggregator):
    deletion = DeletionRequest(
        event_id="d1", author_pubkey=ALICE,
        target_event_ids=("r1",), target_coords=(),
        created_at=200,
    )
    snap = aggregator.snapshot(
        ratings=[_rating("r1", ALICE, 5, 100)],
        deletions=[deletion],
    )
    assert snap.distribution.total == 0


def test_cross_author_deletion_does_not_remove():
    """A deletion published by BOB cannot erase ALICE's rating.

    Without this rule a malicious actor could delete everyone else's
    reviews by publishing kind:5 events naming their event ids.
    """
    agg = EngagementAggregator(anchor=ANCHOR)
    deletion = DeletionRequest(
        event_id="d1", author_pubkey=BOB,
        target_event_ids=("r1",), target_coords=(),
        created_at=200,
    )
    snap = agg.snapshot(
        ratings=[_rating("r1", ALICE, 5, 100)],
        deletions=[deletion],
    )
    assert snap.distribution.total == 1  # Alice's rating survives


def test_addressable_deletion_index_records_coord_window():
    """``a``-tag deletions must populate the coord index with the
    deletion's created_at so future versions of the addressable can
    survive while older versions get tombstoned."""
    from plugin_marketplace.social.aggregator import _index_deletions
    deletion = DeletionRequest(
        event_id="d1", author_pubkey=ALICE,
        target_event_ids=(), target_coords=(ANCHOR.coord,),
        created_at=500,
    )
    index = _index_deletions([deletion])
    # Older version tombstoned.
    assert index.is_coord_deleted_at_or_before(ANCHOR.coord, ALICE, 400) is True
    # Same-instant version tombstoned (spec: "up to created_at").
    assert index.is_coord_deleted_at_or_before(ANCHOR.coord, ALICE, 500) is True
    # Strictly newer version survives.
    assert index.is_coord_deleted_at_or_before(ANCHOR.coord, ALICE, 501) is False
    # Cross-author coord deletion is ignored.
    assert index.is_coord_deleted_at_or_before(ANCHOR.coord, BOB, 100) is False


def test_addressable_deletion_index_keeps_newest_created_at():
    """Multiple deletions of the same coord retain the newest timestamp."""
    from plugin_marketplace.social.aggregator import _index_deletions
    older = DeletionRequest(
        event_id="d1", author_pubkey=ALICE,
        target_event_ids=(), target_coords=(ANCHOR.coord,),
        created_at=400,
    )
    newer = DeletionRequest(
        event_id="d2", author_pubkey=ALICE,
        target_event_ids=(), target_coords=(ANCHOR.coord,),
        created_at=600,
    )
    # Order shouldn't matter — newest timestamp wins either way.
    index = _index_deletions([newer, older])
    assert index.is_coord_deleted_at_or_before(ANCHOR.coord, ALICE, 600) is True
    assert index.is_coord_deleted_at_or_before(ANCHOR.coord, ALICE, 601) is False


# ──────────────────────────────────────────────────────────────────────
# Trust filtering
# ──────────────────────────────────────────────────────────────────────

def test_muted_authors_are_dropped(aggregator):
    policy = TrustPolicy(muted_pubkeys=frozenset({MALICE}))
    snap = aggregator.snapshot(
        ratings=[
            _rating("r1", ALICE, 5, 100),
            _rating("r2", MALICE, 1, 100),
        ],
        policy=policy,
    )
    assert snap.distribution.total == 1
    assert snap.distribution.five == 1


def test_nip05_filter_excludes_unverified(aggregator):
    policy = TrustPolicy(
        require_nip05=True,
        verified_pubkeys=frozenset({ALICE}),
    )
    snap = aggregator.snapshot(
        ratings=[
            _rating("r1", ALICE, 5, 100),
            _rating("r2", BOB, 4, 100),
        ],
        policy=policy,
    )
    assert snap.distribution.total == 1
    assert snap.distribution.five == 1


def test_follow_graph_filter(aggregator):
    policy = TrustPolicy(
        require_follow_graph=True,
        followed_pubkeys=frozenset({BOB}),
    )
    snap = aggregator.snapshot(
        comments=[
            _comment("c1", ALICE, "alice spam", 100),
            _comment("c2", BOB, "bob friend", 200),
        ],
        policy=policy,
    )
    assert len(snap.comments) == 1
    assert snap.comments[0].comment.author_pubkey == BOB


def test_viewer_always_sees_own_contributions_even_when_unverified(aggregator):
    """Without this, a freshly-published rating disappears from the
    viewer's own view while waiting for NIP-05 propagation."""
    policy = TrustPolicy(
        require_nip05=True,
        verified_pubkeys=frozenset(),
        viewer_pubkey=ALICE,
    )
    snap = aggregator.snapshot(
        ratings=[_rating("r1", ALICE, 5, 100)],
        policy=policy,
    )
    assert snap.distribution.total == 1
    assert snap.own_rating is not None
    assert snap.own_rating.event_id == "r1"


def test_mute_beats_viewer_self():
    """If the viewer somehow muted themselves we still apply the mute.

    Edge case: a user accidentally adds themselves to a mute list.
    The mute filter is strict to avoid surprising behavior where
    'I don't see myself but I do see them' becomes inconsistent.
    """
    agg = EngagementAggregator(anchor=ANCHOR)
    policy = TrustPolicy(
        muted_pubkeys=frozenset({ALICE}),
        viewer_pubkey=ALICE,
    )
    snap = agg.snapshot(
        ratings=[_rating("r1", ALICE, 5, 100)],
        policy=policy,
    )
    assert snap.distribution.total == 0


# ──────────────────────────────────────────────────────────────────────
# Comment thread
# ──────────────────────────────────────────────────────────────────────

def test_top_level_comments_sorted_newest_first(aggregator):
    snap = aggregator.snapshot(comments=[
        _comment("c1", ALICE, "old", 100),
        _comment("c2", BOB, "newer", 200),
        _comment("c3", CAROL, "newest", 300),
    ])
    assert [n.comment.event_id for n in snap.comments] == ["c3", "c2", "c1"]


def test_replies_attach_to_parent(aggregator):
    snap = aggregator.snapshot(comments=[
        _comment("parent", ALICE, "top", 100),
        _comment("child1", BOB, "first reply", 200, parent_id="parent"),
        _comment("child2", CAROL, "second reply", 300, parent_id="parent"),
    ])
    assert len(snap.comments) == 1
    parent_node = snap.comments[0]
    assert parent_node.comment.event_id == "parent"
    assert [c.comment.event_id for c in parent_node.children] == ["child1", "child2"]


def test_orphan_reply_becomes_top_level(aggregator):
    """When the parent isn't in our cache yet, the reply still shows
    up so the user isn't left wondering where it went."""
    snap = aggregator.snapshot(comments=[
        _comment("orphan", BOB, "reply", 200, parent_id="missing"),
    ])
    assert len(snap.comments) == 1
    assert snap.comments[0].comment.event_id == "orphan"


def test_comment_count_includes_nested_replies(aggregator):
    snap = aggregator.snapshot(comments=[
        _comment("a", ALICE, "top", 100),
        _comment("b", BOB, "reply", 200, parent_id="a"),
        _comment("c", CAROL, "nested", 300, parent_id="b"),
    ])
    assert snap.comment_count == 3


# ──────────────────────────────────────────────────────────────────────
# Zap totals
# ──────────────────────────────────────────────────────────────────────

def test_zap_amounts_sum(aggregator):
    snap = aggregator.snapshot(zaps=[
        _zap("z1", ALICE, 21, 100),
        _zap("z2", BOB, 100, 200),
        _zap("z3", CAROL, 1000, 300),
    ])
    assert snap.zaps_sats == 1121
    assert snap.zap_count == 3


def test_duplicate_zap_receipt_counted_once(aggregator):
    """If two relays serve the same receipt, only the first is counted."""
    receipt = _zap("z1", ALICE, 50, 100)
    snap = aggregator.snapshot(zaps=[receipt, receipt])
    assert snap.zap_count == 1
    assert snap.zaps_sats == 50


def test_muted_zappers_excluded(aggregator):
    policy = TrustPolicy(muted_pubkeys=frozenset({MALICE}))
    snap = aggregator.snapshot(
        zaps=[
            _zap("z1", ALICE, 100, 100),
            _zap("z2", MALICE, 1000, 200),
        ],
        policy=policy,
    )
    assert snap.zaps_sats == 100
    assert snap.zap_count == 1


# ──────────────────────────────────────────────────────────────────────
# Own contributions surface
# ──────────────────────────────────────────────────────────────────────

def test_own_comment_ids_surfaced_for_viewer(aggregator):
    policy = TrustPolicy(viewer_pubkey=ALICE)
    snap = aggregator.snapshot(
        comments=[
            _comment("a", ALICE, "mine", 100),
            _comment("b", BOB, "theirs", 200),
            _comment("c", ALICE, "mine 2", 300),
        ],
        policy=policy,
    )
    assert set(snap.own_comment_ids) == {"a", "c"}


def test_own_rating_is_latest_one(aggregator):
    policy = TrustPolicy(viewer_pubkey=ALICE)
    snap = aggregator.snapshot(
        ratings=[
            _rating("r1", ALICE, 3, 100),
            _rating("r2", ALICE, 5, 200),
        ],
        policy=policy,
    )
    assert snap.own_rating is not None
    assert snap.own_rating.event_id == "r2"
    assert snap.own_rating.stars.value == 5
