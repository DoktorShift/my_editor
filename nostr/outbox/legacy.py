# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The relay-selection code that predates nostr/outbox (being replaced).

Kept only while call sites move to RelayDirectory and the policy
functions; nothing new should use it. Each name here has a successor:

    RelayListCache               -> RelayDirectory

What follows is the original description.

NIP-65 outbox: per-pubkey relay-list cache.

Spec: https://github.com/nostr-protocol/nips/blob/master/65.md

A user's NIP-65 event (``kind:10002``) is a list of ``["r", url, marker?]``
tags. Marker is ``"read"``, ``"write"``, or omitted (meaning both). We
extract the write set for publishing.

For publishing our own kind 1 notes, we use:

    dedup(DEFAULT_RELAYS ∪ user_write_relays)[:RELAY_CAP]

DEFAULT_RELAYS guarantees a known-good base set even when the user has
no published list yet; the union ensures the note also reaches the
relays the user has chosen to advertise, so other clients querying their
pubkey via the outbox model find it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from PySide6.QtCore import QObject

from .. import DEFAULT_RELAYS
from ..queries import fetch_latest_event
from ..relay import RelayPool
from .policy import RelayList, parse_relay_list


# Per the NIP-65 spec, lists should stay small (2-4 per category), so clamping
# the union at 10 keeps publishing fast even if a user has a sprawling list.
RELAY_CAP: int = 10

# Cache TTLs. Empty results retry sooner so a user who just published their
# kind:10002 sees their preferences honoured on the next publish.
_TTL_HIT_S: int = 30 * 60
_TTL_EMPTY_S: int = 3 * 60


# --------------------------------------------------------------------------- #
# Cached fetcher                                                              #
# --------------------------------------------------------------------------- #

@dataclass
class _CacheEntry:
    relay_list: RelayList
    fetched_at: float
    empty: bool


class RelayListCache(QObject):
    """Fetches NIP-65 events on demand and caches them per pubkey.

    Callers pass in the relays to consult (typically ``DEFAULT_RELAYS`` plus
    the bunker relays for the profile). The fetch is asynchronous; callers
    receive the parsed ``RelayList`` via callback. Cached hits resolve
    synchronously on the next event-loop tick.
    """

    def __init__(self, pool: RelayPool, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._pool = pool
        self._cache: Dict[str, _CacheEntry] = {}
        self._inflight: Dict[str, List[Callable[[RelayList], None]]] = {}

    def clear(self) -> None:
        self._cache.clear()

    def invalidate(self, pubkey_hex: str) -> None:
        self._cache.pop(pubkey_hex, None)

    def get_cached(self, pubkey_hex: str) -> Optional[RelayList]:
        """Return the cached list if still fresh, else ``None``."""
        entry = self._cache.get(pubkey_hex)
        if entry is None:
            return None
        ttl = _TTL_EMPTY_S if entry.empty else _TTL_HIT_S
        if time.time() - entry.fetched_at > ttl:
            return None
        return entry.relay_list

    def fetch(
        self,
        pubkey_hex: str,
        relays: List[str],
        on_done: Callable[[RelayList], None],
        *,
        timeout_ms: int = 6_000,
    ) -> None:
        """Resolve the user's NIP-65 list (cached or fresh) and call ``on_done``."""
        cached = self.get_cached(pubkey_hex)
        if cached is not None:
            on_done(cached)
            return

        # Coalesce concurrent fetches so a flood of publishes doesn't
        # spam the same relays with identical REQs.
        waiters = self._inflight.get(pubkey_hex)
        if waiters is not None:
            waiters.append(on_done)
            return
        self._inflight[pubkey_hex] = [on_done]

        def _on_event(event: Optional[dict]) -> None:
            relay_list = parse_relay_list(event) if event else RelayList()
            self._cache[pubkey_hex] = _CacheEntry(
                relay_list=relay_list,
                fetched_at=time.time(),
                empty=relay_list.is_empty,
            )
            callbacks = self._inflight.pop(pubkey_hex, [])
            for cb in callbacks:
                try:
                    cb(relay_list)
                except Exception:  # noqa: BLE001, best-effort, don't swallow others
                    pass

        fetch_latest_event(
            self._pool,
            relays,
            filters=[{"kinds": [10002], "authors": [pubkey_hex], "limit": 1}],
            on_done=_on_event,
            timeout_ms=timeout_ms,
            parent=self,
        )
