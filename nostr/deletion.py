# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taking back what was published: deletion requests (NIP-09).

A deletion request is a kind 5 event that names events its author wants
removed. ``e`` tags name one event each, by id. ``a`` tags name an event
that newer versions replace (an article, a draft) by its address,
``<kind>:<pubkey>:<d>``, and remove every version of it up to the time of
the request, so a version published later stays. ``k`` tags say the kinds.

Relays that honour a request stop handing those events out. People and
apps that saved a copy may still have it, which is why the app says it
asks relays to remove something rather than that it is gone.

One home for each rule:

    address          the address of a replaceable or addressable event
    build_deletion   an unsigned request naming events
    read_deletion    what a validly signed request by an author asks to
                     remove (a DeletionRequest); anything else asks nothing
    DeletionRequest.covers
                     whether a request covers an event: only the author's
                     own events, and an address only up to its time

:class:`DeletionJob` takes one of the account's published events back:
it has the request signed through the session pool and sends it to every
relay that may keep the event, reporting relay by relay.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, Signal

from i18n import _, ngettext

from . import events
from .outbox.policy import created_at_of, dedupe_relays, relays_from
from .outbox.writer import signed_matches

DELETION_KIND = 5

_HEX64 = re.compile(r"[0-9a-f]{64}")     # an event id, a public key


# --------------------------------------------------------------------------- #
# Addresses                                                                   #
# --------------------------------------------------------------------------- #

def is_replaceable(kind: int) -> bool:
    """NIP-01: one event per author and kind (a profile, a relay list)."""
    return kind in (0, 3) or 10_000 <= kind < 20_000


def is_addressable(kind: int) -> bool:
    """NIP-01: one event per author, kind and ``d`` (an article, a draft)."""
    return 30_000 <= kind < 40_000


def address(kind: int, pubkey_hex: str, identifier: str = "") -> str:
    """NIP-01 address: ``<kind>:<pubkey>:<d>`` (``d`` empty for replaceable kinds)."""
    return f"{int(kind)}:{pubkey_hex.lower()}:{identifier}"


def address_of(event: dict) -> Optional[str]:
    """The address of a replaceable or addressable event, None for any other."""
    if not isinstance(event, dict):
        return None
    kind = event.get("kind")
    if isinstance(kind, bool) or not isinstance(kind, int):
        return None
    pubkey = str(event.get("pubkey", "")).lower()
    if is_replaceable(kind):
        return address(kind, pubkey)
    if is_addressable(kind):
        return address(kind, pubkey, _d_of(event))
    return None


def _d_of(event: dict) -> str:
    for tag in event.get("tags", []) or []:
        if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "d":
            return str(tag[1])
    return ""


def _normal_address(value: str) -> Optional[str]:
    """An ``a`` tag's address with kind and pubkey written one way, or None."""
    parts = value.split(":", 2)
    if len(parts) != 3:
        return None
    kind, pubkey, identifier = parts
    if not kind.isdigit() or not _HEX64.fullmatch(pubkey.lower()):
        return None
    return address(int(kind), pubkey, identifier)


# --------------------------------------------------------------------------- #
# Requests                                                                    #
# --------------------------------------------------------------------------- #

def build_deletion(pubkey_hex: str, *, addresses: Iterable[str] = (),
                   event_ids: Iterable[str] = (), kinds: Iterable[int] = (),
                   created_at: Optional[int] = None) -> dict:
    """An unsigned request to delete the events at ``addresses`` and with
    ``event_ids``, of ``kinds``. Raises ValueError when it names nothing."""
    named_addresses = list(dict.fromkeys(addresses))
    named_ids = list(dict.fromkeys(event_ids))
    if not named_addresses and not named_ids:
        raise ValueError("a deletion request names at least one event")
    tags = [["a", value] for value in named_addresses]
    tags += [["e", value] for value in named_ids]
    tags += [["k", str(int(kind))] for kind in dict.fromkeys(kinds)]
    return events.build_event(kind=DELETION_KIND, content="", tags=tags,
                              pubkey_hex=pubkey_hex, created_at=created_at)


@dataclass(frozen=True)
class DeletionRequest:
    """What one validly signed deletion request asks to remove."""

    id: str
    pubkey: str
    created_at: int
    event_ids: FrozenSet[str] = frozenset()
    addresses: FrozenSet[str] = frozenset()     # "<kind>:<pubkey>:<d>"

    def covers(self, event: dict) -> bool:
        """Whether this request removes ``event`` (NIP-09): one of the
        requester's own events named by id, or a version of a named
        address no newer than the request."""
        if not isinstance(event, dict) or str(event.get("pubkey", "")).lower() != self.pubkey:
            return False
        if str(event.get("id", "")).lower() in self.event_ids:
            return True
        where = address_of(event)
        if where is None or where not in self.addresses:
            return False
        created = created_at_of(event)
        return created is not None and created <= self.created_at

    def identifiers(self, kind: int) -> List[str]:
        """The ``d`` of every address of ``kind`` this request names."""
        prefix = address(kind, self.pubkey)
        return sorted(value[len(prefix):] for value in self.addresses
                      if value.startswith(prefix))


def read_deletion(event: dict, author: str) -> Optional[DeletionRequest]:
    """What ``event`` asks to remove, when it is a deletion request signed
    by ``author``; None for anything else. A relay cannot delete anyone's
    events by inventing a request: only the author's signature counts."""
    author = (author or "").lower()
    if not isinstance(event, dict) or event.get("kind") != DELETION_KIND:
        return None
    if str(event.get("pubkey", "")).lower() != author:
        return None
    created = created_at_of(event)
    if created is None or not events.verify_event(event):
        return None
    ids, addresses = set(), set()
    for tag in event.get("tags", []):
        if not isinstance(tag, list) or len(tag) < 2 or not isinstance(tag[1], str):
            continue
        if tag[0] == "e":
            ids.add(tag[1].lower())
        elif tag[0] == "a":
            normalized = _normal_address(tag[1])
            if normalized is not None:
                addresses.add(normalized)
    return DeletionRequest(id=str(event["id"]), pubkey=author, created_at=created,
                           event_ids=frozenset(ids), addresses=frozenset(addresses))


# --------------------------------------------------------------------------- #
# DeletionJob: sign a request, send it where the event may be kept            #
# --------------------------------------------------------------------------- #

class DeletionJob(QObject):
    """Ask relays to delete one of the account's published events.

    The request names the event by id, one with an address (an article)
    also by its address, and says its kind. It goes to every relay the
    event was found on, and wherever the account publishes: the routing a
    publish takes (RelayDirectory.publish_plan: the account's write
    relays, a membership's relay, and the relays of the people the event
    mentions), so each relay that may keep a copy is asked. The routing
    and the signature are fetched at the same time.

    Signals, in firing order:
      status_changed(str)            progress, in words
      signed(dict)                   the signed request, before it is sent
      relay_result(str, bool, str)   each relay's answer (url, ok, message)
      first_accept(str)              the first relay that took it, once
                                     per sending
      completed(list)                every answer [(url, ok, message)],
                                     also when no relay took it
      failed(str)                    the signer did not sign; terminal

    ``send_again()`` sends the signed request once more (Try Again) once
    the relays have answered, without asking the signer again.
    ``cancel()`` silences the job: a request already sent still reaches
    the relays, a signature that arrives later is not used.
    """

    status_changed = Signal(str)
    signed = Signal(dict)
    relay_result = Signal(str, bool, str)
    first_accept = Signal(str)
    completed = Signal(list)
    failed = Signal(str)

    def __init__(self, *, relay_pool, relay_directory, session_pool, profile,
                 event: dict, found_on: Sequence[str] = (),
                 mentioned: Sequence[Tuple[str, str]] = (), entitled_relays=(),
                 clock: Callable[[], float] = time.time,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        author = str(profile.user_pubkey).lower()
        if not isinstance(event, dict):
            raise ValueError("not a published event")
        kind = event.get("kind")
        if (not _HEX64.fullmatch(str(event.get("id", ""))) or isinstance(kind, bool)
                or not isinstance(kind, int)):
            raise ValueError("not a published event")
        if str(event.get("pubkey", "")).lower() != author:
            raise ValueError("only the account's own events can be deleted")
        self._relay_pool = relay_pool
        self._relay_directory = relay_directory
        self._session_pool = session_pool
        self._profile = profile
        self._author = author
        self._event = event
        self._found_on = list(found_on)
        self._mentioned = list(mentioned)
        self._entitled = entitled_relays
        self._clock = clock
        self._targets: Optional[List[str]] = None
        self._request: Optional[dict] = None
        self._sent = False
        self._sending = False
        self._done = False
        self._cancelled = False

    # -- public API --------------------------------------------------------

    @property
    def request(self) -> Optional[dict]:
        """The signed request, once the signer has signed it."""
        return self._request

    @property
    def targets(self) -> List[str]:
        """Where the request goes (known once the routing is in)."""
        return list(self._targets or ())

    def start(self) -> None:
        """Ask for the routing and the signature. Once per job."""
        self._emit_status(_("Connecting to your signer…"))
        self._relay_directory.publish_plan(self._author, self._on_plan,
                                           mentioned=self._mentioned,
                                           entitled=relays_from(self._entitled))
        self._session_pool.get(self._profile, on_ready=self._on_signer, on_error=self._fail)

    def send_again(self) -> bool:
        """Send the signed request again, to the same relays. False when
        nothing was signed yet (start a new job instead) or the relays
        have not all answered the last sending."""
        if (self._request is None or self._targets is None or self._cancelled
                or self._sending):
            return False
        self._send()
        return True

    def cancel(self) -> None:
        self._cancelled = True

    # -- pipeline ----------------------------------------------------------

    def _unsigned(self) -> dict:
        kind = int(self._event["kind"])
        where = address_of(self._event)
        return build_deletion(self._author, addresses=[where] if where else [],
                              event_ids=[self._event["id"]], kinds=[kind],
                              created_at=int(self._clock()))

    def _on_plan(self, plan) -> None:
        self._targets = dedupe_relays(self._found_on, plan.targets)
        self._send_when_ready()

    def _on_signer(self, client) -> None:
        if self._cancelled or self._done:
            return
        unsigned = self._unsigned()
        self._emit_status(_("Waiting for signature. Approve the deletion on your signer…"))

        def signed(event: dict) -> None:
            if not signed_matches(event, unsigned, self._author):
                self._fail(_("The signer returned a different event."))
                return
            self._on_signed(event)

        client.sign_event(unsigned, on_success=signed, on_failure=self._fail)

    def _on_signed(self, event: dict) -> None:
        if self._cancelled or self._done:
            return
        self._request = event
        self.signed.emit(dict(event))
        self._send_when_ready()

    def _send_when_ready(self) -> None:
        if (self._sent or self._cancelled or self._done or self._request is None
                or self._targets is None):
            return
        self._sent = True
        self._send()

    def _send(self) -> None:
        self._sending = True
        count = len(self._targets)
        self._emit_status(ngettext("Asking {n} relay to delete it…",
                                   "Asking {n} relays to delete it…", count).format(n=count))
        job = self._relay_pool.publish(self._targets, self._request)
        job.relay_result.connect(self._on_relay_result)
        job.first_accept.connect(self._on_first_accept)
        job.all_done.connect(self._on_all_done)

    def _on_relay_result(self, url: str, ok: bool, message: str) -> None:
        if not self._cancelled:
            self.relay_result.emit(url, ok, message)

    def _on_first_accept(self, url: str) -> None:
        if not self._cancelled:
            self.first_accept.emit(url)

    def _on_all_done(self, results: list) -> None:
        self._sending = False
        if not self._cancelled:
            self.completed.emit(list(results))

    def _fail(self, reason: str) -> None:
        if self._cancelled or self._done or self._sent:
            return
        self._done = True
        self.failed.emit(reason or _("The signer didn’t sign the request."))

    def _emit_status(self, text: str) -> None:
        if not self._cancelled:
            self.status_changed.emit(text)
