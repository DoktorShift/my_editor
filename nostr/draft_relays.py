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
the pure halves, and :func:`draft_relay_address` checks an address a
person typed for the list. :class:`PrivateDraftRelays` reads the list
(find it, then decrypt it with the account's signer) and publishes a new
one (encrypt, then the read-modify-write of nostr/outbox/writer.py, which
never replaces a list it could not read). Reading answers with a list,
an empty list for an account that has none, or None when it could not
be read or decrypted: that is not "none" and must not be treated as such
(``last_failure`` says which of the two it was).

Going back to the usual relays publishes an empty list rather than
deleting the old one: a replaceable event is replaced on every relay
that gets the newer one, while a deletion request is honoured by some
relays only, and an app reading a relay that kept the old list would
still save drafts there. An account with no list has nothing to stop,
and nothing is published for it.

Drafts move with the list. Once it changes, a relay that is no longer on
it is no longer read, and every draft kept only there would vanish from
every device. So ``save`` first copies the drafts (:class:`DraftMove`):
draft wraps are signed events, so the newest wrap of every draft (an
emptied one, a deleted draft, too) and the account's requests deleting
drafts are read from where drafts are read now and sent, unchanged and
with no signer prompt, to where drafts go next. The list is published
only when every one of them reached at least one of those relays; then
a short pass copies what was saved in the meantime, and only then do
drafts follow the new list (RelayDirectory.set_draft_relays). While all
this runs, saving a draft waits for the answer
(RelayDirectory.expect_draft_relays), so nothing is saved by the old
list after it was copied.

A relay being left that does not answer stops the change, as its drafts
would vanish, unless the person chose to leave that relay's drafts
behind (``leave_unread`` names it: for a relay that is gone for good).
Every other relay being left must still answer. The copies on the
relays left stay there; removing them would take a signed deletion
request for each.

``save`` changes only the list it last read: a list changed elsewhere
in the meantime is not written over (the outcome's cause is
CAUSE_CHANGED, and the list is to be read again).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, Signal

import url_safety
from nostr.deletion import DELETION_KIND
from nostr.draft_deletions import deleted_identifiers
from nostr.drafts import DRAFT_WRAP_KIND
from nostr.outbox import ask_draft_relays, defaults
from nostr.outbox.lookup import fetch_replaceable
from nostr.outbox.policy import (
    LookupState, created_at_of, dedupe_relays, is_newer, lookup_relays, normalize_relay_url,
)
from nostr.outbox.writer import (
    CAUSE_SIGNER, FAILED, REFUSED, UNCHANGED, BaseChanged, ReplaceableWriter, WriteOutcome,
)
from nostr.publish_pipeline import kept_by_relay, refused_by_relay
from nostr.queries import Fetched, fetch_events, page_horizon

KIND_PRIVATE_RELAYS = 10013

# Why the list could not be read (PrivateDraftRelays.last_failure).
UNREACHABLE = "unreachable"     # no relay answered for it
UNDECRYPTED = "undecrypted"     # the signer did not decrypt it, or it is not a list

# Where a save is (``save``'s ``on_step``).
READING_DRAFTS = "reading_drafts"
MOVING_DRAFTS = "moving_drafts"
SAVING_LIST = "saving_list"

# Moving the drafts.
MOVE_PAGE = 100               # wraps asked for at once: below every relay's own ceiling
                              # we know of (strfry 500, rnostr 2000), so a relay that
                              # sends fewer has no older ones
SENDS_AT_ONCE = 8             # drafts on their way at the same time
MOVE_QUERY_TIMEOUT_MS = 10_000
MOVE_WAIT_MS = 120_000        # how long saving a draft waits for a change to end
CATCH_UP_SLACK_S = 60         # the catch-up pass reads from this long before the change

logger = logging.getLogger(__name__)


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


def draft_relay_address(text: str) -> Optional[str]:
    """The address of a relay a person typed for their drafts, written
    one way (``wss://host[:port][/path]``), or None when it cannot be one.

    ``wss://`` anywhere: the list is the person's own, so a relay on their
    own network is fine. Plain ``ws://`` only on this computer (a relay
    running here), where nobody on the way can read or change what is
    sent."""
    url = normalize_relay_url((text or "").strip())
    if url is None:
        return None
    if url.startswith("wss://"):
        return url
    return url if url_safety.is_loopback_host(urlsplit(url).hostname or "") else None


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


# --------------------------------------------------------------------------- #
# Moving the drafts                                                           #
# --------------------------------------------------------------------------- #

def _tag(event: dict, name: str) -> Optional[str]:
    for tag in event.get("tags", []) or []:
        if isinstance(tag, list) and len(tag) >= 2 and tag[0] == name:
            return str(tag[1])
    return None


def _expired(event: dict, now: int) -> bool:
    """NIP-40: past its expiration, so a relay would refuse it."""
    expiration = _tag(event, "expiration")
    try:
        return expiration is not None and int(expiration) <= now
    except ValueError:
        return False


@dataclass(frozen=True)
class MoveOutcome:
    """What moving the drafts did."""

    moved: int = 0                       # events now on a relay drafts go to next
    total: int = 0                       # events there were to move
    unread: Tuple[str, ...] = ()         # relays being left that did not answer
    not_moved: Tuple[str, ...] = ()      # ids of the events no relay took
    refused: bool = False                # a relay turned one down (not just silence)

    @property
    def ok(self) -> bool:
        return not self.unread and not self.not_moved


class DraftMove(QObject):
    """Copy the account's drafts, as they are, to where drafts go next.

    Reads from ``source``, a page at a time, the newest wrap of every
    draft (an emptied one too: the draft was deleted, and an older copy
    must not bring it back) and the account's requests deleting drafts,
    then sends each, unchanged, to the relays of ``destination`` that do
    not have it yet. One is moved once a relay there holds it. Left out
    are a wrap past its NIP-40 expiration (it would be refused, and it is
    gone anyway) and one a request covers (the request goes instead).

    Every relay of ``must_read`` (those not read any more once the list
    changed) has to answer, or the move ends at once, having sent
    nothing. ``since`` reads only what was saved from then on, and
    ``skip`` leaves out what a first pass moved (the catch-up).

    Signals:
      progress(int, int, int)  events settled, events to send, and of
                               them drafts with content ("Moving 12 drafts…")
      finished(object)         a MoveOutcome, once
    """

    progress = Signal(int, int, int)
    finished = Signal(object)

    _WRAPS, _REQUESTS = "wraps", "requests"

    def __init__(self, *, pool, author: str, source: Sequence[str],
                 destination: Sequence[str], must_read: Sequence[str] = (),
                 since: Optional[int] = None, skip: Sequence[str] = (),
                 clock: Callable[[], float] = time.time,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._pool = pool
        self._author = author.lower()
        self._source = dedupe_relays(source)
        self._destination = dedupe_relays(destination)
        self._must_read = set(dedupe_relays(must_read))
        self._since = since
        self._skip = set(skip)
        self._clock = clock
        self._wraps: Dict[str, dict] = {}            # d -> the newest wrap
        self._requests: Dict[str, dict] = {}         # id -> a request deleting drafts
        self._deleted: Dict[str, int] = {}           # d -> when a request deleted it
        self._seen_on: Dict[str, Set[str]] = {}      # id -> relays that sent it
        self._queue: List[dict] = []
        self._in_flight = 0
        self._total = 0
        self._drafts = 0
        self._moved: List[str] = []
        self._not_moved: List[str] = []
        self._refused = False
        self._done = False

    @property
    def moved_ids(self) -> List[str]:
        """The events (ids) that are where drafts go next."""
        return list(self._moved)

    def start(self) -> None:
        if not self._source:
            self._finish()
            return
        self._read(self._WRAPS, None)

    # -- reading -------------------------------------------------------------

    def _read(self, which: str, until: Optional[int]) -> None:
        kind = DRAFT_WRAP_KIND if which == self._WRAPS else DELETION_KIND
        flt: dict = {"kinds": [kind], "authors": [self._author], "limit": MOVE_PAGE}
        if which == self._REQUESTS:
            flt["#k"] = [str(DRAFT_WRAP_KIND)]           # as DraftDeletions reads them
        if until is not None:
            flt["until"] = until
        if self._since is not None:
            flt["since"] = self._since

        def accept(event: dict) -> bool:
            return (event.get("kind") == kind and created_at_of(event) is not None
                    and str(event.get("pubkey", "")).lower() == self._author)

        fetch_events(self._pool, self._source, [flt],
                     lambda fetched: self._on_page(which, until, fetched), accept=accept,
                     timeout_ms=MOVE_QUERY_TIMEOUT_MS, parent=self)

    def _on_page(self, which: str, until: Optional[int], fetched: Fetched) -> None:
        if self._done:
            return
        answered = set(fetched.answered)
        silent = self._must_read - answered
        if silent or not answered or not fetched.complete:
            # Drafts on a relay that is about to be left could not be
            # read: moving the rest would still strand them.
            self._finish(unread=silent or set(self._source) - answered or set(self._source))
            return
        for event in fetched.events:
            self._seen_on.setdefault(event["id"], set()).update(fetched.relays_of(event["id"]))
            if which == self._WRAPS:
                identifier = _tag(event, "d")
                if identifier and is_newer(event, self._wraps.get(identifier)):
                    self._wraps[identifier] = event
            else:
                self._requests[event["id"]] = event
                for identifier, when in deleted_identifiers(event, self._author).items():
                    self._deleted[identifier] = max(when, self._deleted.get(identifier, when))
        horizon = page_horizon(fetched, fetched.events, MOVE_PAGE)
        if horizon is not None:
            # A relay sent a full page: read on from where it ended, or past
            # a second that held a whole page, so the reading ends.
            self._read(which, horizon if until is None or horizon < until else until - 1)
        elif which == self._WRAPS:
            self._read(self._REQUESTS, None)
        else:
            self._send_all()

    # -- sending -------------------------------------------------------------

    def _send_all(self) -> None:
        now = int(self._clock())
        wraps = [wrap for identifier, wrap in self._wraps.items()
                 if not _expired(wrap, now)
                 and self._deleted.get(identifier, -1) < int(wrap["created_at"])]
        self._queue = [event for event in [*self._requests.values(), *wraps]
                       if event["id"] not in self._skip]
        self._total = len(self._queue)
        self._drafts = sum(1 for event in self._queue
                           if event["kind"] == DRAFT_WRAP_KIND and event.get("content"))
        self.progress.emit(0, self._total, self._drafts)
        if not self._queue:
            self._finish()
            return
        self._pump()

    def _pump(self) -> None:
        while self._queue and self._in_flight < SENDS_AT_ONCE and not self._done:
            event = self._queue.pop(0)
            has_it = self._seen_on.get(event["id"], set())
            targets = [url for url in self._destination if url not in has_it]
            if not self._destination or not targets:
                # Nowhere to go, or every relay there has it already.
                self._settle(event, kept=bool(self._destination))
                continue
            already = len(targets) < len(self._destination)
            self._in_flight += 1
            job = self._pool.publish(targets, event)
            job.all_done.connect(
                lambda results, e=event, a=already: self._on_sent(e, a, results))

    def _on_sent(self, event: dict, already: bool, results: list) -> None:
        self._in_flight -= 1
        kept = already or any(kept_by_relay(ok, message) for _url, ok, message in results)
        refused = not kept and any(not ok and refused_by_relay(message)
                                   for _url, ok, message in results)
        self._settle(event, kept=kept, refused=refused)
        self._pump()

    def _settle(self, event: dict, *, kept: bool, refused: bool = False) -> None:
        (self._moved if kept else self._not_moved).append(str(event["id"]))
        self._refused = self._refused or refused
        settled = len(self._moved) + len(self._not_moved)
        self.progress.emit(settled, self._total, self._drafts)
        if settled == self._total:
            self._finish()

    def _finish(self, *, unread: Set[str] = frozenset()) -> None:
        if self._done:
            return
        self._done = True
        self.finished.emit(MoveOutcome(moved=len(self._moved), total=self._total,
                                       unread=tuple(sorted(unread)),
                                       not_moved=tuple(self._not_moved),
                                       refused=self._refused))


@dataclass(frozen=True)
class SaveOutcome:
    """What saving the draft relays did: the drafts' move, then the list's
    own change (None when it was not tried, because the move failed)."""

    move: MoveOutcome
    write: Optional[WriteOutcome] = None

    @property
    def ok(self) -> bool:
        return self.move.ok and self.write is not None and self.write.ok


class _Save(QObject):
    """One change of the draft relays (PrivateDraftRelays.save), step by
    step: where drafts are read from now and go next, the move, the
    list, the catch-up, and then the routing.

    It lives as long as the PrivateDraftRelays it belongs to (one per
    sheet), and is not deleted earlier: a deferred delete still pending
    when its owner is freed from Python deletes it a second time."""

    def __init__(self, *, pool, directory, profile, entitled, clock,
                 current: List[str], chosen: List[str],
                 write: Callable[[Callable[[WriteOutcome], None]], None],
                 leave_unread: Sequence[str], on_done: Callable[[SaveOutcome], None],
                 on_step: Callable[[str, int, int, int], None],
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._pool = pool
        self._directory = directory
        self._profile = profile
        self._entitled = entitled
        self._clock = clock
        self._author = profile.user_pubkey.lower()
        self._current = current
        self._chosen = chosen
        self._write = write
        self._leave_unread = set(dedupe_relays(leave_unread))
        self._on_done = on_done
        self._on_step = on_step
        self._started = int(clock())
        self._routes: Dict[str, List[str]] = {}
        self._source: List[str] = []
        self._destination: List[str] = []
        self._must_read: List[str] = []
        self._move = MoveOutcome()
        self._moved_ids: List[str] = []
        self._written: Optional[WriteOutcome] = None

    def start(self) -> None:
        # Drafts saved from now on wait for where this leaves them.
        self._directory.expect_draft_relays(self._author, timeout_ms=MOVE_WAIT_MS)
        # The routing this device saves by may lag behind the list just
        # read (changed on another device since it started): drafts are
        # read from both.
        here = self._directory.draft_relays_of(self._author)
        here = self._current if here is None else here
        if set(self._chosen) == set(self._current) == set(here):
            self._write_list()                       # drafts stay where they are
            return
        questions = (("source", True, self._current), ("source_here", True, here),
                     ("destination", False, self._chosen), ("read_next", True, self._chosen))
        for name, reading, mine in questions:
            ask_draft_relays(self._directory, self._profile,
                             lambda relays, n=name: self._routed(n, relays),
                             entitled=self._entitled, reading=reading, chosen=mine)

    def _routed(self, name: str, relays: List[str]) -> None:
        self._routes[name] = list(relays)
        if len(self._routes) < 4:
            return
        self._source = dedupe_relays(self._routes["source"] + self._routes["source_here"])
        self._destination = self._routes["destination"]
        # Relays not read after the change must answer now, or their
        # drafts would vanish; but for those the person chose to leave.
        self._must_read = [url for url in self._source
                           if url not in self._routes["read_next"]
                           and url not in self._leave_unread]
        self._on_step(READING_DRAFTS, 0, 0, 0)
        mover = DraftMove(pool=self._pool, author=self._author, source=self._source,
                          destination=self._destination, must_read=self._must_read,
                          clock=self._clock, parent=self)
        mover.progress.connect(
            lambda done, total, drafts: self._on_step(MOVING_DRAFTS, done, total, drafts))
        mover.finished.connect(lambda outcome: self._moved(outcome, mover.moved_ids))
        mover.start()

    def _moved(self, outcome: MoveOutcome, moved_ids: List[str]) -> None:
        self._move, self._moved_ids = outcome, moved_ids
        if not outcome.ok:
            self._end(SaveOutcome(outcome), follow=None)
            return
        self._write_list()

    def _write_list(self) -> None:
        self._on_step(SAVING_LIST, 0, 0, 0)
        self._write(self._on_written)

    def _on_written(self, result: WriteOutcome) -> None:
        if result.status == REFUSED and not self._chosen:
            result = WriteOutcome(UNCHANGED)         # there was no list to stop
        if not result.ok:
            self._end(SaveOutcome(self._move, result), follow=None)
            return
        self._written = result
        if not self._routes:                         # nothing moved, nothing to catch up
            self._end(SaveOutcome(self._move, result), follow=self._chosen)
            return
        # What was saved by the old routing while the drafts moved and the
        # signer was asked (another device, or a save already under way).
        late = DraftMove(pool=self._pool, author=self._author, source=self._source,
                         destination=self._destination, must_read=self._must_read,
                         since=self._started - CATCH_UP_SLACK_S, skip=self._moved_ids,
                         clock=self._clock, parent=self)
        late.finished.connect(self._caught_up)
        late.start()

    def _caught_up(self, outcome: MoveOutcome) -> None:
        if not outcome.ok:
            logger.warning("drafts saved during the change were not all copied: %s", outcome)
        self._end(SaveOutcome(self._move, self._written), follow=self._chosen)

    def _end(self, outcome: SaveOutcome, *, follow: Optional[List[str]]) -> None:
        # None: drafts go where they went; either way saving them goes on.
        self._directory.set_draft_relays(self._author, follow)
        self._on_done(outcome)


# --------------------------------------------------------------------------- #
# The list                                                                    #
# --------------------------------------------------------------------------- #

_ANY = object()       # ``over``: write over whatever list there is


class PrivateDraftRelays(QObject):
    """Read or publish one account's private relay list for drafts.

    ``entitled_relays`` (a list, or a callable answering one) are the
    relays the account has standing on beyond its own list, a
    membership's: drafts go there too, so a move reads and writes them
    the way saving a draft does."""

    def __init__(self, *, profile, pool, session_pool, directory, entitled_relays=(),
                 query=fetch_replaceable, clock: Callable[[], float] = time.time,
                 parent=None) -> None:
        super().__init__(parent)
        self._profile = profile
        self._pool = pool
        self._session_pool = session_pool
        self._directory = directory
        self._entitled = entitled_relays
        self._query = query
        self._clock = clock
        # The list as last read here (None: not read yet), and the id of
        # the event it came from (None: there was none).
        self._known: Optional[List[str]] = None
        self._read_id: Optional[str] = None
        self.last_failure: Optional[str] = None    # why the last read answered None

    def read(self, on_done: Callable[[Optional[List[str]]], None]) -> None:
        """``on_done(relays)``, ``on_done([])`` for none, ``on_done(None)``
        when it could not be read (``last_failure`` is UNREACHABLE) or
        decrypted (UNDECRYPTED)."""
        author = self._profile.user_pubkey.lower()
        relays = lookup_relays(known=self._directory.cached(author), own=True)

        def answer(read: Optional[List[str]], event_id: Optional[str] = None,
                   failure: Optional[str] = None) -> None:
            self.last_failure = failure
            if read is not None:
                self._known, self._read_id = list(read), event_id
            on_done(read)

        def found(result) -> None:
            if result.state is LookupState.ABSENT:
                answer([])
            elif result.state is LookupState.UNKNOWN or result.event is None:
                answer(None, failure=UNREACHABLE)
            else:
                event = result.event
                self._decrypt(str(event.get("content") or ""), lambda read: answer(
                    read, event.get("id"), None if read is not None else UNDECRYPTED))

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

    def publish(self, relays: Sequence[str], on_done: Callable[[WriteOutcome], None], *,
                over=_ANY) -> None:
        """Encrypt and publish ``relays`` as the account's draft relays.
        Raises ValueError for an empty list, before asking anything.
        ``over``: the id of the list this change was made to (None for
        none); a list found to be another one is not written over, and the
        outcome's cause is CAUSE_CHANGED."""
        self._write(private_relays_plaintext(relays), on_absent="create", on_done=on_done,
                    over=over)

    def clear(self, on_done: Callable[[WriteOutcome], None], *, over=_ANY) -> None:
        """Go back to the usual relays: publish an empty list. An account
        with none has nothing to stop: nothing is published, and the
        outcome is REFUSED. ``over`` as for ``publish``."""
        self._write("[]", on_absent="refuse", on_done=on_done, over=over)

    def save(self, relays: Sequence[str], on_done: Callable[[SaveOutcome], None], *,
             on_step: Optional[Callable[[str, int, int, int], None]] = None,
             leave_unread: Sequence[str] = ()) -> None:
        """Make ``relays`` the account's draft relays, or with none go back
        to the usual relays: move the drafts to where they go next, publish
        the list, and then save drafts by it (see the module's text).

        ``on_step(step, done, total, drafts)`` says where it is:
        READING_DRAFTS; MOVING_DRAFTS with the events settled, to send, and
        the drafts with content among them; SAVING_LIST. ``leave_unread``
        names the relays being left whose drafts the person agreed to leave
        behind (they did not answer); every other relay being left must
        still answer."""
        chosen = dedupe_relays(relays, cap=defaults.PRIVATE_CAP)
        if relays and not chosen:
            raise ValueError("no usable relay for drafts")
        author = self._profile.user_pubkey.lower()
        read = self._known is not None
        current = self._known if read else self._directory.draft_relays_of(author) or []
        over = self._read_id if read else _ANY

        def write(on_written: Callable[[WriteOutcome], None]) -> None:
            if chosen:
                self.publish(chosen, on_written, over=over)
            else:
                self.clear(on_written, over=over)

        def done(outcome: SaveOutcome) -> None:
            if outcome.ok:
                self._known = list(chosen)
                self._read_id = (outcome.write.event or {}).get("id")
            on_done(outcome)

        _Save(pool=self._pool, directory=self._directory, profile=self._profile,
              entitled=self._entitled, clock=self._clock, current=list(current),
              chosen=chosen, write=write, leave_unread=list(leave_unread), on_done=done,
              on_step=on_step or (lambda _step, _done, _total, _drafts: None),
              parent=self).start()

    def _write(self, plaintext: str, *, on_absent: str,
               on_done: Callable[[WriteOutcome], None], over=_ANY) -> None:
        def failed(reason: str) -> None:
            on_done(WriteOutcome(FAILED, reason=reason or "The signer didn’t encrypt it.",
                                 cause=CAUSE_SIGNER))

        def encrypted(ciphertext: str) -> None:
            def mutate(base: Optional[dict]):
                if over is not _ANY and (base or {}).get("id") != over:
                    raise BaseChanged("The list changed after it was read.")
                return ciphertext, []

            writer = ReplaceableWriter(
                kind=KIND_PRIVATE_RELAYS, mutate=mutate, on_absent=on_absent,
                pool=self._pool, directory=self._directory,
                session_pool=self._session_pool, profile=self._profile,
                query=self._query, clock=self._clock, parent=self)
            writer.finished.connect(on_done)
            writer.start()

        self._session_pool.get(
            self._profile,
            lambda client: client.nip44_encrypt_self(plaintext, encrypted, failed),
            failed)
