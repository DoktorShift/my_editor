# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Draft deletions announced as NIP-09 deletion requests.

NIP-37 deletes a draft by replacing its wrap with one whose content is
empty. This app has always done that, and DraftSync reads it. Other
apps, EINUNDZWANZIG STANDUP among them, delete with a NIP-09 deletion
request instead: a kind 5 event that names the draft's address,
``31234:<pubkey>:<d>`` (nostr/deletion.py keeps the rules of such
requests). Without reading those, a draft deleted in
STANDUP stayed in this app's drafts list, and could be published from
here as if it had never been deleted.

:class:`DraftDeletions` keeps a subscription open for the account's own
deletion requests, on the relays its drafts are read from, and hands
each to the DraftStore the way a blanked wrap is handed to it
(:meth:`DraftStore.apply_deletion`): a deletion removes only what it is
newer than, and is remembered, so a later refresh that brings the old
wrap back does not bring the draft back. A draft saved after the
request is a new draft and stays. Only requests signed by the account
count: a relay cannot delete anyone's draft by inventing one.

:func:`build_deletion_request` makes the request this app sends with
its own blanked wrap when it deletes an imported draft, so STANDUP
learns about that deletion too and never imports the post again.
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional, Sequence

from PySide6.QtCore import QObject, Signal

from .deletion import DELETION_KIND, address, build_deletion, read_deletion
from .drafts import DRAFT_WRAP_KIND
from .outbox import RelayDirectory, ask_draft_relays
from .profiles import Profile
from .relay import RelayPool


def draft_address(pubkey_hex: str, identifier: str) -> str:
    """NIP-01 address of a draft wrap: ``31234:<pubkey>:<d>``."""
    return address(DRAFT_WRAP_KIND, pubkey_hex, identifier)


def build_deletion_request(*, pubkey_hex: str, identifier: str, wrap_id: str = "",
                           created_at: Optional[int] = None) -> dict:
    """Unsigned NIP-09 request deleting the draft ``identifier``.

    Tags: the draft's address (what deletes every version up to now),
    the id of the wrap being replaced when known, and the kind, the
    shape STANDUP sends and reads.
    """
    return build_deletion(pubkey_hex, addresses=[draft_address(pubkey_hex, identifier)],
                          event_ids=[wrap_id] if wrap_id else [], kinds=[DRAFT_WRAP_KIND],
                          created_at=created_at)


def deleted_identifiers(event: dict, pubkey_hex: str) -> Dict[str, int]:
    """``{d: created_at}`` for the drafts of ``pubkey_hex`` that ``event``
    deletes. Empty unless it is a deletion request signed by that key."""
    request = read_deletion(event, pubkey_hex)
    if request is None:
        return {}
    return {identifier: request.created_at
            for identifier in request.identifiers(DRAFT_WRAP_KIND) if identifier}


# Requests without the optional ``k`` tag are found by their author only:
# the newest this many of the account's requests (engine review L13).
UNTAGGED_LIMIT = 500


class DraftDeletions(QObject):
    """The account's deletion requests for its drafts, applied to a store.

    ``removed(str)`` fires for each draft a request took away, so other
    views (the imports inbox) can show the post as removed.

    The requests are followed where the drafts are read, and follow them
    when they move: the account's relay list changing, a membership's
    relay coming or going (:meth:`reroute`), the drafts list refreshed
    (:meth:`refresh`). Engine review L13: they were followed on the relays
    known at the start only.
    """

    removed = Signal(str)

    def __init__(self, *, relay_pool: RelayPool, relay_directory: RelayDirectory,
                 store, entitled_relays: Optional[Callable[[], Sequence[str]]] = None,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._relay_pool = relay_pool
        self._relay_directory = relay_directory
        self._store = store
        self._entitled_relays = entitled_relays
        self._profile: Optional[Profile] = None
        self._subscription = None
        self._relays: List[str] = []
        self._generation = 0
        relay_directory.changed.connect(self._on_relay_list_changed)

    # -- lifecycle -----------------------------------------------------------

    def start_for(self, profile: Profile) -> None:
        """Follow ``profile``'s deletion requests (a no-op for the same one)."""
        if self._profile is not None and self._profile.user_pubkey == profile.user_pubkey:
            return
        self.stop()
        self._profile = profile
        generation = self._generation
        ask_draft_relays(self._relay_directory, profile,
                         lambda relays: self._subscribe(generation, relays),
                         entitled=self._entitled_relays, reading=True)

    def stop(self) -> None:
        self._generation += 1
        self._close()
        self._profile = None

    def reroute(self) -> None:
        """The relays the drafts are read from may have changed (a
        membership's relay came or went): follow the requests there,
        asking again only when the set changed."""
        profile = self._profile
        if profile is None:
            return
        generation = self._generation
        ask_draft_relays(self._relay_directory, profile,
                         lambda relays: self._rerouted(generation, relays),
                         entitled=self._entitled_relays, reading=True)

    def _on_relay_list_changed(self, pubkey: str) -> None:
        if self._profile is not None and pubkey.lower() == self._profile.user_pubkey.lower():
            self.reroute()

    def _rerouted(self, generation: int, relays: Iterable[str]) -> None:
        relays = list(relays)
        if (generation != self._generation or self._profile is None
                or sorted(relays) == sorted(self._relays)):
            return
        self._subscribe(generation, relays)

    def refresh(self) -> None:
        """Ask again (the drafts list was refreshed, or the relays moved)."""
        profile = self._profile
        if profile is not None:
            self._profile = None
            self.start_for(profile)

    # -- internals -----------------------------------------------------------

    def _close(self) -> None:
        if self._subscription is not None:
            self._subscription.close()
            self._subscription = None
        self._relays = []

    def _subscribe(self, generation: int, relays: Iterable[str]) -> None:
        if generation != self._generation or self._profile is None:
            return
        relays = list(relays)
        self._close()
        if not relays:
            return
        self._relays = relays
        author = self._profile.user_pubkey
        self._subscription = self._relay_pool.subscribe(relays, [
            {"kinds": [DELETION_KIND], "authors": [author], "#k": [str(DRAFT_WRAP_KIND)]},
            # The k tag is optional (NIP-09): a request naming a draft's
            # address without it is one of these.
            {"kinds": [DELETION_KIND], "authors": [author], "limit": UNTAGGED_LIMIT},
        ])
        self._subscription.event.connect(self.handle_event)

    def handle_event(self, event: dict) -> None:
        if self._profile is None:
            return
        event_id = str(event.get("id", ""))
        for identifier, when in deleted_identifiers(event, self._profile.user_pubkey).items():
            if self._store.apply_deletion(identifier, when, event_id, request=True):
                self.removed.emit(identifier)
