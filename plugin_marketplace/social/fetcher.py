"""Live engagement subscriptions, one per opened plugin.

The fetcher's job is small but load-bearing:

  - When the user opens a plugin's detail pane, subscribe to all
    configured relays for events that target that plugin's anchor.
    Concretely the wire filter is:

        {"#a": ["30700:<pubkey>:<plugin-id>"],
         "kinds": [1111, 1985, 9735, 5]}

  - Persist every event we receive into the local SQLite cache so
    later cold-opens are instant.
  - Emit a Qt signal so the UI knows to recompute its snapshot.
  - Tear down cleanly when the user navigates away.

Subscriptions are deduplicated per ``anchor.coord``. If the user
flips between two plugins quickly, we cancel the previous sub and
open a new one. Reopening the same plugin reuses the existing sub.

This module owns *no* Qt widgets. The detail pane wires up signals
in one place; nothing else in the social/ tree imports Qt.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from PySide6.QtCore import QObject, Signal

from nostr import DEFAULT_RELAYS
from nostr.events import verify_event
from nostr.relay import RelayPool, Subscription

from ._seeds import seed_relays
from .cache import EngagementCache
from .models import PluginAnchor
from .outbox_router import OutboxRouter
from .parser import (
    NIP09_DELETION_KIND,
    NIP22_COMMENT_KIND,
    NIP32_LABEL_KIND,
    NIP57_ZAP_RECEIPT_KIND,
)


# Kinds we ever want to receive for an engagement subscription.
# Trust list events (mute / contacts / NIP-05 metadata) come from
# separate per-user subscriptions in the publisher / verifier path,
# not here.
_ENGAGEMENT_KINDS: tuple[int, ...] = (
    NIP22_COMMENT_KIND,
    NIP32_LABEL_KIND,
    NIP57_ZAP_RECEIPT_KIND,
    NIP09_DELETION_KIND,
)


class EngagementFetcher(QObject):
    """Owns the live subscription set for engagement events.

    One instance per editor process. Multiple plugin detail panes can
    share it concurrently; each ``watch(anchor)`` call refcounts so
    closing one panel never tears down a sub another panel still
    needs.
    """

    # Fires every time at least one fresh event landed for an anchor.
    # Listeners use this as a "go recompute your snapshot" pulse; the
    # raw event is already in the cache.
    snapshot_changed = Signal(object)   # PluginAnchor

    # Fires once when the initial backlog for an anchor's subscription
    # has fully arrived (every relay sent EOSE). Lets the UI swap
    # "Loading reviews..." for the actual content even when nothing
    # has been published yet.
    initial_load_done = Signal(object)  # PluginAnchor

    def __init__(
        self,
        cache: EngagementCache,
        *,
        relay_pool: RelayPool,
        extra_relays: Iterable[str] = (),
        outbox_router: Optional[OutboxRouter] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._cache = cache
        self._relay_pool = relay_pool
        # Defensive copy; we never mutate the caller's iterable.
        self._extra_relays: tuple[str, ...] = tuple(extra_relays)
        # Optional NIP-65 router. When ``None`` we behave like the
        # pre-outbox fetcher: union of extras + DEFAULT_RELAYS.
        self._router = outbox_router
        # Per-anchor state.
        self._watches: Dict[str, _Watch] = {}

    # ----------------------------------------------------------------------
    # Public lifecycle
    # ----------------------------------------------------------------------

    def watch(self, anchor: PluginAnchor) -> None:
        """Open (or refcount up) a subscription for ``anchor``.

        Safe to call repeatedly; callers don't need to track whether a
        sub already exists. Use one matching ``unwatch`` per ``watch``
        to release the reference.
        """
        existing = self._watches.get(anchor.coord)
        if existing is not None:
            existing.refs += 1
            return
        watch = _Watch(anchor=anchor, refs=1)
        watch.subscription = self._open_subscription(anchor, watch)
        self._watches[anchor.coord] = watch

    def unwatch(self, anchor: PluginAnchor) -> None:
        """Drop one reference for ``anchor``.

        Closes the underlying subscription when the refcount reaches
        zero. Idempotent for unknown anchors so callers can call
        ``unwatch`` from a ``finally`` without checking.
        """
        watch = self._watches.get(anchor.coord)
        if watch is None:
            return
        watch.refs -= 1
        if watch.refs <= 0:
            self._close_watch(watch)
            self._watches.pop(anchor.coord, None)

    def close_all(self) -> None:
        """Tear down every open subscription. Used at editor shutdown."""
        for watch in list(self._watches.values()):
            self._close_watch(watch)
        self._watches.clear()

    # ----------------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------------

    def _open_subscription(
        self,
        anchor: PluginAnchor,
        watch: "_Watch",
    ) -> Subscription:
        urls = self._relays_for(anchor)
        filters = [{
            "kinds": list(_ENGAGEMENT_KINDS),
            "#a": [anchor.coord],
        }]
        sub = self._relay_pool.subscribe(urls, filters)
        # Hold the connection objects on the watch so the slots can be
        # disconnected in tear-down.
        sub.event.connect(lambda e, a=anchor: self._handle_event(a, e))
        sub.eose.connect(lambda a=anchor: self.initial_load_done.emit(a))
        return sub

    def _relays_for(self, anchor: PluginAnchor) -> List[str]:
        """Return the relays we'll subscribe on for ``anchor``.

        The events we're subscribing to (kind:1985 ratings, kind:1111
        comments, kind:9735 zaps, kind:5 deletions) are authored by
        **arbitrary** raters — we don't know who they are ahead of
        time, so NIP-65 outbox routing doesn't apply. Use the seed
        set (DEFAULT_RELAYS + user-curated extras), which is the
        anonymous-discovery channel.

        The plugin author's own listing event (kind:30700) is fetched
        separately by the curator-index path, where outbox does apply
        (the author is known).
        """
        seeds = self._seed_relays()
        if self._router is None:
            return seeds
        plan = self._router.relays_for_anonymous_discovery(seeds=seeds)
        return plan.relays

    def _seed_relays(self) -> List[str]:
        """Delegate to the package-level helper."""
        return seed_relays(self._extra_relays)

    def _handle_event(self, anchor: PluginAnchor, event: dict) -> None:
        """One event arrived from a relay subscription.

        Validation steps:
          1. NIP-01 signature must verify. We never trust the
             ``["EVENT", ...]`` content frame from a relay without
             this check; a hostile relay could otherwise serve
             arbitrary events.
          2. The event's kind must be one of our expected engagement
             kinds. Filters narrow the wire but relays can lie.
          3. Cache the raw event. The parser layer runs lazily inside
             the aggregator; if a parse fails we keep the raw row so
             a future spec relaxation can pick it up without a
             re-fetch.
        """
        if not isinstance(event, dict):
            return
        if event.get("kind") not in _ENGAGEMENT_KINDS:
            return
        if not verify_event(event):
            return
        self._cache.upsert_event(anchor=anchor, event=event)
        self.snapshot_changed.emit(anchor)

    def _close_watch(self, watch: "_Watch") -> None:
        sub = watch.subscription
        if sub is not None:
            try:
                sub.close()
            except Exception:  # noqa: BLE001
                # Defensive: closing twice is fine. We never want
                # tear-down to crash the editor.
                pass
            watch.subscription = None


# --------------------------------------------------------------------------- #
# Per-anchor watch record                                                     #
# --------------------------------------------------------------------------- #

class _Watch:
    """One open subscription plus its refcount.

    Plain object rather than a dataclass because the ``subscription``
    field is a Qt object whose default ``__eq__`` we want unchanged.
    """

    __slots__ = ("anchor", "refs", "subscription")

    def __init__(self, *, anchor: PluginAnchor, refs: int) -> None:
        self.anchor = anchor
        self.refs = refs
        self.subscription: Optional[Subscription] = None
