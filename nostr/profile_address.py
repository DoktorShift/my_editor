# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Show a Nostr address (NIP-05) on the user's profile, and change nothing else.

The membership window offers this once a member has chosen their
EINUNDZWANZIG address, and again whenever the profile does not show it,
so a person who said Not Now can change their mind later. It only ever
runs after a click.

The safe read-modify-write (read the profile fresh, keep every other
field, check what the signer returns, require two relays) lives in
nostr/outbox/writer.py; this module turns its outcome into the ones the
window shows:

    shown        the profile with the address reached the relays
    already      the profile already shows this address; nothing sent
    no_profile   no profile was found; nothing was published
    unreadable   the profile couldn't be read; nothing was published
    failed       the signer said no, or too few relays took it

A profile that could not be found is not created from nothing here: a
profile holding only an address would replace the person's name and
picture in every app, should the relays asked simply have missed it.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from nostr.outbox import writer as outbox_writer
from nostr.outbox.lookup import fetch_replaceable

SHOWN = "shown"
ALREADY = "already"
NO_PROFILE = "no_profile"
UNREADABLE = "unreadable"
FAILED = "failed"

# Outcomes after which there is nothing left to do.
DONE = frozenset({SHOWN, ALREADY})

_MESSAGES = {
    SHOWN: "Your profile shows it now.",
    ALREADY: "Your profile already shows it.",
    NO_PROFILE: ("MyEditor couldn’t find your profile, so nothing was changed. "
                 "Try again later."),
    UNREADABLE: ("MyEditor couldn’t read your profile right now, so nothing was "
                 "changed. Try again later."),
    FAILED: "Your profile wasn’t changed. Try again in a moment.",
}

_FROM_WRITER = {
    outbox_writer.WRITTEN: SHOWN,
    outbox_writer.UNCHANGED: ALREADY,
    outbox_writer.REFUSED: NO_PROFILE,
    outbox_writer.UNKNOWN_BASE: UNREADABLE,
    outbox_writer.FAILED: FAILED,
}


def outcome_message(outcome: str) -> str:
    """Plain words for an outcome, for the window to show."""
    return _MESSAGES.get(outcome, _MESSAGES[FAILED])


class ProfileAddress(QObject):
    """One attempt to show ``address`` as the Nostr address on ``profile``."""

    finished = Signal(str)   # SHOWN, ALREADY, NO_PROFILE, UNREADABLE or FAILED; once

    def __init__(self, pool, session_pool, profile, address: str, *, directory,
                 query=fetch_replaceable, clock: Callable[[], float] = time.time,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._writer = outbox_writer.update_profile(
            changes={"nip05": address}, on_absent="refuse", pool=pool,
            directory=directory, session_pool=session_pool, profile=profile,
            query=query, clock=clock, parent=self)
        self._writer.finished.connect(
            lambda outcome: self.finished.emit(_FROM_WRITER.get(outcome.status, FAILED)))

    def start(self) -> None:
        self._writer.start()
