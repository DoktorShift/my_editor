# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What an account published: its articles and notes, read back from its relays.

A writer expects to see what they published and to take something back.
:class:`PublishedList` reads one account's articles (NIP-23, kind 30023,
the newest version of each) and notes (kind 1, newest first, a page at a
time) from the relays the account writes to (RelayDirectory.outbox_of),
leaves out what a deletion request by the same author covers (NIP-09,
nostr/deletion.py), and deletes an item through DeletionJob, hiding it as
soon as one relay took the request.

Reading:

- One request asks for the newest PAGE_SIZE notes and for the articles
  (at most ARTICLE_LIMIT); then the deletion requests naming what came
  back are asked for, by id and by address. When no relay answers either
  question the list says it could not be read, never that nothing was
  published: an item a deletion covers must not be shown as if it were
  still out there.
- Notes come a page at a time, and every relay answers a page with its
  own newest notes. The list is complete only down to the oldest note of
  the relay that sent a full page and went least far back (the horizon,
  :func:`notes_horizon`); older notes stay out of the list until Load
  More moves it, so a page never opens a gap in the middle.
- Articles: the newest version of each identifier that no deletion
  covers, all of them at once (there are few, and a writer looks for
  them). Every item knows each relay it was found on.

An account change drops every late answer (a generation number). The
deletion requests this app sent are remembered for the session, so an
item taken back stays hidden even when a relay that missed the request
answers a later refresh.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from PySide6.QtCore import QObject, Signal

import link_url
import url_safety

from .bech32 import encode_naddr, encode_nevent
from .deletion import (
    DELETION_KIND, DeletionJob, DeletionRequest, address_of, read_deletion,
)
from .outbox.policy import created_at_of, is_newer
from .publisher import mentioned_pubkeys, published_at_of
from .queries import Fetched, fetch_events

NOTE_KIND = 1
ARTICLE_KIND = 30023
PAGE_SIZE = 50                # notes per page
ARTICLE_LIMIT = 500           # articles asked for at once
TAG_BATCH = 100               # ids or addresses in one filter
FILTERS_PER_REQUEST = 4       # filters in one request
QUERY_TIMEOUT_MS = 8_000
LINK_HINTS = 2                # relays a link names, so a reader finds the item

# What the list is doing.
SIGNED_OUT = "signed_out"     # no account
LOADING = "loading"           # the first page is on its way
READY = "ready"               # read (possibly with nothing in it)
UNREACHABLE = "unreachable"   # no relay answered

# An item's deletion.
DELETING = "deleting"
NOT_DELETED = "not_deleted"   # the request was not taken; Try Again


# --------------------------------------------------------------------------- #
# One published item                                                          #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PublishedItem:
    """A note, or the newest version of an article, and where it was found."""

    event: dict
    found_on: Tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return str(self.event["id"])

    @property
    def kind(self) -> int:
        return int(self.event["kind"])

    @property
    def is_article(self) -> bool:
        return self.kind == ARTICLE_KIND

    @property
    def pubkey(self) -> str:
        return str(self.event["pubkey"]).lower()

    @property
    def created_at(self) -> int:
        return int(self.event["created_at"])

    @property
    def identifier(self) -> str:
        """An article's identifier (its ``d``), "" for a note."""
        return _tag(self.event, "d") if self.is_article else ""

    @property
    def first_published(self) -> int:
        """When it first went out: an article's ``published_at`` (NIP-23),
        kept across edits, or the time it was made."""
        if self.is_article:
            return published_at_of(self.event) or self.created_at
        return self.created_at

    @property
    def title(self) -> str:
        """An article's title, or its first line without one; a note's
        first line. "" when there are no words at all."""
        if self.is_article:
            title = " ".join(_tag(self.event, "title").split())
            if title:
                return title
            return first_line(self.event.get("content", "")).lstrip("#").strip()
        return first_line(self.event.get("content", ""))

    @property
    def mentioned(self) -> List[Tuple[str, str]]:
        """``(pubkey, relay hint)`` for everyone it mentions."""
        return mentioned_pubkeys(self.event)

    def nostr_address(self) -> str:
        """``naddr1…`` for an article (every version), ``nevent1…`` for a
        note, naming relays it was found on so a reader finds it."""
        hints = [url for url in self.found_on if url.isascii()][:LINK_HINTS]
        if self.is_article:
            return encode_naddr(self.identifier, self.pubkey, ARTICLE_KIND, hints)
        return encode_nevent(self.id, hints, self.pubkey, self.kind)

    def web_link(self) -> Optional[str]:
        """A web address any browser shows it at, None when there is none
        it is safe to open."""
        link = link_url.web_address_for("nostr:" + self.nostr_address())
        return link if link and url_safety.is_safe_external_url(link) else None


def first_line(text: str, *, limit: int = 200) -> str:
    """The first line with words on it, its spacing evened out."""
    for line in str(text or "").splitlines():
        words = " ".join(line.split())
        if words:
            return words[:limit]
    return ""


def _tag(event: dict, name: str) -> str:
    for tag in event.get("tags", []) or []:
        if isinstance(tag, list) and len(tag) >= 2 and tag[0] == name:
            return str(tag[1])
    return ""


# --------------------------------------------------------------------------- #
# Rules                                                                       #
# --------------------------------------------------------------------------- #

def notes_horizon(fetched: Fetched, notes: Iterable[dict], *,
                  page_size: int = PAGE_SIZE) -> Optional[int]:
    """How far back a page of notes is complete, None when it is all.

    Each relay answers with its own newest ``page_size`` notes. One that
    sent fewer has nothing older; one that sent a full page may have,
    from before its oldest note. So the page is complete down to the
    oldest note of the relay that sent a full page and went least far
    back, and the next page asks for notes from there on.
    """
    times: Dict[str, List[int]] = {}
    for note in notes:
        for relay in fetched.relays_of(note["id"]):
            times.setdefault(relay, []).append(int(note["created_at"]))
    full = [min(found) for found in times.values() if len(found) >= page_size]
    return max(full) if full else None


def deletion_requests(author: str, event_ids: Iterable[str],
                      addresses: Iterable[str]) -> List[List[dict]]:
    """The requests (each a list of filters) for the author's deletion
    requests that name these events, by id or by address."""
    def chunks(values, size):
        values = list(values)
        return [values[i:i + size] for i in range(0, len(values), size)]

    base = {"kinds": [DELETION_KIND], "authors": [author]}
    filters = [dict(base, **{"#e": batch}) for batch in chunks(sorted(set(event_ids)), TAG_BATCH)]
    filters += [dict(base, **{"#a": batch}) for batch in chunks(sorted(set(addresses)), TAG_BATCH)]
    return chunks(filters, FILTERS_PER_REQUEST)


# --------------------------------------------------------------------------- #
# The list                                                                    #
# --------------------------------------------------------------------------- #

class PublishedList(QObject):
    """One account's published notes and articles, and taking them back.

    Signals:
      changed()                       the items, the state, or an item's
                                      deletion changed
      deletion_status(object, str)    a deletion's progress, in words
                                      (the item, the words)
      deletion_done(object, list)     a relay took the request: the item
                                      is gone from the list (the item,
                                      every relay's answer)
      deletion_not_taken(object, list)
                                      no relay took it; ``try_again`` sends
                                      it again, ``dismiss`` lets it be
      deletion_refused(object, str)   the signer did not sign (the item,
                                      why); ``try_again`` asks again
    """

    changed = Signal()
    deletion_status = Signal(object, str)
    deletion_done = Signal(object, list)
    deletion_not_taken = Signal(object, list)
    deletion_refused = Signal(object, str)

    def __init__(self, *, relay_pool, relay_directory, session_pool,
                 entitled_relays: Optional[Callable[[], Sequence[str]]] = None,
                 clock: Callable[[], float] = time.time,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._pool = relay_pool
        self._directory = relay_directory
        self._session_pool = session_pool
        self._entitled = entitled_relays
        self._clock = clock
        self._profile = None
        self._generation = 0
        self._state = SIGNED_OUT
        self._relays: List[str] = []
        self._notes: Dict[str, dict] = {}
        self._articles: Dict[str, dict] = {}            # every version, by id
        self._seen_on: Dict[str, Set[str]] = {}
        self._requests: Dict[str, DeletionRequest] = {}
        # author -> the requests this app sent that a relay took, by id
        self._sent_requests: Dict[str, Dict[str, DeletionRequest]] = {}
        self._horizon: Optional[int] = None
        self._has_more = False
        self._loading_more = False
        self._more_failed = False
        self._refreshing = False
        self._refresh_failed = False
        self._partial = False
        self._items: List[PublishedItem] = []
        self._jobs: Dict[str, DeletionJob] = {}
        self._deleting: Dict[str, str] = {}
        self._deleting_items: Dict[str, PublishedItem] = {}

    # -- what is known ------------------------------------------------------

    @property
    def account(self):
        return self._profile

    @property
    def state(self) -> str:
        return self._state

    def items(self) -> List[PublishedItem]:
        """What to show: newest first by first publication."""
        return list(self._items)

    def item(self, event_id: str) -> Optional[PublishedItem]:
        return next((item for item in self._items if item.id == event_id), None)

    @property
    def has_more(self) -> bool:
        """Older notes may be there (Load More)."""
        return self._has_more

    @property
    def loading_more(self) -> bool:
        return self._loading_more

    @property
    def more_failed(self) -> bool:
        """The last Load More found no relay."""
        return self._more_failed

    @property
    def refreshing(self) -> bool:
        return self._refreshing

    @property
    def refresh_failed(self) -> bool:
        """The last refresh found no relay; what is shown is from before."""
        return self._refresh_failed

    @property
    def partial(self) -> bool:
        """Some relays did not answer, so something may be missing."""
        return self._partial

    def deletion_state(self, event_id: str) -> str:
        """DELETING, NOT_DELETED, or "" for an item nobody is deleting."""
        return self._deleting.get(event_id, "")

    # -- the account --------------------------------------------------------

    def set_account(self, profile) -> None:
        """Show ``profile``'s items (None: nobody's). The same account
        again changes nothing; another one starts over, and whatever is
        still on its way for the previous one is dropped."""
        same = (profile is not None and self._profile is not None
                and profile.user_pubkey.lower() == self._profile.user_pubkey.lower())
        self._profile = profile
        if same:
            return
        self._forget()
        if profile is None:
            self._state = SIGNED_OUT
            self.changed.emit()
            return
        self._state = LOADING
        self.changed.emit()
        self._read_first_page()

    def refresh(self) -> None:
        """Read again. What is shown stays until the new answers are in."""
        if self._profile is None:
            return
        self._generation += 1
        self._loading_more = False
        if self._state == READY:
            self._refreshing = True
            self._refresh_failed = False
        else:
            self._state = LOADING
        self.changed.emit()
        self._read_first_page()

    def load_more(self) -> None:
        """The next page of older notes."""
        if self._state != READY or not self._has_more or self._loading_more:
            return
        self._loading_more = True
        self._more_failed = False
        self.changed.emit()
        generation, horizon = self._generation, self._horizon
        author = self._author()
        self._ask(self._relays, [{"kinds": [NOTE_KIND], "authors": [author],
                                  "limit": PAGE_SIZE, "until": horizon}],
                  lambda fetched: self._on_more(generation, horizon, fetched))

    # -- deleting -----------------------------------------------------------

    def delete(self, event_id: str) -> None:
        """Ask relays to delete the item (confirmed by the person first)."""
        item = self.item(event_id)
        if item is None or self._profile is None or self._deleting.get(event_id) == DELETING:
            return
        self._drop_job(event_id)
        job = DeletionJob(relay_pool=self._pool, relay_directory=self._directory,
                          session_pool=self._session_pool, profile=self._profile,
                          event=item.event, found_on=item.found_on,
                          mentioned=item.mentioned, entitled_relays=self._entitled,
                          clock=self._clock, parent=self)
        self._jobs[event_id] = job
        self._deleting[event_id] = DELETING
        self._deleting_items[event_id] = item
        author = self._author()
        job.status_changed.connect(lambda text, j=job: self._on_job_status(j, event_id, text))
        job.first_accept.connect(lambda _url, j=job: self._on_taken(j, event_id, author))
        job.completed.connect(lambda results, j=job: self._on_answered(j, event_id, results))
        job.failed.connect(lambda reason, j=job: self._on_refused(j, event_id, reason))
        self.changed.emit()
        job.start()

    def try_again(self, event_id: str) -> None:
        """After a deletion that did not go through: send the signed
        request again, or ask the signer again when nothing was signed."""
        job = self._jobs.get(event_id)
        if job is not None and job.send_again():
            self._deleting[event_id] = DELETING
            self.changed.emit()
            return
        self._deleting.pop(event_id, None)
        self.delete(event_id)

    def dismiss(self, event_id: str) -> None:
        """Let a deletion that did not go through be: the item stays."""
        if self._deleting.get(event_id) != NOT_DELETED:
            return
        self._drop_job(event_id)
        self._deleting.pop(event_id, None)
        self._deleting_items.pop(event_id, None)
        self.changed.emit()

    # -- reading ------------------------------------------------------------

    def _author(self) -> str:
        return self._profile.user_pubkey.lower() if self._profile is not None else ""

    def _accepts(self, kinds: Tuple[int, ...]) -> Callable[[dict], bool]:
        author = self._author()

        def accept(event: dict) -> bool:
            return (str(event.get("pubkey", "")).lower() == author
                    and event.get("kind") in kinds and created_at_of(event) is not None)
        return accept

    def _ask(self, relays: Sequence[str], filters: List[dict],
             on_done: Callable[[Fetched], None],
             kinds: Tuple[int, ...] = (NOTE_KIND, ARTICLE_KIND)) -> None:
        fetch_events(self._pool, relays, filters, on_done, accept=self._accepts(kinds),
                     timeout_ms=QUERY_TIMEOUT_MS, parent=self)

    def _read_first_page(self) -> None:
        generation = self._generation
        self._directory.outbox_of(self._author(),
                                  lambda relays: self._ask_first_page(generation, relays))

    def _ask_first_page(self, generation: int, relays: Sequence[str]) -> None:
        if generation != self._generation:
            return
        relays = list(relays)
        author = self._author()
        self._ask(relays, [{"kinds": [NOTE_KIND], "authors": [author], "limit": PAGE_SIZE},
                           {"kinds": [ARTICLE_KIND], "authors": [author],
                            "limit": ARTICLE_LIMIT}],
                  lambda fetched: self._on_first_page(generation, relays, fetched))

    def _on_first_page(self, generation: int, relays: List[str], fetched: Fetched) -> None:
        if generation != self._generation:
            return
        if not fetched.answered:
            self._unreachable()
            return
        self._find_deletions(generation, relays, fetched.events,
                             lambda found, answered: self._take_first_page(
                                 generation, relays, fetched, found, answered))

    def _take_first_page(self, generation: int, relays: List[str], fetched: Fetched,
                         found: Dict[str, DeletionRequest], answered: bool) -> None:
        if generation != self._generation:
            return
        if not answered:
            self._unreachable()
            return
        self._relays = relays
        self._notes, self._articles, self._seen_on = {}, {}, {}
        self._requests = dict(found)
        self._absorb(fetched)
        notes = [e for e in fetched.events if e["kind"] == NOTE_KIND]
        self._horizon = notes_horizon(fetched, notes)
        self._has_more = self._horizon is not None
        self._more_failed = False
        self._partial = len(fetched.answered) < len(relays)
        self._state = READY
        self._refreshing = self._refresh_failed = False
        self._rebuild()
        self.changed.emit()

    def _unreachable(self) -> None:
        if self._refreshing:
            # A refresh that found no relay keeps what was read before.
            self._refreshing = False
            self._refresh_failed = True
        else:
            self._state = UNREACHABLE
        self.changed.emit()

    def _on_more(self, generation: int, horizon: int, fetched: Fetched) -> None:
        if generation != self._generation:
            return
        if not fetched.answered:
            self._loading_more = False
            self._more_failed = True
            self.changed.emit()
            return
        self._find_deletions(generation, self._relays, fetched.events,
                             lambda found, answered: self._take_more(
                                 generation, horizon, fetched, found, answered))

    def _take_more(self, generation: int, horizon: int, fetched: Fetched,
                   found: Dict[str, DeletionRequest], answered: bool) -> None:
        if generation != self._generation:
            return
        self._loading_more = False
        if not answered:
            self._more_failed = True
            self.changed.emit()
            return
        self._requests.update(found)
        self._absorb(fetched)
        reached = notes_horizon(fetched, [e for e in fetched.events if e["kind"] == NOTE_KIND])
        if reached is None:
            self._horizon, self._has_more = None, False
        else:
            # A full page that ends at the horizon itself means more notes
            # share that second than one page holds, and a page cannot
            # start inside a second: go on from the second before it.
            self._horizon, self._has_more = min(reached, horizon - 1), True
        self._rebuild()
        self.changed.emit()

    def _find_deletions(self, generation: int, relays: Sequence[str], found: Iterable[dict],
                        on_done: Callable[[Dict[str, DeletionRequest], bool], None]) -> None:
        """The author's deletion requests that name ``found``; ``answered``
        is False when some request reached no relay at all."""
        found = list(found)
        author = self._author()
        requests = deletion_requests(
            author, [e["id"] for e in found],
            [address_of(e) for e in found if e["kind"] == ARTICLE_KIND])
        if not requests:
            on_done({}, True)
            return
        state = {"left": len(requests), "answered": True, "found": {}}

        def one(fetched: Fetched) -> None:
            if generation != self._generation:
                return
            state["answered"] = state["answered"] and bool(fetched.answered)
            for event in fetched.events:
                request = read_deletion(event, author)
                if request is not None:
                    state["found"][request.id] = request
            state["left"] -= 1
            if state["left"] == 0:
                on_done(state["found"], state["answered"])

        for filters in requests:
            self._ask(relays, filters, one, kinds=(DELETION_KIND,))

    def _absorb(self, fetched: Fetched) -> None:
        for event in fetched.events:
            target = self._notes if event["kind"] == NOTE_KIND else self._articles
            target.setdefault(event["id"], event)
            self._seen_on.setdefault(event["id"], set()).update(fetched.relays_of(event["id"]))

    def _rebuild(self) -> None:
        """Work out what to show from what is known."""
        requests = list(self._requests.values())
        requests += list(self._sent_requests.get(self._author(), {}).values())

        def covered(event: dict) -> bool:
            return any(request.covers(event) for request in requests)

        def where(ids: Iterable[str]) -> Tuple[str, ...]:
            relays: Set[str] = set()
            for event_id in ids:
                relays |= self._seen_on.get(event_id, set())
            order = {url: index for index, url in enumerate(self._relays)}
            return tuple(sorted(relays, key=lambda url: (order.get(url, len(order)), url)))

        items = [PublishedItem(note, where([note["id"]])) for note in self._notes.values()
                 if not covered(note) and (not self._has_more
                                           or note["created_at"] >= self._horizon)]
        versions: Dict[str, List[dict]] = {}
        for version in self._articles.values():
            versions.setdefault(_tag(version, "d"), []).append(version)
        for same in versions.values():
            newest = None
            for version in same:
                if not covered(version) and is_newer(version, newest):
                    newest = version
            if newest is not None:
                items.append(PublishedItem(newest, where(v["id"] for v in same)))
        items.sort(key=lambda item: (-item.first_published, -item.created_at, item.id))
        self._items = items

    # -- deleting, inside ---------------------------------------------------

    def _current(self, job: DeletionJob, event_id: str) -> bool:
        return self._jobs.get(event_id) is job

    def _on_job_status(self, job: DeletionJob, event_id: str, text: str) -> None:
        if self._current(job, event_id):
            self.deletion_status.emit(self._deleting_items[event_id], text)

    def _on_taken(self, job: DeletionJob, event_id: str, author: str) -> None:
        if not self._current(job, event_id):
            return
        request = read_deletion(job.request or {}, author)
        if request is not None:
            self._sent_requests.setdefault(author, {})[request.id] = request
        self._deleting.pop(event_id, None)
        self._rebuild()
        self.changed.emit()

    def _on_answered(self, job: DeletionJob, event_id: str, results: list) -> None:
        if not self._current(job, event_id):
            return
        item = self._deleting_items[event_id]
        if any(ok for _url, ok, _message in results):
            self._drop_job(event_id)
            self._deleting_items.pop(event_id, None)
            self.deletion_done.emit(item, list(results))
            return
        self._deleting[event_id] = NOT_DELETED
        self.changed.emit()
        self.deletion_not_taken.emit(item, list(results))

    def _on_refused(self, job: DeletionJob, event_id: str, reason: str) -> None:
        if not self._current(job, event_id):
            return
        item = self._deleting_items[event_id]
        self._drop_job(event_id)
        self._deleting[event_id] = NOT_DELETED
        self.changed.emit()
        self.deletion_refused.emit(item, reason)

    def _drop_job(self, event_id: str) -> None:
        job = self._jobs.pop(event_id, None)
        if job is not None:
            job.cancel()
            # Let go of it first, so the deferred delete is the only way it
            # goes: its connections hold this list, and a list torn down
            # by that would otherwise delete it a second time
            # (outbox/lookup.py has the same rule for its queries).
            job.setParent(None)
            job.deleteLater()

    def _forget(self) -> None:
        """Everything about the account shown so far, and every answer
        still on its way for it."""
        self._generation += 1
        for event_id in list(self._jobs):
            self._drop_job(event_id)
        self._deleting.clear()
        self._deleting_items.clear()
        self._relays = []
        self._notes, self._articles, self._seen_on, self._requests = {}, {}, {}, {}
        self._horizon, self._has_more = None, False
        self._loading_more = self._more_failed = False
        self._refreshing = self._refresh_failed = self._partial = False
        self._items = []
