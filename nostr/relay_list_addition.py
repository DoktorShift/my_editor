# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Add one relay to the user's published relay list, and change nothing else.

The membership window offers this for the EINUNDZWANZIG members' relay.
The safe read-modify-write itself (read the current list fresh, never
build one from nothing, check what the signer returns, require two
relays, read it back) lives in nostr/outbox/writer.py; this module only
turns its outcome into the four the window shows:

    added    the new list reached the relays
    already  the relay is already listed (under any marker); nothing sent
    no_list  no list could be read, so none was published
    failed   the signer said no, or too few relays took the new list
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from nostr.outbox import writer as outbox_writer
from nostr.outbox.lookup import fetch_replaceable

ADDED = "added"
ALREADY = "already"
NO_LIST = "no_list"
FAILED = "failed"

_MESSAGES = {
    ADDED: "Added to your relay list.",
    ALREADY: "It’s already on your relay list.",
    NO_LIST: ("MyEditor couldn’t find your relay list, so nothing was changed. "
              "Try again in a moment."),
    FAILED: "Your relay list wasn’t changed. Try again in a moment.",
}

_FROM_WRITER = {
    outbox_writer.WRITTEN: ADDED,
    outbox_writer.UNCHANGED: ALREADY,
    outbox_writer.REFUSED: NO_LIST,
    outbox_writer.UNKNOWN_BASE: NO_LIST,
    outbox_writer.FAILED: FAILED,
}


def outcome_message(outcome: str) -> str:
    """Plain words for an outcome, for the window to show."""
    return _MESSAGES.get(outcome, _MESSAGES[FAILED])


class RelayListAddition(QObject):
    """One attempt to add ``relay_url`` to ``profile``'s relay list."""

    finished = Signal(str)   # ADDED, ALREADY, NO_LIST or FAILED; emitted once

    def __init__(self, pool, session_pool, profile, relay_url: str, *, directory,
                 query=fetch_replaceable, clock: Callable[[], float] = time.time,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._writer = outbox_writer.add_relay(
            url=relay_url, pool=pool, directory=directory, session_pool=session_pool,
            profile=profile, query=query, clock=clock, parent=self)
        self._writer.finished.connect(
            lambda outcome: self.finished.emit(_FROM_WRITER.get(outcome.status, FAILED)))

    def start(self) -> None:
        self._writer.start()
