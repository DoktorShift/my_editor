"""NIP-65 outbox routing for the marketplace social layer.

The outbox model (NIP-65, popularized by the nostrify gossip docs)
says: a user's ``kind:10002`` declares which relays they write to
(outbox) and which relays accept mentions for them (inbox). Clients
that want to find a user's content should query the user's *write*
relays. Clients that want to *reach* a user should publish to that
user's *read* relays.

Hardcoded relay lists are wrong because they centralize Nostr around
a small set of well-known relays, miss authors who publish elsewhere,
and ignore each user's stated routing preferences.

This module is the thin glue between the marketplace's read/write
paths and the editor-wide ``nostr.outbox.RelayListCache``.

Key entry points:

  - ``relays_for_read(author_pubkeys, *, seeds)``: which relays to ask
    when fetching events authored by these users. Falls back to
    ``seeds`` for anyone without a cached 10002 and triggers a
    background fetch so the next query benefits.

  - ``relays_for_write(viewer_pubkey, target_pubkeys, *, seeds)``: where
    to publish an event by ``viewer_pubkey`` that mentions or is
    addressed to ``target_pubkeys``. Combines the viewer's write
    relays with each target's read relays. Includes ``seeds`` as a
    backstop so publishing never gets routed to zero relays.

  - ``relays_for_anonymous_discovery(*, seeds)``: returns a curated
    seed set for queries where the author is unknown ahead of time
    (e.g. "fetch every rating for plugin X"). Outbox doesn't apply
    when there's no author key to look up.

The 10-relay cap is the operational ceiling we picked across the
project; NIP-65 doesn't mandate a number but recommends keeping the
list small (2-4 per category). Inputs always normalize to lower-case
host comparisons so trivial URL variations don't double-fan-out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Set

from nostr.outbox import RelayList, RelayListCache, parse_relay_list


# Operational fan-out cap. NIP-65 itself doesn't specify a number.
_FAN_OUT_CAP: int = 10


@dataclass(frozen=True)
class RoutingPlan:
    """The relays a single call should hit, plus any pubkeys whose
    10002 was missing so callers can trigger a refresh.

    ``relays`` are deduped and capped at ``_FAN_OUT_CAP``. Order is
    deterministic: viewer/preferred relays first, then target-derived,
    then seeds, so the dialog's "publish progress" UI shows the most
    important relay first.

    ``unresolved_pubkeys`` is a hint to the cache fetcher: pubkeys
    whose 10002 we didn't have and should fetch before the next call.
    Callers can ignore it; the cache will eventually populate via the
    normal background refresh.
    """

    relays: List[str]
    unresolved_pubkeys: List[str]


class OutboxRouter:
    """Resolve NIP-65 routing for marketplace reads and writes.

    Single instance per editor process. Holds a reference to the
    existing ``RelayListCache`` (which already coalesces concurrent
    fetches, caches with a TTL, and handles the empty-result case).

    Thread-safety: callers run on the Qt main thread; the cache itself
    is built around ``QObject`` so signals come back on the main loop.
    """

    def __init__(self, relay_list_cache: Optional[RelayListCache]) -> None:
        self._cache = relay_list_cache

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def relays_for_read(
        self,
        author_pubkeys: Iterable[str],
        *,
        seeds: Sequence[str],
    ) -> RoutingPlan:
        """Return the relays to query for events authored by ``author_pubkeys``.

        Honours NIP-65 author intent:

          - If every author in the set has a published kind:10002,
            we use ONLY their declared write relays. Seeds are not
            unioned — the author chose their relays for a reason.
          - If at least one author has no 10002, we fall back to a
            union of (resolved authors' write relays) + seeds so the
            unresolved author's content can still be discovered.
          - With no resolved authors at all, we use only seeds.

        Unresolved pubkeys are reported so the caller can trigger a
        background prewarm; the next query will use their NIP-65
        choices once the cache fills.
        """
        author_relays: List[str] = []
        seen_author: Set[str] = set()
        unresolved: List[str] = []
        resolved_count = 0
        for pubkey in author_pubkeys:
            if not pubkey:
                continue
            relay_list = self._cached(pubkey)
            if relay_list is None or relay_list.is_empty:
                unresolved.append(pubkey)
                continue
            resolved_count += 1
            for url in relay_list.write:
                self._maybe_add(url, author_relays, seen_author)

        # Three branches per the docstring above.
        if resolved_count > 0 and not unresolved:
            return RoutingPlan(
                relays=author_relays[:_FAN_OUT_CAP],
                unresolved_pubkeys=[],
            )
        relays: List[str] = list(author_relays)
        seen: Set[str] = set(seen_author)
        for url in seeds:
            self._maybe_add(url, relays, seen)
        return RoutingPlan(
            relays=relays[:_FAN_OUT_CAP],
            unresolved_pubkeys=unresolved,
        )

    def relays_for_anonymous_discovery(
        self,
        *,
        seeds: Sequence[str],
    ) -> RoutingPlan:
        """Routing for queries where the author is unknown ahead of time.

        Examples: "all kind:1985 events for this plugin's coord", "all
        zap receipts naming this anchor." Outbox routing doesn't apply
        because there's no author key to resolve. We use the curated
        seed set and rely on dynamic discovery to find new participants.
        """
        relays: List[str] = []
        seen: Set[str] = set()
        for url in seeds:
            self._maybe_add(url, relays, seen)
        return RoutingPlan(
            relays=relays[:_FAN_OUT_CAP],
            unresolved_pubkeys=[],
        )

    # ------------------------------------------------------------------
    # Write paths
    # ------------------------------------------------------------------

    def relays_for_write(
        self,
        viewer_pubkey: Optional[str],
        target_pubkeys: Iterable[str],
        *,
        seeds: Sequence[str],
    ) -> RoutingPlan:
        """Return the relays to publish to.

        Order:

          1. Viewer's own write relays (highest priority — these are
             the relays the viewer's followers query).
          2. Each target's read relays (so mentions actually reach
             them).
          3. Seeds as a backstop.

        Without (2), a comment or rating may never be seen by the
        plugin author who's subscribed only to their own read relays.
        Without (1), a freshly-published event won't surface to the
        viewer's own followers via the outbox.
        """
        relays: List[str] = []
        seen: Set[str] = set()
        unresolved: List[str] = []

        if viewer_pubkey:
            viewer_list = self._cached(viewer_pubkey)
            if viewer_list is None or viewer_list.is_empty:
                unresolved.append(viewer_pubkey)
            else:
                for url in viewer_list.write:
                    self._maybe_add(url, relays, seen)

        for pubkey in target_pubkeys:
            if not pubkey or pubkey == viewer_pubkey:
                continue
            target_list = self._cached(pubkey)
            if target_list is None or target_list.is_empty:
                unresolved.append(pubkey)
                continue
            for url in target_list.read:
                self._maybe_add(url, relays, seen)

        for url in seeds:
            self._maybe_add(url, relays, seen)
        return RoutingPlan(
            relays=relays[:_FAN_OUT_CAP],
            unresolved_pubkeys=unresolved,
        )

    # ------------------------------------------------------------------
    # Cache prewarm
    # ------------------------------------------------------------------

    def prewarm(
        self,
        pubkeys: Iterable[str],
        *,
        seeds: Sequence[str],
        on_any_resolved=None,
    ) -> None:
        """Best-effort background fetch of NIP-65 lists for ``pubkeys``.

        Called by the dialog when it knows a plugin's commenters /
        author pubkeys ahead of opening the panel. The cache handles
        coalescing so spamming this is safe.

        ``on_any_resolved`` fires whenever a prewarm fetch resolves a
        non-empty relay list. Callers use this to re-push their
        snapshot so the next read uses author-aware routing instead
        of the seed fallback.
        """
        if self._cache is None:
            return
        relays = list(seeds) or []
        if not relays:
            return
        for pubkey in pubkeys:
            if not pubkey:
                continue
            if self._cache.get_cached(pubkey) is not None:
                continue

            def _on_done(relay_list, callback=on_any_resolved) -> None:
                # ``RelayList.is_empty`` is True both for "service
                # didn't return a 10002" and for "10002 had zero r
                # tags." We only fire the callback when something
                # actually landed, so the dialog avoids a re-render
                # loop on empty results.
                if callback is None:
                    return
                if relay_list is None or relay_list.is_empty:
                    return
                try:
                    callback(pubkey)
                except Exception:  # noqa: BLE001 — best-effort
                    pass

            self._cache.fetch(pubkey, relays, on_done=_on_done)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _cached(self, pubkey: str) -> Optional[RelayList]:
        if self._cache is None:
            return None
        return self._cache.get_cached(pubkey)

    @staticmethod
    def _maybe_add(url: str, out: List[str], seen: Set[str]) -> None:
        clean = (url or "").strip().rstrip("/")
        if not clean:
            return
        key = clean.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(clean)


# --------------------------------------------------------------------------- #
# Re-export so callers can synthesize RelayList values in tests without
# importing nostr internals.
# --------------------------------------------------------------------------- #

__all__ = [
    "OutboxRouter",
    "RoutingPlan",
    "RelayList",
    "parse_relay_list",
]
