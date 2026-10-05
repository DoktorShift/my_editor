# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Find someone's newest replaceable event, and say how sure that is.

Looking up a relay list (or a profile) has three outcomes, and confusing
two of them is how a client overwrites somebody's real list:

    FOUND    a validly signed event of the right kind by the right author
    ABSENT   enough relays answered "nothing stored" (ABSENT_QUORUM, one of
             them an indexer): there really is none, as far as can be told
    UNKNOWN  relays timed out, refused, or too few answered. This is not
             evidence of absence and must never be treated as such.

Only events that pass verification compete for "newest", so a forged
event with a large timestamp cannot hide the real one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import QObject, QTimer

from . import defaults
from .policy import LookupState, dedupe_relays, newest_valid


@dataclass(frozen=True)
class Lookup:
    """What one lookup established."""

    state: LookupState
    event: Optional[dict] = None
    answered: tuple = ()     # relays that ended their stored events (EOSE)
    asked: tuple = ()


def classify(event: Optional[dict], answered: Sequence[str]) -> LookupState:
    """FOUND, ABSENT or UNKNOWN from a verified event and who answered."""
    if event is not None:
        return LookupState.FOUND
    indexers = set(dedupe_relays(defaults.INDEXER_RELAYS))
    if len(answered) >= defaults.ABSENT_QUORUM and any(a in indexers for a in answered):
        return LookupState.ABSENT
    return LookupState.UNKNOWN


def fetch_replaceable(pool, relays: Sequence[str], *, kind: int, author: str,
                      on_done: Callable[[Lookup], None], timeout_ms: int = 6_000,
                      parent: Optional[QObject] = None) -> "_ReplaceableQuery":
    """Ask ``relays`` for ``author``'s newest ``kind``; ``on_done`` once."""
    return _ReplaceableQuery(pool, dedupe_relays(relays), kind, author.lower(), on_done,
                             timeout_ms, parent)


class _ReplaceableQuery(QObject):
    def __init__(self, pool, relays: List[str], kind: int, author: str, on_done,
                 timeout_ms: int, parent) -> None:
        super().__init__(parent)
        self._relays = relays
        self._kind = kind
        self._author = author
        self._on_done = on_done
        self._candidates: List[dict] = []
        self._answered: List[str] = []
        self._finished = False
        if not relays:
            QTimer.singleShot(0, self._finish)
            self._sub = None
            return
        self._sub = pool.subscribe(relays, [{"kinds": [kind], "authors": [author],
                                             "limit": 2}])
        self._sub.event.connect(self._candidates.append)
        self._sub.relay_eose.connect(self._on_relay_eose)
        self._sub.eose.connect(self._finish)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(self._finish)
        self._timer.start()

    def _on_relay_eose(self, url: str) -> None:
        if url not in self._answered:
            self._answered.append(url)

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        if self._sub is not None:
            self._timer.stop()
            self._sub.close()
        event = newest_valid(self._candidates, kind=self._kind, author=self._author)
        result = Lookup(state=classify(event, self._answered), event=event,
                        answered=tuple(self._answered), asked=tuple(self._relays))
        try:
            self._on_done(result)
        finally:
            self.deleteLater()


def fetch_by_ids(pool, relays: Sequence[str], ids: Sequence[str],
                 on_done: Callable[[set], None], *, timeout_ms: int = 4_000,
                 parent: Optional[QObject] = None) -> None:
    """Which of ``ids`` the relays return: the read-back after a publish,
    because an acknowledgement alone does not prove a relay kept anything."""
    found: set = set()
    if not relays or not ids:
        QTimer.singleShot(0, lambda: on_done(found))
        return
    sub = pool.subscribe(dedupe_relays(relays), [{"ids": list(ids)}])
    state = {"done": False}

    def finish():
        if state["done"]:
            return
        state["done"] = True
        sub.close()
        on_done(found)

    sub.event.connect(lambda event: event.get("id") in ids and found.add(event["id"]))
    sub.eose.connect(finish)
    QTimer.singleShot(timeout_ms, finish)
