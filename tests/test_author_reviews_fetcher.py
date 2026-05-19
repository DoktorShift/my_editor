"""Tests for ``AuthorReviewsFetcher``.

The fetcher's value is its collector: given a stream of raw Nostr
events from one author, it produces a stable ``AuthorReviewSet``. We
test the collector directly so the tests don't depend on a real relay.
"""

from __future__ import annotations

import pytest

from plugin_marketplace.social import (
    NIP22_COMMENT_KIND,
    NIP32_LABEL_KIND,
    PLUGIN_LISTING_KIND,
    PluginAnchor,
    RATING_LABEL_NAMESPACE,
    AuthorReview,
    AuthorReviewSet,
)
from plugin_marketplace.social.author_reviews_fetcher import _Collector


AUTHOR = "a" * 64
OTHER = "b" * 64
PLUGIN_A = PluginAnchor(kind=PLUGIN_LISTING_KIND, author_pubkey="d" * 64, plugin_id="plugin_a")
PLUGIN_B = PluginAnchor(kind=PLUGIN_LISTING_KIND, author_pubkey="e" * 64, plugin_id="plugin_b")


def _hex_id(prefix: str) -> str:
    return (prefix * 64)[:64]


def _rating_event(*, anchor: PluginAnchor, stars: int, created_at: int, eid: str) -> dict:
    return {
        "id": eid,
        "pubkey": AUTHOR,
        "kind": NIP32_LABEL_KIND,
        "created_at": created_at,
        "content": "",
        "sig": "f" * 128,
        "tags": [
            ["L", RATING_LABEL_NAMESPACE],
            ["l", str(stars), RATING_LABEL_NAMESPACE],
            ["a", anchor.coord],
        ],
    }


def _comment_event(*, anchor: PluginAnchor, body: str, created_at: int, eid: str,
                   reply_to: str = "") -> dict:
    tags = [
        ["A", anchor.coord],
        ["K", str(PLUGIN_LISTING_KIND)],
        ["a", anchor.coord],
    ]
    if reply_to:
        tags.append(["e", reply_to])
        tags.append(["k", str(NIP22_COMMENT_KIND)])
    return {
        "id": eid,
        "pubkey": AUTHOR,
        "kind": NIP22_COMMENT_KIND,
        "created_at": created_at,
        "content": body,
        "sig": "f" * 128,
        "tags": tags,
    }


# ──────────────────────────────────────────────────────────────────────
# Happy paths
# ──────────────────────────────────────────────────────────────────────

def test_rating_then_comment_merges_to_one_row():
    c = _Collector(author_pubkey=AUTHOR)
    c.handle(_rating_event(anchor=PLUGIN_A, stars=5, created_at=100, eid=_hex_id("a")))
    c.handle(_comment_event(anchor=PLUGIN_A, body="great plugin", created_at=200, eid=_hex_id("b")))
    result = c.build()
    assert isinstance(result, AuthorReviewSet)
    assert result.author_pubkey == AUTHOR
    assert len(result.reviews) == 1
    review = result.reviews[0]
    assert review.stars == 5
    assert review.comment == "great plugin"
    assert review.created_at == 200  # the newer of the two timestamps


def test_orders_newest_first():
    c = _Collector(author_pubkey=AUTHOR)
    c.handle(_rating_event(anchor=PLUGIN_A, stars=4, created_at=100, eid=_hex_id("a")))
    c.handle(_rating_event(anchor=PLUGIN_B, stars=5, created_at=300, eid=_hex_id("b")))
    reviews = c.build().reviews
    assert reviews[0].anchor == PLUGIN_B
    assert reviews[1].anchor == PLUGIN_A


def test_keeps_latest_rating_per_plugin():
    c = _Collector(author_pubkey=AUTHOR)
    c.handle(_rating_event(anchor=PLUGIN_A, stars=1, created_at=100, eid=_hex_id("a")))
    c.handle(_rating_event(anchor=PLUGIN_A, stars=5, created_at=200, eid=_hex_id("b")))
    reviews = c.build().reviews
    assert len(reviews) == 1
    assert reviews[0].stars == 5


def test_comment_without_rating_still_appears():
    c = _Collector(author_pubkey=AUTHOR)
    c.handle(_comment_event(anchor=PLUGIN_A, body="thoughts", created_at=100, eid=_hex_id("a")))
    reviews = c.build().reviews
    assert len(reviews) == 1
    assert reviews[0].stars is None
    assert reviews[0].comment == "thoughts"


# ──────────────────────────────────────────────────────────────────────
# Filtering
# ──────────────────────────────────────────────────────────────────────

def test_ignores_events_from_other_authors():
    c = _Collector(author_pubkey=AUTHOR)
    bogus = _rating_event(anchor=PLUGIN_A, stars=5, created_at=100, eid=_hex_id("a"))
    bogus["pubkey"] = OTHER
    c.handle(bogus)
    assert c.build().reviews == ()


def test_ignores_replies():
    """Replies to other commenters aren't reviews of the plugin."""
    c = _Collector(author_pubkey=AUTHOR)
    c.handle(_comment_event(
        anchor=PLUGIN_A, body="re: alice", created_at=100, eid=_hex_id("a"),
        reply_to=_hex_id("b"),
    ))
    assert c.build().reviews == ()


def test_ignores_wrong_namespace_rating():
    c = _Collector(author_pubkey=AUTHOR)
    ev = _rating_event(anchor=PLUGIN_A, stars=5, created_at=100, eid=_hex_id("a"))
    # Replace the namespace declaration. The collector should drop it.
    ev["tags"][0] = ["L", "com.someone-else/rating"]
    c.handle(ev)
    assert c.build().reviews == ()


def test_ignores_invalid_star_values():
    c = _Collector(author_pubkey=AUTHOR)
    ev = _rating_event(anchor=PLUGIN_A, stars=5, created_at=100, eid=_hex_id("a"))
    ev["tags"][1] = ["l", "9", RATING_LABEL_NAMESPACE]   # out of range
    c.handle(ev)
    assert c.build().reviews == ()


def test_ignores_unknown_kind():
    c = _Collector(author_pubkey=AUTHOR)
    c.handle({
        "id": _hex_id("a"),
        "pubkey": AUTHOR,
        "kind": 42,
        "created_at": 100,
        "content": "",
        "sig": "f" * 128,
        "tags": [],
    })
    assert c.build().reviews == ()


def test_drops_blank_comment_bodies():
    """Some clients emit kind:1111 with empty content to ack a vote.
    Those would clutter the list with empty rows."""
    c = _Collector(author_pubkey=AUTHOR)
    c.handle(_comment_event(anchor=PLUGIN_A, body="   ", created_at=100, eid=_hex_id("a")))
    assert c.build().reviews == ()


# ──────────────────────────────────────────────────────────────────────
# AuthorReview shape
# ──────────────────────────────────────────────────────────────────────

def test_is_empty_classifies_correctly():
    full = AuthorReview(anchor=PLUGIN_A, stars=5, comment="x", created_at=1)
    star_only = AuthorReview(anchor=PLUGIN_A, stars=5, comment="", created_at=1)
    comment_only = AuthorReview(anchor=PLUGIN_A, stars=None, comment="x", created_at=1)
    empty = AuthorReview(anchor=PLUGIN_A, stars=None, comment="", created_at=1)
    assert not full.is_empty()
    assert not star_only.is_empty()
    assert not comment_only.is_empty()
    assert empty.is_empty()
