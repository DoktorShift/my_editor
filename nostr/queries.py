# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One-shot event queries layered on top of RelayPool subscriptions.

The pattern is the same for kind 0 (user metadata) and kind 10002 (relay
list): subscribe with a tight filter, wait for EOSE or a hard timeout,
hand the caller the most recent event by ``created_at``. We isolate that
shape here so the outbox and metadata loaders both stay tiny.

``fetch_addressable_events`` is a variant for addressable / parameterized-
replaceable kinds (30000–39999, e.g. NIP-23 long-form, NIP-37 drafts):
events are deduplicated by ``(kind, pubkey, d-tag)`` and the newest
event per tuple is returned together.

``fetch_events`` is the careful one: it hands back every validly signed
event the filters match, which relays sent each one, and which relays
answered, refused or could not be reached, so a caller can tell "nothing
there" from "nobody answered" and knows where each event is kept.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, QTimer

from . import events as nostr_events
from .outbox.policy import dedupe_relays
from .relay import RelayPool, Subscription

logger = logging.getLogger(__name__)

# How many copies one query checks at most, so a relay flooding it with
# events costs a bounded amount of work. Far above what any filter of
# this app asks for.
MAX_COPIES: int = 5_000


# --------------------------------------------------------------------------- #
# Events, with the relays that have them                                      #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Fetched:
    """What one query brought back."""

    events: Tuple[dict, ...] = ()       # one validly signed copy per id, in arrival order
    seen_on: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)  # id -> relays
    answered: Tuple[str, ...] = ()      # relays that ended their stored events (EOSE)
    refused: Tuple[str, ...] = ()       # relays that closed the request or were not reached

    def relays_of(self, event_id: str) -> Tuple[str, ...]:
        """The relays that sent the event with this id."""
        return self.seen_on.get(event_id, ())


def fetch_events(
    pool: RelayPool,
    relays: Sequence[str],
    filters: List[Dict[str, Any]],
    on_done: Callable[[Fetched], None],
    *,
    accept: Optional[Callable[[dict], bool]] = None,
    timeout_ms: int = 8_000,
    parent: Optional[QObject] = None,
) -> "_EventsQuery":
    """Ask ``relays`` once for what ``filters`` match; ``on_done`` once.

    The query ends as soon as every relay has ended (its stored events,
    a refusal, or a lost connection), and at the latest after
    ``timeout_ms``; a relay that has not ended by then is in neither
    ``answered`` nor ``refused``.

    Only validly signed events count, and of them only those ``accept``
    takes (the author and kinds the caller asked for). Each copy is
    checked once, so a relay sending a forged copy of a real event
    cannot hide the real one, and sending it again costs nothing.

    The question lives as long as ``parent`` (or the returned object,
    when there is no parent) and lets go of itself once answered.
    """
    return _EventsQuery(pool, dedupe_relays(relays), filters, on_done, accept,
                        timeout_ms, parent)


class _EventsQuery(QObject):
    """Internal helper, see ``fetch_events``."""

    def __init__(self, pool: RelayPool, relays: List[str], filters: List[Dict[str, Any]],
                 on_done: Callable[[Fetched], None],
                 accept: Optional[Callable[[dict], bool]], timeout_ms: int,
                 parent: Optional[QObject], *, max_copies: int = MAX_COPIES) -> None:
        super().__init__(parent)
        self._relays = relays
        self._on_done = on_done
        self._accept = accept
        self._max_copies = max_copies
        self._events: Dict[str, dict] = {}
        self._seen_on: Dict[str, List[str]] = {}
        self._verdicts: Dict[str, bool] = {}     # fingerprint -> whether it counts
        self._flooded = False
        self._answered: List[str] = []
        self._refused: List[str] = []
        self._finished = False
        self._sub: Optional[Subscription] = None
        self._timer: Optional[QTimer] = None
        if not relays or not filters:
            QTimer.singleShot(0, self._finish)
            return
        self._sub = pool.subscribe(relays, list(filters))
        self._sub.relay_event.connect(self._on_event)
        self._sub.relay_eose.connect(self._on_relay_eose)
        self._sub.relay_closed.connect(self._on_relay_refused)
        self._sub.relay_failed.connect(self._on_relay_refused)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._finish)
        self._timer.start(timeout_ms)

    def _on_event(self, url: str, event: dict) -> None:
        if self._finished or not isinstance(event, dict):
            return
        event_id = event.get("id")
        if not isinstance(event_id, str):
            return
        fingerprint = nostr_events.fingerprint(event)
        counts = self._verdicts.get(fingerprint)
        if counts is None:
            if len(self._verdicts) >= self._max_copies:
                if not self._flooded:
                    self._flooded = True
                    logger.warning("a query received more than %d copies; the rest is ignored",
                                   self._max_copies)
                return
            counts = nostr_events.verify_event(event) and (
                self._accept is None or bool(self._accept(event)))
            self._verdicts[fingerprint] = counts
        if not counts:
            return
        self._events.setdefault(event_id, event)
        seen = self._seen_on.setdefault(event_id, [])
        if url not in seen:
            seen.append(url)

    def _on_relay_eose(self, url: str) -> None:
        if url not in self._answered and url not in self._refused:
            self._answered.append(url)
        self._finish_when_settled()

    def _on_relay_refused(self, url: str, _reason: str = "") -> None:
        if url not in self._answered and url not in self._refused:
            self._refused.append(url)
        self._finish_when_settled()

    def _finish_when_settled(self) -> None:
        if set(self._relays) <= set(self._answered) | set(self._refused):
            self._finish()

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        if self._timer is not None:
            self._timer.stop()
        if self._sub is not None:
            self._sub.close()
            self._sub.deleteLater()
        result = Fetched(events=tuple(self._events.values()),
                         seen_on={k: tuple(v) for k, v in self._seen_on.items()},
                         answered=tuple(self._answered), refused=tuple(self._refused))
        try:
            self._on_done(result)
        finally:
            # A finished query belongs to nobody (outbox/lookup.py says why).
            self.setParent(None)
            self.deleteLater()


def fetch_latest_event(
    pool: RelayPool,
    relays: List[str],
    filters: List[Dict[str, Any]],
    on_done: Callable[[Optional[dict]], None],
    *,
    timeout_ms: int = 6_000,
    parent: Optional[QObject] = None,
) -> "_LatestEventQuery":
    """Subscribe, collect matching events until EOSE-or-timeout, callback once.

    ``on_done`` receives the event with the largest ``created_at`` value, or
    ``None`` if no event arrived. It is invoked exactly once.

    The returned object owns the subscription and timer; the caller may
    discard it. It cleans itself up after firing the callback.
    """
    return _LatestEventQuery(pool, relays, filters, on_done, timeout_ms, parent)


class _LatestEventQuery(QObject):
    """Internal helper, see ``fetch_latest_event`` for the public API."""

    def __init__(
        self,
        pool: RelayPool,
        relays: List[str],
        filters: List[Dict[str, Any]],
        on_done: Callable[[Optional[dict]], None],
        timeout_ms: int,
        parent: Optional[QObject],
    ) -> None:
        super().__init__(parent)
        self._on_done = on_done
        self._best: Optional[dict] = None
        self._finished = False

        self._sub = pool.subscribe(relays, filters)
        self._sub.event.connect(self._on_event)
        self._sub.eose.connect(self._finish)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(self._finish)
        self._timer.start()

    def _on_event(self, event: dict) -> None:
        # Replaceable-event semantics: keep the newest by created_at.
        # Defense in depth: events can still race in between
        # ``_sub.close()`` and the eventual ``deleteLater`` cycle.
        if self._finished:
            return
        try:
            ts = int(event.get("created_at", 0))
        except (TypeError, ValueError):
            return
        if self._best is None or ts > int(self._best.get("created_at", 0)):
            self._best = event

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._timer.stop()
        self._sub.close()
        try:
            self._on_done(self._best)
        finally:
            self.deleteLater()


# --------------------------------------------------------------------------- #
# Addressable-event bulk fetch                                                #
# --------------------------------------------------------------------------- #

# (kind, pubkey, d-tag value)
AddressableKey = Tuple[int, str, str]


def fetch_addressable_events(
    pool: RelayPool,
    relays: List[str],
    filters: List[Dict[str, Any]],
    on_done: Callable[[List[dict]], None],
    *,
    timeout_ms: int = 6_000,
    parent: Optional[QObject] = None,
) -> "_AddressableEventsQuery":
    """Collect newest-per-(kind,pubkey,d) events matching ``filters``.

    Use this for addressable kinds where the user holds many distinct
    documents: every NIP-37 draft is its own ``d``-tag, and we want the
    newest version of *each* draft, not just the newest event overall.

    ``on_done`` is invoked exactly once with the list of winning events
    (order: descending ``created_at``). Events without a ``d``-tag are
    silently skipped: they're not addressable in the protocol sense
    and we couldn't dedupe them anyway.
    """
    return _AddressableEventsQuery(pool, relays, filters, on_done, timeout_ms, parent)


class _AddressableEventsQuery(QObject):
    """Internal helper, see ``fetch_addressable_events``."""

    def __init__(
        self,
        pool: RelayPool,
        relays: List[str],
        filters: List[Dict[str, Any]],
        on_done: Callable[[List[dict]], None],
        timeout_ms: int,
        parent: Optional[QObject],
    ) -> None:
        super().__init__(parent)
        self._on_done = on_done
        self._best: Dict[AddressableKey, dict] = {}
        self._finished = False

        self._sub = pool.subscribe(relays, filters)
        self._sub.event.connect(self._on_event)
        self._sub.eose.connect(self._finish)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(self._finish)
        self._timer.start()

    def _on_event(self, event: dict) -> None:
        if self._finished:
            return
        try:
            kind = int(event.get("kind", -1))
            pubkey = str(event.get("pubkey", "")).lower()
            created_at = int(event.get("created_at", 0))
        except (TypeError, ValueError):
            return
        if not pubkey:
            return
        d_value: Optional[str] = None
        for tag in event.get("tags", []):
            if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "d":
                d_value = str(tag[1])
                break
        # An empty d-tag is technically the "no parameter" form of an
        # addressable event. Treat it the same as missing: for the
        # drafts use case we explicitly require a non-empty identifier
        # so callers and dedup are aligned with ``parse_wrap_event``.
        if not d_value:
            return

        key: AddressableKey = (kind, pubkey, d_value)
        existing = self._best.get(key)
        if existing is None or created_at > int(existing.get("created_at", 0)):
            self._best[key] = event

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._timer.stop()
        self._sub.close()
        # Newest first: the drafts panel scrolls from most-recent down.
        ordered = sorted(
            self._best.values(),
            key=lambda e: int(e.get("created_at", 0)),
            reverse=True,
        )
        try:
            self._on_done(ordered)
        finally:
            self.deleteLater()
