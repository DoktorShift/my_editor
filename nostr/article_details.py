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

import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import FrozenSet, Iterable, List, Optional, Sequence, Tuple

import front_matter

from . import refs
from .bech32 import encode_nprofile, encode_npub

# Tags MyEditor writes itself; every other tag is carried as it is.
DETAILS = ("d", "title", "summary", "image", "published_at", "t")
REBUILT = ("client", "p", "q")
# An author: ["p", <pubkey>, <relay hint>, "author"] (NIP-84's role, the
# marker standup writes and reads back for "this person wrote it"). When an
# article names co-authors, its signer is written first, as standup takes
# the first one for the writer; the co-authors follow.
AUTHOR_MARKER = "author"


def hashtags_from(text: str) -> Tuple[str, ...]:
    """The hashtags written in ``text``, separated by commas (one holding a
    comma in double quotes, as hashtags_text writes it): without "#", each
    once whatever its case, in the order and the case written (an article's
    ``t`` tags are lowercase, as they are published)."""
    return _hashtags(front_matter.split_items(text or ""))


def hashtags_text(hashtags: Sequence[str]) -> str:
    """``hashtags`` as they are typed in a field: separated by commas, one
    holding a comma or a double quote in double quotes, so it stays whole."""
    return ", ".join(json.dumps(tag, ensure_ascii=False) if any(c in tag for c in ',"')
                     else tag for tag in hashtags)


def _hashtags(items: Iterable[str]) -> Tuple[str, ...]:
    found: List[str] = []
    for raw in items:
        tag = raw.strip().lstrip("#").strip()
        if tag and tag.lower() not in (seen.lower() for seen in found):
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
    def from_tags(cls, tags: Iterable[Sequence[str]], signer: str = "") -> "ArticleDetails":
        """The details an event's tags hold (the first of each kind wins;
        every hashtag counts). ``signer``, the event's author, is no
        co-author of their own article."""
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
                if value != signer and value not in (pubkey for pubkey, _hint in coauthors):
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

    def draft_tags(self, content: str, author: Tuple[str, str] = ("", "")) -> List[List[str]]:
        """The tags of an article draft's inner event with ``content``, by
        ``author`` (pubkey, relay hint)."""
        return (self._detail_tags(with_people=False) + self._people_tags(author)
                + self.carried_tags(content))

    def published_tags(self, content: str,
                       author: Tuple[str, str] = ("", "")) -> List[List[str]]:
        """The tags an article with ``content`` is published with by
        ``author`` (pubkey, relay hint) besides those its publisher makes of
        the details it is given (title, summary, cover, hashtags, first
        date): its authors, zap shares and the carried tags."""
        return self._people_tags(author) + self.carried_tags(content)

    def tags(self) -> List[List[str]]:
        """Every detail as a tag, the carried ones all as they are:
        ``from_tags`` makes the same details of them again (for keeping
        them somewhere, such as a tab written down for a restart)."""
        return self._detail_tags() + [list(tag) for tag in self.carried]

    def _detail_tags(self, with_people: bool = True) -> List[List[str]]:
        tags: List[List[str]] = [["d", self.identifier]]
        if self.title.strip():
            tags.append(["title", self.title.strip()])
        if self.summary.strip():
            tags.append(["summary", self.summary.strip()])
        if self.image.strip():
            tags.append(["image", self.image.strip()])
        if self.published_at:
            tags.append(["published_at", str(int(self.published_at))])
        tags.extend(["t", tag.lower()] for tag in self.hashtags)
        return tags + (self._people_tags() if with_people else [])

    def _people_tags(self, author: Tuple[str, str] = ("", "")) -> List[List[str]]:
        """The authors, the signer first when co-authors are named and the
        signer is known, then the co-authors; then the zap shares."""
        pubkey, hint = author
        writers = [(pk, relay) for pk, relay in self.coauthors if pk != pubkey]
        if pubkey and writers:
            writers.insert(0, (pubkey, hint))
        return ([["p", pk, relay, AUTHOR_MARKER] for pk, relay in writers]
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


def from_front_matter(front: Optional[front_matter.FrontMatter]) -> ArticleDetails:
    """The details a file's front matter holds (empty for none). Each is
    read from the first entry under its key or another app's name for it
    that can be read fully; one that can't (a mapping) is left alone."""
    values = {}
    for key, _others in _FRONT_KEYS:
        found = _found(front, key)
        if found is not None:
            values[key] = found[2].value
    def text(key: str) -> str:
        value = values.get(key, "")
        return value.strip() if isinstance(value, str) else ""
    def items(key: str) -> List[str]:
        value = values.get(key, [])
        return value if isinstance(value, list) else [part for part in value.split(",")]
    tags = values.get("tags", [])
    hashtags = _hashtags(tags) if isinstance(tags, list) else _string_tags(tags)
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


def _string_tags(written: str) -> Tuple[str, ...]:
    """The hashtags a string of them holds: separated by commas, else by
    spaces, as Jekyll reads "tags: ruby jekyll"."""
    return hashtags_from(written) if "," in written else _hashtags(written.split())


def kept_in_file(front: Optional[front_matter.FrontMatter]) -> FrozenSet[str]:
    """The details ``front`` writes in a way this app can't read safely (a
    mapping, an anchor, a tag), with no entry for them it can: they stay as
    written, whatever is set here. Named as in _FRONT_KEYS."""
    kept = set()
    for key, _others in _FRONT_KEYS:
        found = [entry for _at, _inner, entry in _candidates(front, key)]
        if found and not any(_readable(key, entry) for entry in found):
            kept.add(key)
    return frozenset(kept)


def _candidates(front: Optional[front_matter.FrontMatter], key: str):
    """``(index, inner index or None, entry)`` of each entry under the
    detail ``key``'s names, whatever their case (Hugo's "Title"); for the
    hashtags in TOML also those in Zola's [taxonomies] table."""
    names = {name.lower() for name in (key, *dict(_FRONT_KEYS)[key])}
    entries = front.entries if front is not None else ()
    for at, entry in enumerate(entries):
        if entry.key.lower() in names:
            yield at, None, entry
    if key == "tags":
        for at, entry in enumerate(entries):
            if entry.table.lower() == "taxonomies":
                for inner, sub in enumerate(entry.inner_entries()):
                    if sub.key.lower() in names:
                        yield at, inner, sub


def _readable(key: str, entry: front_matter.Entry) -> bool:
    """Whether ``entry`` holds the detail ``key`` in a way read safely: a
    date only when it is one (Eleventy's "date: Last Modified" is not)."""
    value = entry.value
    if value is None:
        return False
    if key == "date":
        return isinstance(value, str) and (not value.strip() or _moment(value) is not None)
    return True


def _found(front, key: str):
    """The first readable entry of the detail ``key`` (_candidates), or None."""
    return next(((at, inner, entry) for at, inner, entry in _candidates(front, key)
                 if _readable(key, entry)), None)


def front_matter_with(details: ArticleDetails, front: Optional[front_matter.FrontMatter]
                      ) -> Optional[front_matter.FrontMatter]:
    """``front`` (a file's front matter, or None) with what changed of the
    details written into it, and nothing else: an unchanged detail keeps its
    entry as written, byte for byte; a changed one replaces its entry where
    it was (a date in the form and time zone it had), or follows the other
    entries in the order of _FRONT_KEYS; one emptied is left out. Entries
    this app does not know, or can't read fully, stay as written, and so
    does a detail only such an entry holds (kept_in_file): never a second
    entry beside it. None when there are no details and there was no front
    matter."""
    was = from_front_matter(front)
    entries = list(front.entries) if front is not None else []
    old = _front_values(was)
    new = _front_values(details)
    kept = kept_in_file(front)
    changed = [key for key, _others in _FRONT_KEYS
               if new[key] != old[key] and key not in kept]
    if not changed:
        return front
    for key in changed:
        found = _found(front_matter.FrontMatter(tuple(entries), syntax=front.syntax)
                       if front is not None else None, key)
        at, inner, current = found if found is not None else (None, None, None)
        if not new[key]:
            if inner is not None:
                rest = entries[at].inner_entries()
                del rest[inner]
                entries[at] = entries[at].with_inner(rest)
            elif at is not None:
                del entries[at]
            continue
        value = new[key]
        syntax = front.syntax if front is not None else front_matter.YAML
        bare = False
        if key == "date":
            original = current.written if current is not None else ""
            value = _date_like(details.published_at, original.strip("\"'"))
            # A date YAML and TOML read as one (a timestamp), not a text;
            # in quotes only when the file had it so.
            bare = not original.startswith(("\"", "'"))
        if key == "tags" and current is not None and isinstance(current.value, str) \
                and len(_string_tags(current.value)) > 1:
            # Written as a string of tags ("ruby jekyll", "a, b"): a string
            # again, in its way, while it can hold each tag whole. A string
            # of one tag, or none, is no such way: Hugo and Eleventy read
            # "idea draft" as one tag, so it becomes a list.
            separator = ", " if "," in current.value else " "
            mark = separator.strip() or " "
            if not any(mark in tag for tag in value):
                value = separator.join(value)
        elif key == "tags" and current is not None and isinstance(current.value, list):
            # A hashtag the file has keeps its spelling there ("#idea").
            spelled = {tag.lower(): item for item in current.value
                       for tag in _hashtags([item])}
            value = [spelled.get(tag.lower(), tag) for tag in value]
        written = (front_matter.rewritten(current, value, bare=bare) if current is not None
                   else front_matter.entry(key, value, syntax=syntax, bare=bare))
        if inner is not None:
            rest = entries[at].inner_entries()
            rest[inner] = written
            entries[at] = entries[at].with_inner(rest)
        elif at is not None:
            entries[at] = written
        elif front is not None:
            entries.insert(front.where_new_entries_go(entries), written)
        else:
            entries.append(written)
    if front is None:
        return front_matter.FrontMatter(tuple(entries), changed=True)
    return front.with_entries(entries)


def _front_values(details: ArticleDetails) -> dict:
    """The details as the values compared and written in front matter."""
    return {"title": details.title.strip(), "summary": details.summary.strip(),
            "image": details.image.strip(), "tags": list(details.hashtags),
            "slug": details.identifier.strip(),
            "date": details.published_at or 0,
            "coauthors": [_address(pubkey, hint) for pubkey, hint in details.coauthors],
            "zap_split": [f"{_address(tag[1], tag[2] if len(tag) > 2 else '')} "
                          f"{tag[3] if len(tag) > 3 else '1'}" for tag in details.zaps]}


def _date_like(when: int, original: str) -> str:
    """``when`` written the way ``original`` (the date the file had) is: a
    Unix time, a date, or a date and time in its time zone; an ISO 8601
    time in UTC when the file had none."""
    moment = datetime.fromtimestamp(int(when), tz=timezone.utc)
    original = original.strip()
    if original.isdigit():
        return str(int(when))
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", original):
        return moment.strftime("%Y-%m-%d")
    parsed = _parsed(original)
    if parsed is None:
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    separator = original[10] if len(original) > 10 and original[10] in "Tt " else "T"
    if parsed.tzinfo is None or original.endswith(("Z", "z")):
        return moment.strftime(f"%Y-%m-%d{separator}%H:%M:%S") + (
            "Z" if original.endswith(("Z", "z")) else "")
    local = moment.astimezone(parsed.tzinfo)
    offset = re.search(r"(\s?)([+-])(\d{2})(:?)(\d{2})?$", original)
    if offset is None:
        return local.isoformat(sep=separator)
    # The offset as the file wrote it: "+01:00", "+0100", " +0100".
    space, _sign, _hours, colon, minutes = offset.groups()
    zone = local.strftime("%z")                       # "+0100"
    zone = zone[:3] + (colon + zone[3:] if minutes is not None else "")
    return local.strftime(f"%Y-%m-%d{separator}%H:%M:%S") + space + zone


def _person(written: str) -> Optional[Tuple[str, str]]:
    """(pubkey, relay hint) of a person's Nostr address, or None."""
    reference = refs.read_entity(written.strip().removeprefix("nostr:"))
    if reference is None or reference.kind != refs.PERSON:
        return None
    return reference.pubkey, reference.relay


def _address(pubkey: str, hint: str) -> str:
    """A person's Nostr address, with their relay when one is known."""
    return encode_nprofile(pubkey, [hint]) if hint else encode_npub(pubkey)


def _parsed(written: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(written.strip().replace("Z", "+00:00")
                                      .replace("z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None


def _moment(written: str) -> Optional[int]:
    """A front matter date as a Unix time: seconds, or an ISO 8601 date
    or date and time (UTC when it names no offset); None otherwise."""
    written = (written or "").strip()
    if not written:
        return None
    if written.isdigit():
        return int(written) or None
    moment = _parsed(written)
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())
