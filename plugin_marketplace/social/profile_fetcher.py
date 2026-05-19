"""Fetch kind:0 (NIP-01) metadata for everyone in an engagement thread.

Distinct from ``nostr.metadata.ProfileMetadataFetcher`` which scopes
itself to the *user's own* profile. This fetcher takes a list of
arbitrary pubkeys (the people who commented or rated on a plugin)
and resolves them in a single batched relay subscription.

Pipeline per pubkey:

  1. Look up the cached profile row; skip if it's already fresh.
  2. Open one ``{"kinds":[0], "authors":[pk1, pk2, ...]}`` REQ.
  3. For each event that arrives, parse the ``content`` JSON, write
     ``display_name`` / ``picture_url`` / ``nip05`` to the cache.
  4. Emit ``profile_updated(pubkey)`` so the UI can re-render with
     real names and (where present) trigger an avatar download.

The fetcher never downloads avatar images itself. That's the
``AvatarBatchLoader``'s job; the marketplace dialog wires it once
the URL lands in the cache.
"""

from __future__ import annotations

import json
import time
from typing import Iterable, List, Optional, Set

from PySide6.QtCore import QObject, QTimer, Signal

from nostr import DEFAULT_RELAYS
from nostr.relay import RelayPool, Subscription

from ._seeds import seed_relays
from .cache import EngagementCache
from .outbox_router import OutboxRouter


# Profile metadata is replaceable but rarely changes; the existing
# ``nostr.metadata`` module uses a similar TTL. Match it for symmetry.
_PROFILE_TTL_S: int = 24 * 60 * 60

# Idle delay before we tear down a finished batch subscription. We
# leave it open briefly so multiple ``request_many`` calls in quick
# succession can piggyback on the same REQ instead of re-handshaking.
_BATCH_LINGER_MS: int = 1500


class CommenterProfileFetcher(QObject):
    """Batched kind:0 fetcher for commenter / rater / zapper pubkeys."""

    profile_updated = Signal(str)   # pubkey

    def __init__(
        self,
        *,
        relay_pool: RelayPool,
        cache: EngagementCache,
        extra_relays: Iterable[str] = (),
        outbox_router: Optional[OutboxRouter] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._relay_pool = relay_pool
        self._cache = cache
        self._extra_relays: tuple[str, ...] = tuple(extra_relays)
        self._router = outbox_router
        self._inflight: Set[str] = set()
        # Active subscriptions awaiting EOSE-then-linger close.
        self._subs: List[Subscription] = []

    # ----------------------------------------------------------------------
    def request_many(self, pubkeys: Iterable[str]) -> None:
        """Fetch kind:0 for any of ``pubkeys`` whose cached row is stale.

        Pubkeys already in flight are skipped so a flood of
        ``request_many`` calls (e.g. one per refreshed snapshot) only
        results in one network round-trip per pubkey per TTL window.
        """
        wanted: List[str] = []
        now = int(time.time())
        for pubkey in pubkeys:
            if not pubkey or pubkey in self._inflight:
                continue
            existing = self._cache.get_profile(pubkey)
            if existing is not None and (now - int(existing.get("updated_at", 0))) < _PROFILE_TTL_S:
                continue
            wanted.append(pubkey)
            self._inflight.add(pubkey)
        if not wanted:
            return
        self._open_subscription(wanted)

    def close_all(self) -> None:
        for sub in self._subs:
            try:
                sub.close()
            except Exception:  # noqa: BLE001
                pass
        self._subs.clear()
        self._inflight.clear()

    # ----------------------------------------------------------------------
    def _open_subscription(self, pubkeys: List[str]) -> None:
        urls = self._relays_to_use(pubkeys)
        filters = [{"kinds": [0], "authors": list(pubkeys)}]
        sub = self._relay_pool.subscribe(urls, filters)
        sub.event.connect(self._handle_event)

        # Tear down after EOSE + a short linger so a follow-up
        # ``request_many`` for the same anchor doesn't reopen a brand
        # new socket immediately afterwards.
        def _on_eose() -> None:
            QTimer.singleShot(_BATCH_LINGER_MS, lambda: self._retire(sub, pubkeys))
        sub.eose.connect(_on_eose)
        self._subs.append(sub)

    def _retire(self, sub: Subscription, pubkeys: List[str]) -> None:
        try:
            sub.close()
        except Exception:  # noqa: BLE001
            pass
        if sub in self._subs:
            self._subs.remove(sub)
        # Release the in-flight gate so a future TTL miss can re-fetch.
        for pk in pubkeys:
            self._inflight.discard(pk)

    def _relays_to_use(self, pubkeys: Optional[List[str]] = None) -> List[str]:
        """Outbox routing for kind:0 fetches.

        Each profile owner declares where they publish via NIP-65;
        fetching their kind:0 from those relays first is what makes
        the outbox model work end-to-end.

        Falls back to seeds for any pubkey without a cached 10002 so
        we still pick up brand-new profiles or accounts that haven't
        published a relay list yet.
        """
        seeds = self._seed_relays()
        if self._router is None or not pubkeys:
            return seeds
        plan = self._router.relays_for_read(pubkeys, seeds=seeds)
        return plan.relays

    def _seed_relays(self) -> List[str]:
        return seed_relays(self._extra_relays)

    def _handle_event(self, event: dict) -> None:
        """Parse a single kind:0 event and persist its metadata."""
        if not isinstance(event, dict) or event.get("kind") != 0:
            return
        pubkey = event.get("pubkey")
        if not isinstance(pubkey, str) or len(pubkey) != 64:
            return
        content = event.get("content", "")
        if not isinstance(content, str):
            return
        try:
            metadata = json.loads(content) if content else {}
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            return

        display_name = _coerce_str(metadata.get("display_name")) or _coerce_str(metadata.get("name"))
        picture_url = _coerce_str(metadata.get("picture"))
        nip05 = _coerce_str(metadata.get("nip05"))

        # Refuse non-HTTPS picture URLs so a malicious profile can't
        # make us GET unencrypted hosts when AvatarBatchLoader picks
        # this up later.
        if picture_url and not picture_url.lower().startswith(("https://", "http://")):
            picture_url = ""

        self._cache.store_profile(
            pubkey,
            display_name=display_name,
            picture_url=picture_url,
            nip05=nip05,
        )
        self.profile_updated.emit(pubkey)


def _coerce_str(value) -> str:
    """Return a stripped string or an empty string for anything else.

    Profile metadata is user-generated JSON; we trust nothing about
    its shape until we've coerced each field through this gate.
    """
    if not isinstance(value, str):
        return ""
    return value.strip()
