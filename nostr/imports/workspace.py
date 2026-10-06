# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Imports window shows, as plain data.

The window lists posts from two places, and this module gives them one
shape, :class:`Post` (EINUNDZWANZIG STANDUP's imports-workspace store
does the same):

- posts a check of a source found, which the inbox keeps
  (inbox_store.py): the Inbox, Older Posts, Imported, Skipped, and a
  source's own new and older posts;
- posts held only while they are shown (a :class:`Collection`): a source
  that is read when it is opened, such as a Nostr author or a sitemap,
  or a file or a link opened to import once.

It also holds the rules the window's parts share: which list is shown
(:class:`Scope`), which posts an action applies to (Mail's rule: the
checked posts, else the open one), which posts can be chosen at all, the
one word that says what became of a post, and how a post's date reads.

Pure Python apart from QLocale for dates; no widgets.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from PySide6.QtCore import QDate, QLocale

import i18n
from i18n import N_, _, ngettext

from ..rss.parser import FeedItem
from . import snapshots
from .inbox_store import IMPORTED_STATES, NEW, OLDER, SKIPPED, PostRow, View

INBOX_ORIGIN = "inbox"
MEMORY_ORIGIN = "memory"

# How recent a date still reads as "2 h ago" (STANDUP's importDates.js).
RELATIVE_SECONDS = 6 * 24 * 3600

# The one word that says what became of a post. Nothing else is shown:
# no queue, no stage names.
STATE_NEW = N_("New")
STATE_IMPORTING = N_("Importing")
STATE_IMPORTED = N_("Imported")
STATE_SKIPPED = N_("Skipped")
STATE_FAILED = N_("Failed")


@dataclass(frozen=True)
class Post:
    """One post in a list."""

    key: str                    # unique in the window
    origin: str                 # INBOX_ORIGIN | MEMORY_ORIGIN
    source_key: str
    source_title: str
    source_url: str
    d_tag: str
    title: str
    excerpt: str
    image: str
    link: str
    author: str
    published_at: int
    found_at: int
    read_minutes: int
    image_count: int
    state: str = ""             # "" for a post nothing was done with yet
    revision: int = 0
    collection: str = ""        # the Collection a held post belongs to

    @property
    def imported(self) -> bool:
        return self.state in IMPORTED_STATES

    @property
    def skippable(self) -> bool:
        """Only kept posts that are not imported can be set aside."""
        return self.origin == INBOX_ORIGIN and self.state in (NEW, OLDER)

    @property
    def skipped(self) -> bool:
        return self.state == SKIPPED


def post_from_row(row: PostRow) -> Post:
    return Post(key=f"c:{row.source_key}:{row.d_tag}", origin=INBOX_ORIGIN,
                source_key=row.source_key, source_title=row.source_title,
                source_url=row.source_url, d_tag=row.d_tag, title=row.title,
                excerpt=row.excerpt, image=row.image, link=row.link, author=row.author,
                published_at=row.published_at, found_at=row.found_at,
                read_minutes=row.read_minutes, image_count=row.image_count,
                state=row.state, revision=row.revision)


def post_from_item(item: FeedItem, *, collection: str, source_key: str = "",
                   source_title: str = "", source_url: str = "", state: str = "") -> Post:
    try:
        d_tag = snapshots.identifier_of(item)
    except ValueError:
        d_tag = ""
    return Post(key=f"m:{collection}:{d_tag}", origin=MEMORY_ORIGIN, source_key=source_key,
                source_title=source_title, source_url=source_url, d_tag=d_tag,
                title=item.title or "", excerpt=snapshots.excerpt(item),
                image=snapshots.cover(item), link=item.link or "", author=item.author or "",
                published_at=item.published_at or 0, found_at=0,
                read_minutes=snapshots.minutes_of(item),
                image_count=snapshots.images_in(item), state=state,
                collection=collection)


@dataclass
class Collection:
    """Posts held while they are shown: a source read when it is opened
    ("manual"), or a file or a link opened to import once ("file",
    "link")."""

    id: str
    kind: str                   # "manual" | "file" | "link"
    label: str
    source_url: str = ""
    source_key: str = ""
    items: List[FeedItem] = field(default_factory=list)
    # What became of a post, by identifier (drafted, published, removed).
    states: Dict[str, str] = field(default_factory=dict)
    loading: bool = False
    error: str = ""

    def posts(self) -> List[Post]:
        """The posts, newest first, each once."""
        out: List[Post] = []
        seen = set()
        for item in sorted(self.items, key=lambda i: i.published_at or 0, reverse=True):
            post = post_from_item(item, collection=self.id, source_key=self.source_key,
                                  source_title=self.label, source_url=self.source_url)
            if not post.d_tag or post.d_tag in seen:
                continue
            seen.add(post.d_tag)
            state = self.states.get(post.d_tag, "")
            out.append(replace(post, state=state) if state else post)
        return out

    def item(self, d_tag: str) -> Optional[FeedItem]:
        for item in self.items:
            try:
                if snapshots.identifier_of(item) == d_tag:
                    return item
            except ValueError:
                continue
        return None


@dataclass(frozen=True)
class Scope:
    """Which list the window shows: a stored list (``view``), or the posts
    of a source read now (``collection``)."""

    view: Optional[View] = None
    collection: str = ""

    @property
    def is_collection(self) -> bool:
        return bool(self.collection)


def matches(post: Post, query: str) -> bool:
    """Search over a held list: title, excerpt and source, any case."""
    needle = query.strip().casefold()
    if not needle:
        return True
    return any(needle in text.casefold()
               for text in (post.title, post.excerpt, post.source_title))


def selectable(post: Post, busy: Sequence[str] = ()) -> bool:
    """Whether a post can be imported: it has an identifier, it is not
    imported yet, and no import under way holds it."""
    return bool(post.d_tag) and not post.imported and post.d_tag not in busy


def targets(checked: Sequence[Post], focused: Optional[Post],
            busy: Sequence[str] = ()) -> List[Post]:
    """What an action applies to (Mail's rule): the checked posts, or else
    the open one, when it can be chosen."""
    if checked:
        return [p for p in checked if selectable(p, busy)]
    return [focused] if focused is not None and selectable(focused, busy) else []


def state_word(post: Post, *, importing: bool = False, failed: bool = False) -> str:
    """The word for what became of a post: Importing, Failed, Imported,
    Skipped, or New for one not looked at in a stored list; "" when there
    is nothing to say."""
    if importing:
        return _(STATE_IMPORTING)
    if failed:
        return _(STATE_FAILED)
    if post.imported:
        return _(STATE_IMPORTED)
    if post.skipped:
        return _(STATE_SKIPPED)
    if post.state == NEW:
        return _(STATE_NEW)
    return ""


def date_text(timestamp: int, *, now: Optional[float] = None,
              in_sentence: bool = False) -> str:
    """"2 h ago", "Yesterday", "Oct 3" or "Oct 3, 2025": relative under six
    days (STANDUP's rule), the year only when it is not this one.
    ``in_sentence`` gives the words that go inside a sentence ("newest
    yesterday")."""
    if not timestamp:
        return ""
    now = time.time() if now is None else now
    age = int(now) - int(timestamp)
    if 0 <= age < RELATIVE_SECONDS:
        if age < 60:
            return _("just now") if in_sentence else _("Just now")
        if age < 3600:
            minutes = age // 60
            return ngettext("{n} min ago", "{n} min ago", minutes).format(n=minutes)
        if age < 24 * 3600:
            hours = age // 3600
            return ngettext("{n} h ago", "{n} h ago", hours).format(n=hours)
        days = age // (24 * 3600)
        if days == 1:
            return _("yesterday") if in_sentence else _("Yesterday")
        return ngettext("{n} day ago", "{n} days ago", days).format(n=days)
    day = datetime.fromtimestamp(int(timestamp))
    locale = QLocale(i18n.language())
    date = QDate(day.year, day.month, day.day)
    english = locale.language() == QLocale.Language.English
    if day.year == datetime.fromtimestamp(int(now)).year:
        return locale.toString(date, "MMM d" if english else "d. MMM")
    return locale.toString(date, "MMM d, yyyy" if english else "d. MMM yyyy")


def checked_text(count: int, total: int) -> str:
    """"3 of 14 selected"."""
    return ngettext("{count} of {total} selected", "{count} of {total} selected",
                    count).format(count=count, total=total)
