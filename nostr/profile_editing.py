# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The network half of Edit Profile (nostr/ui/profile_window.py).

``read`` asks for the account's newest profile where profiles are found
(its own write relays, then the indexers). ``save`` changes the fields
given and nothing else, through the safe read-modify-write in
nostr/outbox/writer.py. Both answer through a callback, once.
"""

from __future__ import annotations

import time
from typing import Callable, Dict

from PySide6.QtCore import QObject

from nostr.outbox import writer as outbox_writer
from nostr.outbox.lookup import fetch_replaceable
from nostr.outbox.policy import KIND_PROFILE, lookup_relays


class ProfileEditing(QObject):
    """Read and save one account's profile. Parent it to the window that
    uses it, so closing the window ends what is still running."""

    def __init__(self, *, profile, pool, session_pool, directory,
                 query=fetch_replaceable, clock: Callable[[], float] = time.time,
                 parent=None) -> None:
        super().__init__(parent)
        self._profile = profile
        self._pool = pool
        self._session_pool = session_pool
        self._directory = directory
        self._query = query
        self._clock = clock

    def read(self, on_done: Callable[[object], None]) -> None:
        author = self._profile.user_pubkey.lower()
        relays = lookup_relays(known=self._directory.cached(author), own=True)
        self._query(self._pool, relays, kind=KIND_PROFILE, author=author,
                    on_done=on_done, parent=self)

    def save(self, changes: Dict[str, str], on_done: Callable[[object], None]) -> None:
        # An account with no profile yet gets one made of what was entered.
        writer = outbox_writer.update_profile(
            changes=changes, on_absent="create", pool=self._pool,
            directory=self._directory, session_pool=self._session_pool,
            profile=self._profile, query=self._query, clock=self._clock, parent=self)
        writer.finished.connect(on_done)
        writer.start()
