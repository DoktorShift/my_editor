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
this app does not know stay as written, and so does a detail a YAML or
TOML parser reads otherwise than this app's reader (front_matter.parsed).

The expiry of an imported draft is not a detail of the article; it is
on the draft's wrap, and a draft the person saved has none.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from typing import AbstractSet, Any, FrozenSet, Iterable, List, Optional, Sequence, Tuple, Union

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
    comma in double quotes, as hashtags_text writes it; an apostrophe is a
    letter, "don't panic"): without "#", each
    once whatever its case, in the order and the case written (an article's
    ``t`` tags are lowercase, as they are published)."""
    return _hashtags(front_matter.split_items(text or "", quotes='"'))


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
    that this reader reads as a parser does; else as the parser reads it
    (_held), kept as written then (kept_in_file), but never read as empty
    while the file holds it."""
    values = {}
    for key, _others in _FRONT_KEYS:
        found = _found(front, key)
        values[key] = _detail(key, found[2].value if found is not None else _held(front, key))
    coauthors = []
    for written in values["coauthors"]:
        person = _person(written)
        if person is not None and person[0] not in (p for p, _hint in coauthors):
            coauthors.append(person)
    zaps = []
    for written in values["zap_split"]:
        address, _space, weight = written.strip().partition(" ")
        person = _person(address)
        if person is not None and weight.strip().isdigit():
            zaps.append(("zap", person[0], person[1], weight.strip()))
    return ArticleDetails(identifier=values["slug"], title=values["title"],
                          summary=values["summary"], image=values["image"],
                          hashtags=values["tags"], published_at=values["date"],
                          coauthors=tuple(coauthors), zaps=tuple(zaps))


def _detail(key: str, value: Union[str, List[str], None]) -> Any:
    """What the detail ``key`` holds when its entry's value is ``value`` (a
    text, a list of texts; None for no entry): its hashtags, its moment,
    its items, or its text."""
    if key == "tags":
        return _hashtags(value) if isinstance(value, list) else _string_tags(value or "")
    if key in ("coauthors", "zap_split"):
        return value if isinstance(value, list) else (value or "").split(",")
    text = value.strip() if isinstance(value, str) else ""
    return _moment(text) if key == "date" else text


def _string_tags(written: str) -> Tuple[str, ...]:
    """The hashtags a string of them holds: separated by commas, else by
    spaces, as Jekyll reads "tags: ruby jekyll"."""
    return hashtags_from(written) if "," in written else _hashtags(written.split())


def kept_in_file(front: Optional[front_matter.FrontMatter]) -> FrozenSet[str]:
    """The details ``front`` writes in a way this app can't read safely (a
    mapping, an anchor, a tag), with no entry for them it can, or can't
    change safely (a change tried, _trials, or one made before, a parser
    would read otherwise): they stay as written, whatever is set here.
    Named as in _FRONT_KEYS."""
    mapping = front_matter.parsed(front)
    if mapping is None:
        return frozenset(dict(_FRONT_KEYS))  # no parser reads it: nothing is changed
    kept = set(front.kept if front is not None else ())
    for key, _others in _FRONT_KEYS:
        found = list(_candidates(front, key))
        if found and not any(_readable(key, entry, read) for _at, _inner, entry, read in found) \
                or _unseen(key, found, mapping):
            kept.add(key)
    details = from_front_matter(front)
    for key, _others in _FRONT_KEYS:
        if key not in kept and any(key in _refused(details.with_changes(**trial), front, kept)
                                   for trial in _trials(key, details)):
            kept.add(key)
    return frozenset(kept)


def as_in_file(details: ArticleDetails, front: Optional[front_matter.FrontMatter],
               names: AbstractSet[str]) -> ArticleDetails:
    """``details`` with the details ``names`` (as in _FRONT_KEYS) as
    ``front`` holds them: what a change refused there leaves."""
    held = from_front_matter(front)
    return details.with_changes(**{_FIELDS[name]: getattr(held, _FIELDS[name])
                                   for name in names})


def _refused(details: ArticleDetails, front: Optional[front_matter.FrontMatter],
             kept: AbstractSet[str]) -> FrozenSet[str]:
    """The details a change to ``details`` would be refused for in ``front``."""
    written = _written(details, front, frozenset(kept))
    return written.kept if written is not None else frozenset()


# The field of ArticleDetails each detail is.
_FIELDS = {"title": "title", "summary": "summary", "image": "image", "tags": "hashtags",
           "slug": "identifier", "date": "published_at", "coauthors": "coauthors",
           "zap_split": "zaps"}
# Someone a co-author or a zap share is tried with (_trials): the public
# key of secp256k1's generator, a key no one writes as theirs.
_SOMEONE = "79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798"


def _trials(key: str, details: ArticleDetails) -> List[dict]:
    """The changes a detail is tried with before it can be changed
    (kept_in_file): to a value YAML needs quoted, of several lines for the
    summary, or one more item; and to none."""
    if key == "tags":
        values = (details.hashtags + ('trial, "#2"',), ())
    elif key == "date":
        values = ((details.published_at or 0) + 86400, None)
    elif key == "coauthors":
        values = (details.coauthors + ((_SOMEONE, ""),), ())
    elif key == "zap_split":
        values = (details.zaps + (("zap", _SOMEONE, "", "1"),), ())
    elif key == "summary":
        values = ('Trial: "1" #2\nand a second line', "")
    else:
        values = ('Trial: "1" #2', "")
    return [{_FIELDS[key]: value} for value in values]


# What a parser reads for an entry it does not find, and for every entry
# of a front matter it can't read at all (_candidates).
_ABSENT = object()
_UNREAD = object()


def _candidates(front: Optional[front_matter.FrontMatter], key: str):
    """``(index, inner index or None, entry, read)`` of each entry under
    the detail ``key``'s names, whatever their case (Hugo's "Title"); for
    the hashtags in TOML also those in Zola's [taxonomies] table. ``read``:
    what a YAML or TOML parser reads for it (front_matter.parsed), _ABSENT,
    or _UNREAD when no parser reads the front matter."""
    names = _names(key)
    entries = front.entries if front is not None else ()
    mapping = front_matter.parsed(front)

    def read(table: Any, name: str) -> Any:
        if mapping is None:
            return _UNREAD                  # no parser reads it: only this reader does
        return table.get(name, _ABSENT) if isinstance(table, dict) else _ABSENT
    for at, entry in enumerate(entries):
        if entry.key.lower() in names:
            yield at, None, entry, read(mapping, entry.key)
    if key == "tags":
        for at, entry in enumerate(entries):
            if entry.table.lower() == "taxonomies":
                table = mapping.get(entry.table) if mapping is not None else None
                for inner, sub in enumerate(entry.inner_entries()):
                    if sub.key.lower() in names:
                        yield at, inner, sub, read(table, sub.key)


def _names(key: str) -> set:
    """The names the detail ``key`` is written under, in lower case."""
    return {name.lower() for name in (key, *dict(_FRONT_KEYS)[key])}


def _unseen(key: str, found, mapping: dict) -> bool:
    """Whether a parser reads the detail ``key`` under a name this reader
    finds no entry for (a key written "? tags", "tags.x", twice in Zola's
    taxonomies): the detail then stays as written."""
    names = _names(key)
    def under(table) -> set:
        return {name for name in table if isinstance(name, str) and name.lower() in names} \
            if isinstance(table, dict) else set()
    seen = {entry.key for _at, inner, entry, _read in found if inner is None}
    seen_inner = {entry.key for _at, inner, entry, _read in found if inner is not None}
    taxonomies = [table for name, table in mapping.items()
                  if key == "tags" and isinstance(name, str) and name.lower() == "taxonomies"]
    return bool(under(mapping) - seen) or any(under(table) - seen_inner for table in taxonomies)


def _readable(key: str, entry: front_matter.Entry, read: Any) -> bool:
    """Whether ``entry`` holds the detail ``key`` in a way read safely: as
    a parser reads it (``read``), and a date only when it is one
    (Eleventy's "date: Last Modified" is not)."""
    value = entry.value
    if value is None or read is _ABSENT or read is not _UNREAD \
            and _detail(key, value) != _detail(key, _as_value(read, value, entry.syntax)):
        return False
    if key == "date":
        return isinstance(value, str) and (not value.strip() or _moment(value) is not None)
    return True


def _as_value(read: Any, written: Union[str, List[str], None] = None,
              syntax: str = front_matter.YAML) -> Union[str, List[str], None]:
    """What a parser read for an entry as this app's reader gives a value
    (front_matter.Entry.value): a text, or a list of texts. A number, true
    or a date is the text ``written`` for it ("1.10" for 1.1, "yes" for
    true) when that is what the parser reads there, else as YAML writes
    it; a list or a mapping inside a list is left out. None for a mapping."""
    if isinstance(read, list):
        items = written if isinstance(written, list) and len(written) == len(read) \
            else [None] * len(read)
        return [_as_value(item, text, syntax) for item, text in zip(read, items)
                if not isinstance(item, (list, dict))]
    if isinstance(read, dict):
        return None
    if isinstance(read, str):
        return read
    if isinstance(written, str) and front_matter.reads_as(written, read, syntax):
        return written
    if read is None:
        return ""
    if isinstance(read, bool):
        return "true" if read else "false"
    if isinstance(read, (date, datetime)):
        return read.isoformat()
    return str(read)


def _held(front: Optional[front_matter.FrontMatter], key: str) -> Union[str, List[str], None]:
    """The detail ``key`` as a parser reads it where no entry of it reads
    so with this reader (an anchor, an escape it does not know, a key it
    does not see): the first place the parser holds it, as text; for the
    cover a mapping's own image ("cover:" with "image:" under it). None
    where it holds no text there, or nothing."""
    mapping = front_matter.parsed(front)
    if mapping is None:
        return None
    syntax = front.syntax if front is not None else front_matter.YAML
    places = [(read, entry.value) for _at, _inner, entry, read in _candidates(front, key)
              if read is not _ABSENT]
    names = _names(key)
    tables = [mapping] + [table for name, table in mapping.items() if key == "tags"
                          and isinstance(name, str) and name.lower() == "taxonomies"]
    places += [(read, None) for table in tables if isinstance(table, dict)
               for name, read in table.items() if isinstance(name, str) and name.lower() in names]
    for read, written in places:
        if key == "image" and isinstance(read, dict):
            read = next((read[name] for name in ("image", "src", "path", "url")
                         if isinstance(read.get(name), str)), None)
        value = _as_value(read, written, syntax)
        if value is not None:
            return value
    return None


def _found(front, key: str):
    """The first readable entry of the detail ``key`` (_candidates), or None."""
    return next(((at, inner, entry) for at, inner, entry, read in _candidates(front, key)
                 if _readable(key, entry, read)), None)


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
    matter.

    Each change is made only when a parser reads the front matter after it
    as before, but for that detail, which it reads as set; else the detail
    stays as written and is kept from then on (FrontMatter.kept)."""
    return _written(details, front, kept_in_file(front))


def _written(details: ArticleDetails, front: Optional[front_matter.FrontMatter],
             kept: FrozenSet[str]) -> Optional[front_matter.FrontMatter]:
    """front_matter_with, the details ``kept`` left as written."""
    old = _front_values(from_front_matter(front))
    new = _front_values(details)
    changed = [key for key, _others in _FRONT_KEYS
               if new[key] != old[key] and key not in kept]
    if not changed:
        return front
    syntax = front.syntax if front is not None else front_matter.YAML
    entries = list(front.entries) if front is not None else []
    refused = set()
    for key in changed:
        after, place, meant = _with_detail(entries, syntax, key, new[key], details)
        if _holds(front_matter.FrontMatter(tuple(entries), syntax=syntax),
                  front_matter.FrontMatter(tuple(after), syntax=syntax), key, place, meant):
            entries = after
        else:
            refused.add(key)
    if front is None:
        return front_matter.FrontMatter(tuple(entries), changed=True, kept=frozenset(refused))
    return front.with_entries(entries).with_kept(refused)


def _with_detail(entries: List[front_matter.Entry], syntax: str, key: str, value,
                 details: ArticleDetails):
    """``(entries, place, meant)``: ``entries`` with the detail ``key`` set
    to ``value`` (as _front_values gives it; emptied, left out), the key it
    is at (a table's name and its key for one in a table), and the detail
    as the parser should then read it there (_detail; None when left out)."""
    entries = list(entries)
    found = _found(front_matter.FrontMatter(tuple(entries), syntax=syntax), key)
    at, inner, current = found if found is not None else (None, None, None)
    place = (key,) if current is None else (current.key,) if inner is None \
        else (entries[at].table, current.key)
    if not value:
        if inner is not None:
            rest = entries[at].inner_entries()
            del rest[inner]
            entries[at] = entries[at].with_inner(rest)
        elif at is not None:
            del entries[at]
        return entries, place, None
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
    else:
        entries.insert(front_matter.FrontMatter(tuple(entries), syntax=syntax)
                       .where_new_entries_go(entries), written)
    return entries, place, _detail(key, value)


def _holds(before: front_matter.FrontMatter, after: front_matter.FrontMatter, key: str,
           place: Tuple[str, ...], meant: Any) -> bool:
    """Whether a parser reads ``after`` as ``before`` but at ``place``,
    where it reads the detail ``key`` as ``meant`` (nothing for None), and
    reads it so with this app's reader."""
    old, new = front_matter.parsed(before), front_matter.parsed(after)
    if old is None or new is None or _without(old, place) != _without(new, place):
        return False
    if meant is None:
        return _at(new, place) is _ABSENT
    found = _found(after, key)
    return found is not None and _detail(key, found[2].value) == meant


def _at(mapping: dict, place: Tuple[str, ...]) -> Any:
    """What ``mapping`` holds at ``place`` (a key, or a table and its key)."""
    for name in place:
        if not isinstance(mapping, dict) or name not in mapping:
            return _ABSENT
        mapping = mapping[name]
    return mapping


def _without(mapping: dict, place: Tuple[str, ...]) -> dict:
    """``mapping`` without what it holds at ``place``, unchanged itself."""
    mapping = dict(mapping)
    if len(place) == 1:
        mapping.pop(place[0], None)
    elif isinstance(mapping.get(place[0]), dict):
        mapping[place[0]] = _without(mapping[place[0]], place[1:])
    return mapping


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
