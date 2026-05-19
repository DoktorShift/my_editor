"""One-shot relay query for everything one author has reviewed.

Powers the "their reviews across the marketplace" surface in the
author profile modal. Subscribes to ``{authors: [pubkey], kinds:
[1985, 1111]}`` filtered to our review namespace, parses the result
into typed records, groups them by plugin anchor, and emits the
finished view.

Lifecycle per call to ``request(pubkey)``:

  1. Cache hit fresh? Emit immediately and return.
  2. Otherwise open a one-shot REQ. Buffer events as they arrive.
  3. On EOSE (or timeout), parse + group, cache, emit ``ready``.
  4. Close the subscription.

Unlike ``EngagementFetcher`` (which stays open while a plugin's
detail pane is visible), this fetcher always terminates on its own.
Callers don't need to track subscriptions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from PySide6.QtCore import QObject, QTimer, Signal

from nostr import DEFAULT_RELAYS
from nostr.relay import RelayPool, Subscription

from ._seeds import seed_relays
from .models import PluginAnchor, Stars
from .parser import (
    NIP22_COMMENT_KIND,
    NIP32_LABEL_KIND,
    PLUGIN_LISTING_KIND,
    RATING_LABEL_NAMESPACE,
    _find_all,
    _find_first,
    _plugin_anchor_from_a_tag,
    _shared_event_fields,
    _tags,
)


# A relay that never reaches EOSE shouldn't hold the modal in a
# spinner forever. After this many milliseconds we parse whatever
# arrived so far and emit ``ready``.
_REQ_TIMEOUT_MS: int = 4_000

# Cached results expire after this. Author engagement changes slowly
# so a few minutes is plenty; reopening the modal still feels live.
_CACHE_TTL_S: int = 5 * 60


# --------------------------------------------------------------------------- #
# Public dataclasses                                                          #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class AuthorReview:
    """One review by ``author_pubkey`` of a specific plugin.

    Merges the rating (NIP-32) and the optional comment text (NIP-22)
    into a single row so the dialog renders one entry per plugin. When
    the user rated but didn't write a comment, ``comment`` is empty.
    """

    anchor: PluginAnchor
    stars: Optional[int]      # 1..5, or None when only a comment exists
    comment: str
    created_at: int           # unix seconds, the newer of rating/comment

    def is_empty(self) -> bool:
        return self.stars is None and not self.comment


@dataclass(frozen=True)
class AuthorReviewSet:
    """Result of one ``request`` cycle, sorted newest first."""

    author_pubkey: str
    reviews: tuple[AuthorReview, ...]
    fetched_at: int


# --------------------------------------------------------------------------- #
# Fetcher                                                                     #
# --------------------------------------------------------------------------- #

class AuthorReviewsFetcher(QObject):
    """Fetch every kind:1985 / kind:1111 review by a single author.

    One ``QObject`` per editor process is enough; serial requests are
    fine because the modal only asks for one author at a time. The
    fetcher rejects overlapping requests for the same pubkey to keep
    relay traffic predictable.
    """

    ready = Signal(object)   # AuthorReviewSet
    failed = Signal(str, str)  # pubkey, short reason

    def __init__(
        self,
        *,
        relay_pool: RelayPool,
        extra_relays: tuple[str, ...] = (),
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._relay_pool = relay_pool
        self._extra_relays = tuple(extra_relays)
        self._cache: Dict[str, AuthorReviewSet] = {}
        self._inflight: Set[str] = set()

    # ----------------------------------------------------------------------
    def request(self, pubkey: str) -> None:
        """Ask for ``pubkey``'s review history. Emits ``ready`` once."""
        if not _looks_like_pubkey(pubkey):
            self.failed.emit(pubkey, "invalid pubkey")
            return

        cached = self._cache.get(pubkey)
        if cached is not None and (int(time.time()) - cached.fetched_at) < _CACHE_TTL_S:
            self.ready.emit(cached)
            return

        if pubkey in self._inflight:
            # The previous call's ``ready`` will fire shortly and reach
            # every connected slot. Don't open a second socket.
            return

        self._inflight.add(pubkey)
        self._open_subscription(pubkey)

    def prime(self, review_set: AuthorReviewSet) -> None:
        """Seed the cache with a previously-computed result.

        Used by the marketplace dialog to inject ratings/comments it
        already has in the engagement cache for the current plugin, so
        the modal renders instantly even before relays answer.
        """
        self._cache[review_set.author_pubkey] = review_set

    def clear_cache(self) -> None:
        self._cache.clear()

    # ----------------------------------------------------------------------
    def _open_subscription(self, pubkey: str) -> None:
        urls = self._relays_to_use()
        # We don't filter on the NIP-32 ``L`` tag at the relay level
        # because not every implementation indexes it. Filter on parse
        # instead, which is the cheap path: namespace mismatch -> drop.
        filters = [
            {"kinds": [NIP32_LABEL_KIND, NIP22_COMMENT_KIND], "authors": [pubkey]},
        ]
        sub = self._relay_pool.subscribe(urls, filters)
        collector = _Collector(author_pubkey=pubkey)
        sub.event.connect(collector.handle)

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(_REQ_TIMEOUT_MS)

        def _finish() -> None:
            if collector.finished:
                return
            collector.finished = True
            timer.stop()
            try:
                sub.event.disconnect(collector.handle)
            except (RuntimeError, TypeError):
                pass
            try:
                sub.close()
            except Exception:  # noqa: BLE001
                pass
            self._inflight.discard(pubkey)
            result = collector.build()
            self._cache[pubkey] = result
            self.ready.emit(result)

        sub.eose.connect(_finish)
        timer.timeout.connect(_finish)
        timer.start()

    def _relays_to_use(self) -> List[str]:
        seen: dict[str, None] = {}
        for url in self._extra_relays:
            seen.setdefault(url, None)
        for url in seed_relays():
            seen.setdefault(url, None)
        for url in DEFAULT_RELAYS:
            seen.setdefault(url, None)
        return list(seen.keys())


# --------------------------------------------------------------------------- #
# Collector + parsers                                                         #
# --------------------------------------------------------------------------- #

class _Collector:
    """In-memory buffer for events arriving during one request cycle.

    Keeps the latest rating per anchor and the latest comment per
    anchor. Older events from the same author for the same plugin are
    dropped on the floor, matching how the rest of the aggregator
    treats NIP-32 / NIP-22 republishes.
    """

    def __init__(self, *, author_pubkey: str) -> None:
        self._author = author_pubkey
        self._ratings: Dict[str, _RatingRow] = {}   # anchor.coord -> latest
        self._comments: Dict[str, _CommentRow] = {} # anchor.coord -> latest top-level
        self.finished: bool = False

    def handle(self, event: dict) -> None:
        if self.finished or not isinstance(event, dict):
            return
        if event.get("pubkey") != self._author:
            # The relay's authors filter should already guarantee this,
            # but defending in depth costs us nothing here.
            return
        kind = event.get("kind")
        if kind == NIP32_LABEL_KIND:
            self._handle_rating(event)
        elif kind == NIP22_COMMENT_KIND:
            self._handle_comment(event)

    def build(self) -> AuthorReviewSet:
        merged: Dict[str, AuthorReview] = {}
        for coord, rating in self._ratings.items():
            comment_row = self._comments.get(coord)
            merged[coord] = AuthorReview(
                anchor=rating.anchor,
                stars=rating.stars,
                comment=comment_row.text if comment_row is not None else "",
                created_at=max(
                    rating.created_at,
                    comment_row.created_at if comment_row is not None else 0,
                ),
            )
        # Pull in plugins where the author commented but didn't rate.
        for coord, comment_row in self._comments.items():
            if coord in merged:
                continue
            merged[coord] = AuthorReview(
                anchor=comment_row.anchor,
                stars=None,
                comment=comment_row.text,
                created_at=comment_row.created_at,
            )
        reviews = tuple(sorted(
            merged.values(),
            key=lambda r: r.created_at,
            reverse=True,
        ))
        return AuthorReviewSet(
            author_pubkey=self._author,
            reviews=reviews,
            fetched_at=int(time.time()),
        )

    # ------------------------------------------------------------------
    def _handle_rating(self, event: dict) -> None:
        shared = _shared_event_fields(event)
        if shared is None:
            return
        event_id, pubkey, created_at = shared
        tags = _tags(event)

        namespace_tag = _find_first(tags, "L")
        if namespace_tag is None or len(namespace_tag) < 2:
            return
        if namespace_tag[1] != RATING_LABEL_NAMESPACE:
            return

        label_tag = _find_first(tags, "l")
        if label_tag is None or len(label_tag) < 3:
            return
        if label_tag[2] != RATING_LABEL_NAMESPACE:
            return
        try:
            stars = Stars(int(label_tag[1])).value
        except (TypeError, ValueError):
            return

        anchor = _first_plugin_anchor(tags)
        if anchor is None:
            return

        existing = self._ratings.get(anchor.coord)
        if existing is not None and existing.created_at >= created_at:
            return
        self._ratings[anchor.coord] = _RatingRow(
            anchor=anchor,
            stars=stars,
            created_at=created_at,
            event_id=event_id,
        )

    def _handle_comment(self, event: dict) -> None:
        shared = _shared_event_fields(event)
        if shared is None:
            return
        event_id, pubkey, created_at = shared
        tags = _tags(event)

        # We only care about top-level comments here. Replies (carrying
        # a lowercase ``e`` tag pointing at a parent comment) describe
        # the author's voice inside a thread, not their own review of
        # the plugin, so they'd dilute the row count.
        if _find_first(tags, "e") is not None:
            return

        upper_a = _find_first(tags, "A")
        if upper_a is None:
            return
        anchor = _plugin_anchor_from_a_tag(upper_a)
        if anchor is None or anchor.kind != PLUGIN_LISTING_KIND:
            return

        text = event.get("content", "")
        if not isinstance(text, str):
            return
        text = text.strip()
        if not text:
            return

        existing = self._comments.get(anchor.coord)
        if existing is not None and existing.created_at >= created_at:
            return
        self._comments[anchor.coord] = _CommentRow(
            anchor=anchor,
            text=text,
            created_at=created_at,
            event_id=event_id,
        )


@dataclass(frozen=True)
class _RatingRow:
    anchor: PluginAnchor
    stars: int
    created_at: int
    event_id: str


@dataclass(frozen=True)
class _CommentRow:
    anchor: PluginAnchor
    text: str
    created_at: int
    event_id: str


def _first_plugin_anchor(tags) -> Optional[PluginAnchor]:
    """Return the first ``a`` tag that decodes into a plugin anchor.

    Ratings carry a lowercase ``a`` tag for the target plugin. Some
    publishers also emit an uppercase ``A``; accept either.
    """
    for name in ("a", "A"):
        for tag in _find_all(tags, name):
            anchor = _plugin_anchor_from_a_tag(tag)
            if anchor is not None:
                return anchor
    return None


def _looks_like_pubkey(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )
