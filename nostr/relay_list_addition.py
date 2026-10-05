# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Add one relay to the user's published relay list, and change nothing else.

A relay list (NIP-65, kind 10002) tells every other client where to find
an author's notes and articles. It is replaceable: whatever is published
last is the whole list. So adding a relay means reading the current list
first, appending to exactly that, and signing the result. Publishing from
anything less (a list one relay happened to return, or nothing at all)
would replace the author's choices instead of adding to them, which no
undo can fix once relays have taken it.

The rules for that live in outbox.relay_list_tags_adding. This module is
the I/O around it: look the list up on several relays, check it really is
this author's and really is signed, ask the signer, publish to the relays
the list names plus the one being added, and report one outcome:

    added    the new list reached at least one relay
    already  the relay is already listed (under any marker); nothing sent
    no_list  no list was found, so none was published
    failed   the signer said no, or no relay took the new list

Built for the EINUNDZWANZIG members' relay, but nothing here is specific
to it.
"""

from __future__ import annotations

import time
from typing import Callable, Iterable, List, Optional

from PySide6.QtCore import QObject, Signal

from nostr import DEFAULT_RELAYS, events
from nostr.outbox import parse_relay_list, relay_list_tags_adding
from nostr.queries import fetch_latest_event

KIND_RELAY_LIST = 10002

ADDED = "added"
ALREADY = "already"
NO_LIST = "no_list"
FAILED = "failed"

# Relays that specialise in relay lists, asked alongside the user's own,
# so the newest list is found even when the user's relays are slow.
LOOKUP_RELAYS: tuple = ("wss://purplepag.es", "wss://relay.nostr.band")

_MESSAGES = {
    ADDED: "Added to your relay list.",
    ALREADY: "It’s already on your relay list.",
    NO_LIST: ("MyEditor couldn’t find your relay list, so nothing was changed. "
              "Try again in a moment."),
    FAILED: "Your relay list wasn’t changed. Try again in a moment.",
}


def outcome_message(outcome: str) -> str:
    """Plain words for an outcome, for the window to show."""
    return _MESSAGES.get(outcome, _MESSAGES[FAILED])


class RelayListAddition(QObject):
    """One attempt to add ``relay_url`` to ``profile``'s relay list."""

    finished = Signal(str)   # ADDED, ALREADY, NO_LIST or FAILED; emitted once

    def __init__(
        self,
        pool,
        session_pool,
        profile,
        relay_url: str,
        *,
        extra_lookup_relays: Iterable[str] = (),
        fetch: Callable = fetch_latest_event,
        clock: Callable[[], float] = time.time,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._pool = pool
        self._session_pool = session_pool
        self._profile = profile
        self._relay_url = relay_url
        self._fetch = fetch
        self._clock = clock
        self._done = False
        self._lookup = list(dict.fromkeys(
            [*profile.bunker_relays, *extra_lookup_relays, *LOOKUP_RELAYS, *DEFAULT_RELAYS]))
        self._job = None

    def start(self) -> None:
        self._fetch(
            self._pool,
            self._lookup,
            filters=[{"kinds": [KIND_RELAY_LIST],
                      "authors": [self._profile.user_pubkey], "limit": 1}],
            on_done=self._on_list,
            timeout_ms=8_000,
            parent=self,
        )

    # -- steps ----------------------------------------------------------------

    def _on_list(self, event: Optional[dict]) -> None:
        if not self._is_own_signed_list(event):
            self._finish(NO_LIST)
            return
        tags = relay_list_tags_adding(event, self._relay_url)
        if tags is None:
            self._finish(ALREADY)
            return
        unsigned = {
            "kind": KIND_RELAY_LIST,
            "content": event.get("content", "") if isinstance(event.get("content"), str) else "",
            "tags": tags,
            # Strictly newer than the list it replaces, even with a clock
            # that runs behind, or relays would keep the old one.
            "created_at": max(int(self._clock()), int(event.get("created_at", 0)) + 1),
        }
        targets = self._publish_targets(event)

        def signed(signed_event: dict) -> None:
            self._publish(signed_event, targets)

        def sign_failed(_reason: str) -> None:
            self._finish(FAILED)

        def ready(client) -> None:
            client.sign_event(unsigned, signed, sign_failed)

        self._session_pool.get(self._profile, ready, sign_failed)

    def _publish(self, signed_event: dict, targets: List[str]) -> None:
        if not events.verify_event(signed_event) or \
                signed_event.get("pubkey", "").lower() != self._profile.user_pubkey.lower():
            self._finish(FAILED)
            return
        self._job = self._pool.publish(targets, signed_event)
        self._job.all_done.connect(self._on_published)

    def _on_published(self, results: list) -> None:
        # Each result is (url, ok, message).
        accepted = any(len(r) > 1 and r[1] is True for r in results)
        self._finish(ADDED if accepted else FAILED)

    # -- helpers --------------------------------------------------------------

    def _is_own_signed_list(self, event) -> bool:
        return (isinstance(event, dict)
                and event.get("kind") == KIND_RELAY_LIST
                and str(event.get("pubkey", "")).lower() == self._profile.user_pubkey.lower()
                and events.verify_event(event))

    def _publish_targets(self, event: dict) -> List[str]:
        """Where the new list goes: every relay it names (readers look there),
        the relay being added, and the lookup relays clients ask first."""
        listed = parse_relay_list(event)
        return list(dict.fromkeys(
            [*listed.write, *listed.read, self._relay_url, *LOOKUP_RELAYS]))

    def _finish(self, outcome: str) -> None:
        if self._done:
            return
        self._done = True
        self.finished.emit(outcome)
