# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""An article's details, kept the same way wherever they travel.

A long-form article (NIP-23, kind 30023) keeps its details in tags: its
identifier (``d``), title, summary, cover image, hashtags (``t``) and the
time it was first published (``published_at``). Every other tag is
carried as it is, without editing it: where an imported article came
from (``source``), what is known about its pictures (``imeta``, NIP-92),
and whatever another app gave an article MyEditor edits now (zap splits,
a content warning, alt text, labels). Only the tags MyEditor writes
itself are not carried: the details, the app's name (``client``), and
the people the text mentions (``p``) and the notes and articles it
quotes (``q``), both made again from the text, so a quote taken out of
the text is no longer tagged and none is tagged twice.

An article draft holds them in its inner event. Saving the draft again,
publishing it, and the Article Details inspector all read and write them
through this one model, so nothing is dropped on the way: saving an
imported draft used to keep only its identifier, title and summary, and
the next publication then had no cover, no hashtags and a new date.

A Markdown file keeps them in its front matter, under the keys Jekyll,
Hugo, Obsidian and iA Writer use (``title``, ``summary`` or
``description``, ``image``, ``tags``, ``slug``, ``date``), so they are
the document's wherever it goes, and are never published as its text.
Co-authors are ``coauthors`` (their Nostr addresses) and zap shares
``zap_split`` ("<address> <weight>" each). Entries of the front matter
this app does not know stay as written.

The expiry of an imported draft is not a detail of the article; it is
on the draft's wrap, and a draft the person saved has none.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Sequence, Tuple

import front_matter

from . import refs
from .bech32 import encode_nprofile, encode_npub

# Tags MyEditor writes itself; every other tag is carried as it is.
DETAILS = ("d", "title", "summary", "image", "published_at", "t")
REBUILT = ("client", "p", "q")
# A co-author: ["p", <pubkey>, <relay hint>, "author"] (the marker standup
# writes and reads back for "this person wrote it").
AUTHOR_MARKER = "author"


def hashtags_from(text: str) -> Tuple[str, ...]:
    """The hashtags written in ``text``, separated by commas: lowercase,
    without "#", each once, in the order written."""
    found: List[str] = []
    for raw in (text or "").split(","):
        tag = raw.strip().lstrip("#").strip().lower()
        if tag and tag not in found:
            found.append(tag)
    return tuple(found)


@dataclass(frozen=True)
class ArticleDetails:
    """The details of one article (see the module docstring)."""

    identifier: str = ""
    title: str = ""
    summary: str = ""
    image: str = ""
    hashtags: Tuple[str, ...] = ()
    published_at: Optional[int] = None
    # Who wrote it with the author: (pubkey, relay hint) each.
    coauthors: Tuple[Tuple[str, str], ...] = ()
    # How zaps to it are shared (NIP-57 zap tags, as written).
    zaps: Tuple[Tuple[str, ...], ...] = ()
    carried: Tuple[Tuple[str, ...], ...] = field(default=())

    @classmethod
    def from_tags(cls, tags: Iterable[Sequence[str]]) -> "ArticleDetails":
        """The details an event's tags hold (the first of each kind wins;
        every hashtag counts)."""
        found = {}
        hashtags: List[str] = []
        coauthors: List[Tuple[str, str]] = []
        zaps: List[Tuple[str, ...]] = []
        carried: List[Tuple[str, ...]] = []
        for tag in tags or ():
            if not isinstance(tag, (list, tuple)) or len(tag) < 2 \
                    or not all(isinstance(part, str) for part in tag):
                continue
            name, value = tag[0], tag[1]
            if name == "t":
                if value.strip() and value.strip().lower() not in hashtags:
                    hashtags.append(value.strip().lower())
            elif name in DETAILS:
                found.setdefault(name, value)
            elif name == "p" and len(tag) >= 4 and tag[3] == AUTHOR_MARKER:
                if value not in (pubkey for pubkey, _hint in coauthors):
                    coauthors.append((value, tag[2]))
            elif name == "zap":
                zaps.append(tuple(tag))
            elif name not in REBUILT:
                carried.append(tuple(tag))
        published_at = None
        try:
            published_at = int(found.get("published_at", "").strip()) or None
        except ValueError:
            pass
        return cls(identifier=found.get("d", ""), title=found.get("title", ""),
                   summary=found.get("summary", ""), image=found.get("image", ""),
                   hashtags=tuple(hashtags), published_at=published_at,
                   coauthors=tuple(coauthors), zaps=tuple(zaps), carried=tuple(carried))

    def with_changes(self, **changes) -> "ArticleDetails":
        return replace(self, **changes)

    def carried_tags(self, content: str) -> List[List[str]]:
        """The carried tags, as lists: picture descriptions only for the
        pictures still in ``content``."""
        kept = []
        for tag in self.carried:
            if tag[0] == "imeta":
                url = next((part[4:] for part in tag[1:] if part.startswith("url ")), "")
                if not url or url not in content:
                    continue
            kept.append(list(tag))
        return kept

    def draft_tags(self, content: str) -> List[List[str]]:
        """The tags of an article draft's inner event with ``content``."""
        return self._detail_tags() + self.carried_tags(content)

    def published_tags(self, content: str) -> List[List[str]]:
        """The tags an article with ``content`` is published with besides
        those its publisher makes of the details it is given (title,
        summary, cover, hashtags, first date): co-authors, zap shares and
        the carried tags."""
        return self._people_tags() + self.carried_tags(content)

    def tags(self) -> List[List[str]]:
        """Every detail as a tag, the carried ones all as they are:
        ``from_tags`` makes the same details of them again (for keeping
        them somewhere, such as a tab written down for a restart)."""
        return self._detail_tags() + [list(tag) for tag in self.carried]

    def _detail_tags(self) -> List[List[str]]:
        tags: List[List[str]] = [["d", self.identifier]]
        if self.title.strip():
            tags.append(["title", self.title.strip()])
        if self.summary.strip():
            tags.append(["summary", self.summary.strip()])
        if self.image.strip():
            tags.append(["image", self.image.strip()])
        if self.published_at:
            tags.append(["published_at", str(int(self.published_at))])
        tags.extend(["t", tag] for tag in self.hashtags)
        return tags + self._people_tags()

    def _people_tags(self) -> List[List[str]]:
        return ([["p", pubkey, hint, AUTHOR_MARKER] for pubkey, hint in self.coauthors]
                + [list(tag) for tag in self.zaps])


def equal_zaps(author: str, author_relay: str,
               coauthors: Sequence[Tuple[str, str]]) -> Tuple[Tuple[str, ...], ...]:
    """Zaps shared equally between the author and ``coauthors``: a zap tag
    of four parts each (pubkey, relay hint, weight), as every reader of
    zap splits reads them. None without co-authors."""
    if not coauthors:
        return ()
    people = [(author, author_relay)] + [person for person in coauthors
                                          if person[0] != author]
    return tuple(("zap", pubkey, hint or "", "1") for pubkey, hint in people)


# -- a Markdown file's front matter -----------------------------------------------

# Each detail's key, then the other keys it is read from (other apps' names).
_FRONT_KEYS = (("title", ()), ("summary", ("description",)),
               ("image", ("cover", "cover_image")), ("tags", ("hashtags",)),
               ("slug", ()), ("date", ("published_at",)), ("coauthors", ()),
               ("zap_split", ()))


def from_front_matter(entries: Optional[Sequence[front_matter.Entry]]) -> ArticleDetails:
    """The details a file's front matter holds (empty for none)."""
    values = {}
    for entry in entries or ():
        for key, others in _FRONT_KEYS:
            if (entry.key == key or entry.key in others) and key not in values:
                values[key] = entry.value
    def text(key: str) -> str:
        value = values.get(key, "")
        return value if isinstance(value, str) else ""
    def items(key: str) -> List[str]:
        value = values.get(key, [])
        return value if isinstance(value, list) else [part for part in value.split(",")]
    hashtags = hashtags_from(", ".join(items("tags")))
    coauthors = []
    for written in items("coauthors"):
        person = _person(written)
        if person is not None and person[0] not in (p for p, _hint in coauthors):
            coauthors.append(person)
    zaps = []
    for written in items("zap_split"):
        address, _space, weight = written.strip().partition(" ")
        person = _person(address)
        if person is not None and weight.strip().isdigit():
            zaps.append(("zap", person[0], person[1], weight.strip()))
    return ArticleDetails(identifier=text("slug"), title=text("title"),
                          summary=text("summary"), image=text("image"),
                          hashtags=hashtags, published_at=_moment(text("date")),
                          coauthors=tuple(coauthors), zaps=tuple(zaps))


def front_matter_with(details: ArticleDetails,
                      entries: Optional[Sequence[front_matter.Entry]]) -> List[front_matter.Entry]:
    """``entries`` (a file's front matter, or none) with ``details`` written
    into it: each detail where it was, the others after the entries in the
    order of _FRONT_KEYS; empty details left out; every entry this app
    does not know as it was."""
    written = {"title": details.title.strip(), "summary": details.summary.strip(),
               "image": details.image.strip(), "tags": list(details.hashtags),
               "slug": details.identifier.strip(),
               "date": _iso(details.published_at) if details.published_at else "",
               "coauthors": [_address(pubkey, hint) for pubkey, hint in details.coauthors],
               "zap_split": [f"{_address(tag[1], tag[2] if len(tag) > 2 else '')} "
                             f"{tag[3] if len(tag) > 3 else '1'}" for tag in details.zaps]}
    known = {name: key for key, others in _FRONT_KEYS for name in (key, *others)}
    done = set()
    out: List[front_matter.Entry] = []
    for entry in entries or ():
        key = known.get(entry.key)
        if key is None:
            out.append(entry)
        elif key not in done:
            done.add(key)
            if written[key]:
                out.append(front_matter.entry(entry.key, written[key]))
    for key, _others in _FRONT_KEYS:
        if key not in done and written[key]:
            out.append(front_matter.entry(key, written[key]))
    return out


def _person(written: str) -> Optional[Tuple[str, str]]:
    """(pubkey, relay hint) of a person's Nostr address, or None."""
    reference = refs.read_entity(written.strip().removeprefix("nostr:"))
    if reference is None or reference.kind != refs.PERSON:
        return None
    return reference.pubkey, reference.relay


def _address(pubkey: str, hint: str) -> str:
    """A person's Nostr address, with their relay when one is known."""
    return encode_nprofile(pubkey, [hint]) if hint else encode_npub(pubkey)


def _iso(when: int) -> str:
    return datetime.fromtimestamp(int(when), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _moment(written: str) -> Optional[int]:
    """A front matter date as a Unix time: seconds, or an ISO 8601 date
    or date and time (UTC when it names no offset); None otherwise."""
    written = (written or "").strip()
    if not written:
        return None
    if written.isdigit():
        return int(written) or None
    try:
        moment = datetime.fromisoformat(written.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())
