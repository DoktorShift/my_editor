# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where an account keeps its drafts: the private relay list (NIP-37, kind 10013).

Drafts are encrypted, but where they are stored still says something:
someone may prefer to keep them on a server of their own that only lets
them read their own events. NIP-37 gives that a list of its own, kind
10013. Its relays are private too: they are written as tags, as JSON,
encrypted to the account's own key and placed in the event's content, so
only the account can read where its drafts live.

:func:`parse_private_relays` and :func:`private_relays_plaintext` are
the pure halves. :class:`PrivateDraftRelays` reads the list (find it,
then decrypt it with the account's signer) and publishes a new one
(encrypt, then the read-modify-write of nostr/outbox/writer.py, which
never replaces a list it could not read). Reading answers with a list,
an empty list for an account that has none, or None when it could not
be read or decrypted: that is not "none" and must not be treated as such.
"""

from __future__ import annotations

import json
import time
from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import QObject

from nostr.outbox import defaults
from nostr.outbox.lookup import fetch_replaceable
from nostr.outbox.policy import LookupState, lookup_relays, normalize_relay_url
from nostr.outbox.writer import FAILED, ReplaceableWriter, WriteOutcome

KIND_PRIVATE_RELAYS = 10013


def parse_private_relays(plaintext: str) -> List[str]:
    """The relays in a decrypted list, in order, without duplicates. Raises
    ValueError when the text is not a JSON list (the list is never guessed
    at)."""
    tags = json.loads(plaintext)
    if not isinstance(tags, list):
        raise ValueError("the private relay list is not a JSON list")
    relays: List[str] = []
    for tag in tags:
        if not isinstance(tag, list) or len(tag) < 2 or tag[0] != "relay":
            continue
        url = normalize_relay_url(tag[1])
        if url and url not in relays:
            relays.append(url)
        if len(relays) >= defaults.PRIVATE_CAP:
            break
    return relays


def private_relays_plaintext(relays: Sequence[str]) -> str:
    """What is encrypted into the list's content: ``[["relay", url], ...]``.
    Raises ValueError when no relay is usable (an empty list would leave
    the drafts nowhere)."""
    tags = []
    for relay in relays:
        url = normalize_relay_url(relay)
        if url and ["relay", url] not in tags:
            tags.append(["relay", url])
    if not tags:
        raise ValueError("no usable relay for drafts")
    return json.dumps(tags, separators=(",", ":"))


class PrivateDraftRelays(QObject):
    """Read or publish one account's private relay list for drafts."""

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

    def read(self, on_done: Callable[[Optional[List[str]]], None]) -> None:
        """``on_done(relays)``, ``on_done([])`` for none, ``on_done(None)``
        when it could not be read or decrypted."""
        author = self._profile.user_pubkey.lower()
        relays = lookup_relays(known=self._directory.cached(author), own=True)

        def found(result) -> None:
            if result.state is LookupState.ABSENT:
                on_done([])
            elif result.state is LookupState.UNKNOWN or result.event is None:
                on_done(None)
            else:
                self._decrypt(str(result.event.get("content") or ""), on_done)

        self._query(self._pool, relays, kind=KIND_PRIVATE_RELAYS, author=author,
                    on_done=found, parent=self)

    def _decrypt(self, ciphertext: str, on_done) -> None:
        def plaintext(text: str) -> None:
            try:
                on_done(parse_private_relays(text))
            except (ValueError, TypeError):
                on_done(None)

        self._session_pool.get(
            self._profile,
            lambda client: client.nip44_decrypt_self(ciphertext, plaintext,
                                                     lambda _reason: on_done(None)),
            lambda _reason: on_done(None))

    def publish(self, relays: Sequence[str],
                on_done: Callable[[WriteOutcome], None]) -> None:
        """Encrypt and publish ``relays`` as the account's draft relays.
        Raises ValueError for an empty list, before asking anything."""
        plaintext = private_relays_plaintext(relays)

        def failed(reason: str) -> None:
            on_done(WriteOutcome(FAILED, reason=reason or "The signer didn’t encrypt it."))

        def encrypted(ciphertext: str) -> None:
            writer = ReplaceableWriter(
                kind=KIND_PRIVATE_RELAYS, mutate=lambda _base: (ciphertext, []),
                on_absent="create", pool=self._pool, directory=self._directory,
                session_pool=self._session_pool, profile=self._profile,
                query=self._query, clock=self._clock, parent=self)
            writer.finished.connect(on_done)
            writer.start()

        self._session_pool.get(
            self._profile,
            lambda client: client.nip44_encrypt_self(plaintext, encrypted, failed),
            failed)
