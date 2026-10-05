# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""RelayDirectory: the one place that knows where anyone reads and writes.

It answers "where are this person's relays" (looked up, verified, cached)
and, through the pure rules in policy.py, "where does this go":

    publish_plan    a public note or article: the author's outbox, plus the
                    inbox of everyone it mentions (NIP-65)
    private_relays  drafts and other private records; the same set for
                    writing and reading, so other devices find them
                    (ask_private_relays asks it for a profile)
    outbox_of       where to read what someone else wrote

Caching. A FOUND list is trusted for half an hour; after that it is still
answered at once while a refresh runs (stale while revalidate). A refresh
only ever replaces it with a newer validly signed list: a timeout or an
empty answer never downgrades a list we know. ABSENT is trusted for three
minutes, UNKNOWN for thirty seconds, just long enough not to hammer relays.

The user's own lists are kept on disk as the signed events themselves
(re-verified on load), so routing is right from the first second after a
launch, even offline. Everyone else's are kept in memory only.

Every list MyEditor publishes goes through ``remember``, which seeds the
cache with the known-good event instead of asking relays that may not
have it yet.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, QTimer, Signal

from .. import events
from . import defaults, policy
from .lookup import Lookup, fetch_replaceable
from .policy import KIND_RELAY_LIST, LookupState, RelayList

RELAY_LISTS_FILE = Path.home() / ".config" / "my_editor" / "nostr_relay_lists.json"

logger = logging.getLogger(__name__)

_TTL = {
    LookupState.FOUND: defaults.TTL_FOUND_S,
    LookupState.ABSENT: defaults.TTL_ABSENT_S,
    LookupState.UNKNOWN: defaults.TTL_UNKNOWN_S,
}


class RelayDirectory(QObject):
    """Relay lists by pubkey, and the routing built on them."""

    changed = Signal(str)       # pubkey whose known list became newer

    def __init__(self, pool, *, query=fetch_replaceable,
                 store_path: Optional[Path] = RELAY_LISTS_FILE,
                 own_pubkeys: Callable[[], Iterable[str]] = tuple,
                 clock: Callable[[], float] = time.time,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._pool = pool
        self._query = query
        self._store_path = Path(store_path) if store_path else None
        self._own_pubkeys = own_pubkeys
        self._clock = clock
        self._entries: Dict[str, RelayList] = {}
        self._waiting: Dict[str, List[Callable[[RelayList], None]]] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # Knowing                                                            #
    # ------------------------------------------------------------------ #

    def cached(self, pubkey: str) -> RelayList:
        """What is known now, however old; UNKNOWN when nothing is."""
        return self._entries.get((pubkey or "").lower()) or RelayList()

    def lookup(self, pubkey: str, on_done: Callable[[RelayList], None], *,
               hints: Sequence[str] = (), fresh: bool = False,
               timeout_ms: int = 6_000) -> None:
        """The person's relay list. Answered from cache when it is fresh; a
        stale FOUND list is answered at once and refreshed behind it."""
        key = (pubkey or "").lower()
        entry = self._entries.get(key)
        if entry is not None and not fresh:
            if not self._expired(entry):
                QTimer.singleShot(0, lambda: on_done(entry))
                return
            if entry.found:
                QTimer.singleShot(0, lambda: on_done(entry))
                self._start(key, hints, timeout_ms, None)
                return
        self._start(key, hints, timeout_ms, on_done)

    def lookup_many(self, pubkeys: Sequence[str],
                    on_done: Callable[[Dict[str, RelayList]], None], *,
                    timeout_ms: int = 3_000) -> None:
        """Several people's lists at once (the people a note mentions)."""
        keys = list(dict.fromkeys(p.lower() for p in pubkeys if p))[:defaults.MENTION_LOOKUP_CAP]
        result: Dict[str, RelayList] = {}
        if not keys:
            QTimer.singleShot(0, lambda: on_done(result))
            return
        remaining = {"n": len(keys)}

        def one(key):
            def done(relay_list):
                result[key] = relay_list
                remaining["n"] -= 1
                if remaining["n"] == 0:
                    on_done(result)
            return done

        for key in keys:
            self.lookup(key, one(key), timeout_ms=timeout_ms)

    def remember(self, event: dict) -> bool:
        """Take a relay list we hold (one we just published, or were handed).
        Only a validly signed one newer than what is known is kept."""
        if not isinstance(event, dict) or event.get("kind") != KIND_RELAY_LIST:
            return False
        if policy.created_at_of(event) is None or not events.verify_event(event):
            return False
        key = str(event.get("pubkey", "")).lower()
        current = self._entries.get(key)
        if current is not None and current.found and not policy.is_newer(event, current.event):
            return False
        self._store(key, self._found(event))
        return True

    def forget(self, pubkey: str) -> None:
        if self._entries.pop((pubkey or "").lower(), None) is not None:
            self._save()

    # ------------------------------------------------------------------ #
    # Routing                                                            #
    # ------------------------------------------------------------------ #

    def publish_plan(self, author: str, on_done: Callable[[policy.PublishPlan], None], *,
                     mentioned: Sequence[Tuple[str, str]] = (),
                     entitled: Sequence[str] = ()) -> None:
        """Where a public event goes; mentions are ``(pubkey, hint)`` pairs."""
        state: dict = {}
        hints = {p.lower(): h for p, h in mentioned if h}

        def maybe_done():
            if "author" in state and "mentions" in state:
                on_done(policy.plan_publish(state["author"], mentioned=state["mentions"],
                                            hints=hints, entitled=entitled))

        def got_author(relay_list):
            state["author"] = relay_list
            maybe_done()

        def got_mentions(lists):
            state["mentions"] = lists
            maybe_done()

        self.lookup(author, got_author)
        self.lookup_many([p for p, _hint in mentioned], got_mentions)

    def private_relays(self, author: str, on_done: Callable[[List[str]], None], *,
                       entitled: Sequence[str] = (), legacy: Sequence[str] = ()) -> None:
        self.lookup(author, lambda relay_list: on_done(policy.private_relays(
            relay_list, entitled=entitled, legacy=legacy)))

    def outbox_of(self, author: str, on_done: Callable[[List[str]], None], *,
                  hints: Sequence[str] = ()) -> None:
        self.lookup(author, lambda relay_list: on_done(policy.outbox_relays(
            relay_list, hints=hints)), hints=hints)

    def share_relay_list(self, author: str, relays: Sequence[str]) -> None:
        """Send the author's signed relay list to relays it just published to
        but that do not hold it (NIP-65). Needs no signer: it is the event
        we already have."""
        entry = self.cached(author)
        if not entry.found or entry.event is None:
            return
        listed = set(entry.write) | set(entry.read)
        extra = [r for r in policy.dedupe_relays(relays) if r not in listed]
        if extra:
            self._pool.publish(extra, entry.event)

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #

    def _expired(self, entry: RelayList) -> bool:
        return (self._clock() - entry.fetched_at) >= _TTL[entry.state]

    def _start(self, key: str, hints, timeout_ms: int, on_done) -> None:
        waiting = self._waiting.get(key)
        if waiting is not None:
            if on_done is not None:
                waiting.append(on_done)
            return
        self._waiting[key] = [on_done] if on_done is not None else []
        relays = policy.lookup_relays(hints=hints, known=self._entries.get(key))
        self._query(self._pool, relays, kind=KIND_RELAY_LIST, author=key,
                    on_done=lambda result: self._answered(key, result),
                    timeout_ms=timeout_ms, parent=self)

    def _answered(self, key: str, result: Lookup) -> None:
        # The waiters leave first: whatever happens below, a later lookup
        # of this person starts afresh instead of queueing behind this one.
        callbacks = self._waiting.pop(key, [])
        answer = RelayList(state=LookupState.UNKNOWN)
        try:
            answer = self._settle(key, result)
        except Exception:  # noqa: BLE001, a bad answer must not strand the callers
            logger.exception("relay list lookup for %s could not be settled", key)
        for callback in callbacks:
            try:
                callback(answer)
            except Exception:  # noqa: BLE001, one caller's bug must not break the others
                logger.exception("relay list callback for %s failed", key)

    def _settle(self, key: str, result: Lookup) -> RelayList:
        """Fold one lookup into what is known, and return what is known."""
        current = self._entries.get(key)
        event = result.event if result.state is LookupState.FOUND else None
        if event is not None and policy.created_at_of(event) is not None:
            if current is None or not current.found or policy.is_newer(event, current.event):
                self._store(key, self._found(event))
            else:
                current.fetched_at = self._clock()   # what we hold is as new, or newer
        elif current is not None and current.found:
            current.fetched_at = self._clock() - _TTL[LookupState.FOUND] + defaults.TTL_UNKNOWN_S
        else:
            state = LookupState.UNKNOWN if result.state is LookupState.FOUND else result.state
            self._entries[key] = RelayList(state=state, fetched_at=self._clock())
        return self._entries[key]

    def _found(self, event: dict) -> RelayList:
        relay_list = policy.parse_relay_list(event)
        relay_list.fetched_at = self._clock()
        return relay_list

    def _store(self, key: str, relay_list: RelayList) -> None:
        previous = self._entries.get(key)
        self._entries[key] = relay_list
        if key in {p.lower() for p in self._own_pubkeys()}:
            self._save()
        if previous is None or previous.created_at != relay_list.created_at:
            self.changed.emit(key)

    # -- persistence of the user's own lists -----------------------------------

    def _load(self) -> None:
        if self._store_path is None:
            return
        try:
            data = json.loads(self._store_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for event in (data.get("lists") or {}).values() if isinstance(data, dict) else ():
            if (isinstance(event, dict) and event.get("kind") == KIND_RELAY_LIST
                    and policy.created_at_of(event) is not None
                    and events.verify_event(event)):
                relay_list = policy.parse_relay_list(event)
                relay_list.fetched_at = 0.0   # known, but due for a refresh
                self._entries[str(event["pubkey"]).lower()] = relay_list

    def _save(self) -> None:
        if self._store_path is None:
            return
        own = {p.lower() for p in self._own_pubkeys()}
        lists = {k: e.event for k, e in self._entries.items()
                 if k in own and e.found and e.event is not None}
        folder = self._store_path.parent
        try:
            folder.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".relay_lists_", dir=str(folder))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "lists": lists}, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._store_path)
        except OSError:
            pass


def ask_private_relays(directory, profile, on_done: Callable[[List[str]], None], *,
                       entitled: Sequence[str] = ()) -> None:
    """Where ``profile``'s private records live (drafts, synced settings,
    private files), for writing and reading alike.

    The signer relays the profile was paired through are passed as the
    legacy set: MyEditor kept private records there before it read relay
    lists, so they stay in the set and those records stay readable.
    """
    directory.private_relays(profile.user_pubkey, on_done, entitled=list(entitled),
                             legacy=list(getattr(profile, "bunker_relays", None) or ()))
