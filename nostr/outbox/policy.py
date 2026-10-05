# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""NIP-65 rules as pure functions: where to read, where to write.

Spec: https://github.com/nostr-protocol/nips/blob/master/65.md

A relay list (kind 10002) is ``["r", url, marker?]`` tags. ``write``
relays are a person's outbox: where they publish, and where everyone
else reads what they wrote. ``read`` relays are their inbox: where they
look for things that mention them, so a note that tags someone also goes
there. No marker means both.

Everything here is data in, data out: no Qt, no network, no clock. The
directory (directory.py) does the looking up and caching; the writer
(writer.py) does the safe changing; both decide nothing on their own and
call these functions instead, so every rule has one home and one test.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from .. import events
from . import defaults

KIND_RELAY_LIST = 10002
KIND_PROFILE = 0


class LookupState(str, enum.Enum):
    """What looking someone's relay list up established."""

    FOUND = "found"        # a validly signed list is in hand
    ABSENT = "absent"      # enough relays answered "nothing stored" (see ABSENT_QUORUM)
    UNKNOWN = "unknown"    # timeouts, refusals, or too few answers: no evidence either way


@dataclass
class RelayList:
    """One person's relay list, and how sure we are of it."""

    write: List[str] = field(default_factory=list)
    read: List[str] = field(default_factory=list)
    state: LookupState = LookupState.UNKNOWN
    event: Optional[dict] = None          # the verified kind 10002, when FOUND
    fetched_at: float = 0.0

    @property
    def is_empty(self) -> bool:
        return not self.write and not self.read

    @property
    def found(self) -> bool:
        return self.state is LookupState.FOUND

    @property
    def created_at(self) -> int:
        return created_at_of(self.event) or 0


# --------------------------------------------------------------------------- #
# Relay URLs                                                                  #
# --------------------------------------------------------------------------- #

def normalize_relay_url(url) -> Optional[str]:
    """``wss://host[/path]`` with scheme and host lowercased and no trailing
    slash, or None for anything that is not a websocket URL."""
    if not isinstance(url, str):
        return None
    text = url.strip().rstrip("/")
    if "://" not in text or any(c.isspace() for c in text):
        return None
    scheme, rest = text.split("://", 1)
    scheme = scheme.lower()
    if scheme not in ("ws", "wss") or not rest:
        return None
    host, _, path = rest.partition("/")
    if not host or "@" in host:
        return None
    return f"{scheme}://{host.lower()}" + (f"/{path}" if path else "")


def dedupe_relays(*groups: Iterable[str], cap: Optional[int] = None) -> List[str]:
    """The relays of every group, normalized, first occurrence kept, in order."""
    out: List[str] = []
    seen = set()
    for group in groups:
        for url in group or ():
            normalized = normalize_relay_url(url)
            if normalized is None or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
            if cap is not None and len(out) >= cap:
                return out
    return out


# --------------------------------------------------------------------------- #
# Reading relay lists                                                         #
# --------------------------------------------------------------------------- #

def parse_relay_list(event: dict) -> RelayList:
    """Write and read relays from a kind 10002. Unknown markers count as
    both, so a typo loses nothing. The result is FOUND: callers pass only
    events they have verified (see newest_valid)."""
    write: List[str] = []
    read: List[str] = []
    for tag in event.get("tags", []) if isinstance(event, dict) else ():
        if not isinstance(tag, list) or len(tag) < 2 or tag[0] != "r":
            continue
        url = normalize_relay_url(tag[1])
        if url is None:
            continue
        marker = tag[2].lower() if len(tag) >= 3 and isinstance(tag[2], str) else ""
        if marker != "write" and url not in read:
            read.append(url)
        if marker != "read" and url not in write:
            write.append(url)
    return RelayList(write=write, read=read, state=LookupState.FOUND,
                     event=event if isinstance(event, dict) else None)


def created_at_of(event) -> Optional[int]:
    """An event's ``created_at`` when it is a real integer, else None.

    A relay can re-serve a validly signed event with ``created_at`` as a
    string or a float: the signature still checks out (the id is computed
    over the integer), but comparing it with an integer raises or lies.
    Such a copy is not competed with at all.
    """
    if not isinstance(event, dict):
        return None
    value = event.get("created_at")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def is_newer(event: dict, than: Optional[dict]) -> bool:
    """Whether ``event`` replaces ``than`` (NIP-01): a later ``created_at``
    wins, and on equal ones the lowest id. Neither is verified here."""
    created = created_at_of(event)
    if created is None:
        return False
    if than is None:
        return True
    previous = created_at_of(than)
    if previous is None or created > previous:
        return True
    return created == previous and str(event.get("id", "")) < str(than.get("id", ""))


def newest_valid(candidates: Iterable[dict], *, kind: int, author: str) -> Optional[dict]:
    """The newest event that really is ``author``'s ``kind``.

    The signature is checked before anything competes, so a forged event
    with a large timestamp cannot hide the real one. NIP-01: on equal
    ``created_at`` the lowest id wins. An event whose ``created_at`` is
    not an integer does not compete (see created_at_of).
    """
    author = (author or "").lower()
    best: Optional[dict] = None
    for event in candidates:
        if not isinstance(event, dict) or event.get("kind") != kind:
            continue
        if str(event.get("pubkey", "")).lower() != author:
            continue
        if not is_newer(event, best):
            continue                      # cheap, before the signature check
        if not events.verify_event(event):
            continue
        best = event
    return best


# --------------------------------------------------------------------------- #
# Changing relay lists and profiles                                           #
# --------------------------------------------------------------------------- #

def relay_list_tags_adding(
    existing_event: Optional[dict],
    url: str,
    *,
    marker: str = "write",
) -> Optional[List[List[str]]]:
    """Tags for a kind 10002 that adds ``url`` and changes nothing else.

    Returns None when the addition must not be published, which is the
    important half of this function. A kind 10002 is replaceable, so
    publishing one built from an unread list does not add a relay, it
    replaces the user's entire list with whatever we happened to know.
    Losing an author's relay list scatters their readers, and no undo
    exists once relays have taken the replacement. So a caller with no
    confirmed current event gets None and must refuse.

    None is also returned when there is nothing to do, so a caller never
    asks the signer to approve a no-op. That covers a relay already
    listed under any marker, including read-only: silently promoting a
    read entry to a write one would be rewriting a choice the user made,
    not adding to it.
    """
    if not isinstance(existing_event, dict):
        return None
    normalized = normalize_relay_url(url)
    if not normalized:
        return None
    tags: List[List[str]] = []
    for tag in existing_event.get("tags", []):
        if not isinstance(tag, list):
            continue
        tags.append([str(part) for part in tag])
        if len(tag) >= 2 and tag[0] == "r" and normalize_relay_url(tag[1]) == normalized:
            return None  # already listed, under whatever marker they chose
    tags.append(["r", normalized, marker] if marker else ["r", normalized])
    return tags


def starter_relay_list_tags(entries=defaults.STARTER_LIST, *,
                            extra_write: Iterable[str] = ()) -> List[List[str]]:
    """The tags of a brand-new account's relay list."""
    tags: List[List[str]] = []
    seen = set()
    for url, marker in entries:
        normalized = normalize_relay_url(url)
        if normalized and normalized not in seen:
            seen.add(normalized)
            tags.append(["r", normalized, marker] if marker else ["r", normalized])
    for url in extra_write:
        normalized = normalize_relay_url(url)
        if normalized and normalized not in seen:
            seen.add(normalized)
            tags.append(["r", normalized, "write"])
    return tags


def merge_profile_content(existing_content: Optional[str],
                          changes: Mapping[str, Optional[str]]) -> str:
    """A kind 0 content with ``changes`` applied over the existing one.

    Fields MyEditor does not know (lud16, website, banner, ...) are kept.
    A change to None or "" removes that field. A base that is not a JSON
    object raises ValueError: guessing at it could erase a profile.
    """
    if existing_content in (None, ""):
        base: Dict[str, object] = {}
    else:
        base = json.loads(existing_content)
        if not isinstance(base, dict):
            raise ValueError("the existing profile is not a JSON object")
    merged = dict(base)
    for key, value in changes.items():
        if value is None or value == "":
            merged.pop(key, None)
        else:
            merged[key] = value
    return json.dumps(merged, ensure_ascii=False, separators=(",", ":"))


def replacement_created_at(base: Optional[dict], now: float) -> int:
    """A replaceable event's time: now, but always after the one it replaces,
    even when this computer's clock runs behind."""
    previous = created_at_of(base) or 0
    return max(int(now), previous + 1)


# --------------------------------------------------------------------------- #
# Routing                                                                     #
# --------------------------------------------------------------------------- #

def lookup_relays(*, hints: Iterable[str] = (), known: Optional[RelayList] = None) -> List[str]:
    """Where to ask for someone's relay list, profile or other replaceable
    event (contact list, media server list): their own write relays when
    known, then the indexers."""
    own = known.write if known is not None and known.found else ()
    return dedupe_relays(hints, own, defaults.INDEXER_RELAYS, defaults.FALLBACK_RELAYS[:3],
                         cap=defaults.LOOKUP_CAP)


def bulk_profile_relays() -> List[str]:
    """Where to ask for many people's profiles in one request (everyone a
    user follows): the indexers, which collect everyone's, then the
    fallback set. Looking up each person's outbox would be hundreds of
    lookups for a picker that only needs names and pictures."""
    return dedupe_relays(defaults.INDEXER_RELAYS, defaults.FALLBACK_RELAYS)


@dataclass(frozen=True)
class PublishPlan:
    """Where one of the author's public events goes."""

    author: tuple = ()     # the author's outbox, where their readers look
    inbox: tuple = ()      # mentioned people's read relays not already above

    @property
    def targets(self) -> List[str]:
        return [*self.author, *self.inbox]


def author_relays(author: RelayList, *, entitled: Iterable[str] = ()) -> List[str]:
    """The author's write relays first, never displaced by anything MyEditor
    adds; entitled relays (a membership's) after them; topped up from the
    fallback set only when fewer than MIN_WRITE_TARGETS remain."""
    own = author.write[:defaults.WRITE_CAP] if author.found else []
    relays = dedupe_relays(own, entitled)
    if len(relays) < defaults.MIN_WRITE_TARGETS:
        relays = dedupe_relays(relays, defaults.FALLBACK_RELAYS[:3])
    return relays


def plan_publish(author: RelayList, *,
                 mentioned: Mapping[str, RelayList] = (),
                 hints: Mapping[str, str] = (),
                 entitled: Iterable[str] = ()) -> PublishPlan:
    """The outbox and inbox routing for one public event (NIP-65).

    NIP-65 sends a note to all of a mentioned person's read relays. Here
    each person gets their first INBOX_PER_MENTION, and all mentions
    together INBOX_TOTAL_CAP, so a note naming many people does not go
    to dozens of relays. The slots go round: every person's first read
    relay, then every person's second, so the people named last are not
    left out because the first ones used up the cap. A person whose list
    is unknown is reached through the relay hint of their mention.
    """
    own = author_relays(author, entitled=entitled)
    taken = set(own)
    hints = dict(hints)
    choices: List[List[str]] = []
    for pubkey, relay_list in dict(mentioned).items():
        if relay_list is not None and relay_list.found and relay_list.read:
            theirs = relay_list.read
        else:
            hint = hints.get(pubkey)
            theirs = [hint] if hint else []
        choices.append(dedupe_relays(theirs, cap=defaults.INBOX_PER_MENTION))
    inbox: List[str] = []
    for rank in range(defaults.INBOX_PER_MENTION):
        for theirs in choices:
            if len(inbox) >= defaults.INBOX_TOTAL_CAP:
                break
            if rank < len(theirs) and theirs[rank] not in taken:
                taken.add(theirs[rank])
                inbox.append(theirs[rank])
    return PublishPlan(author=tuple(own), inbox=tuple(inbox))


def private_relays(author: RelayList, *, entitled: Iterable[str] = (),
                   legacy: Iterable[str] = ()) -> List[str]:
    """Where the author's private records live (drafts, app data, private
    files). One function serves both writing and reading, so what one
    device stores is exactly where another device looks.

    ``legacy`` are relays such records were kept on before, appended so
    they stay readable (and keep receiving updates)."""
    if author.found and not author.is_empty:
        own = dedupe_relays(author.write, author.read, entitled)
    else:
        own = dedupe_relays(defaults.FALLBACK_RELAYS, entitled)
    return dedupe_relays(own, legacy, cap=defaults.PRIVATE_CAP)


def outbox_relays(author: RelayList, *, hints: Iterable[str] = ()) -> List[str]:
    """Where to read what someone wrote: their write relays, or the
    fallback set when their list is unknown."""
    if author.found and author.write:
        return dedupe_relays(hints, author.write[:defaults.WRITE_CAP])
    return dedupe_relays(hints, defaults.FALLBACK_RELAYS)


def inbox_relays(user: RelayList) -> List[str]:
    """Where to send things addressed to someone: their read relays."""
    if user.found and user.read:
        return dedupe_relays(user.read[:defaults.INBOX_PER_MENTION])
    return []


def relay_list_targets(new_event: dict, old: Optional[RelayList]) -> List[str]:
    """Where a new relay list goes: every relay it names, every relay the old
    one named (so stale copies are replaced there too), and the indexers."""
    new = parse_relay_list(new_event)
    old_relays = (old.write + old.read) if old is not None and old.found else []
    return dedupe_relays(new.write, new.read, old_relays, defaults.INDEXER_RELAYS)


def profile_targets(author: RelayList) -> List[str]:
    """Where a profile (kind 0) goes: the author's outbox and the indexers."""
    return dedupe_relays(author_relays(author), defaults.INDEXER_RELAYS)
