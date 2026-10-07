# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""An article's details, kept the same way wherever they travel.

A long-form article (NIP-23, kind 30023) keeps its details in tags: its
identifier (``d``), title, summary, cover image, hashtags (``t``) and the
time it was first published (``published_at``), and a few tags MyEditor
carries without editing them: where an imported article came from
(``source``) and what is known about its pictures (``imeta``, NIP-92).

An article draft holds them in its inner event. Saving the draft again,
publishing it, and the Article Details inspector all read and write them
through this one model, so nothing is dropped on the way: saving an
imported draft used to keep only its identifier, title and summary, and
the next publication then had no cover, no hashtags and a new date.

The expiry of an imported draft is not a detail of the article; it is
on the draft's wrap, and a draft the person saved has none.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, List, Optional, Sequence, Tuple

# Tags carried as they are: nothing in MyEditor edits them.
CARRIED = ("source", "imeta")


@dataclass(frozen=True)
class ArticleDetails:
    """The details of one article (see the module docstring)."""

    identifier: str = ""
    title: str = ""
    summary: str = ""
    image: str = ""
    hashtags: Tuple[str, ...] = ()
    published_at: Optional[int] = None
    carried: Tuple[Tuple[str, ...], ...] = field(default=())

    @classmethod
    def from_tags(cls, tags: Iterable[Sequence[str]]) -> "ArticleDetails":
        """The details an event's tags hold (the first of each kind wins;
        every hashtag counts)."""
        found = {}
        hashtags: List[str] = []
        carried: List[Tuple[str, ...]] = []
        for tag in tags or ():
            if not isinstance(tag, (list, tuple)) or len(tag) < 2 \
                    or not all(isinstance(part, str) for part in tag):
                continue
            name, value = tag[0], tag[1]
            if name == "t":
                if value.strip() and value.strip().lower() not in hashtags:
                    hashtags.append(value.strip().lower())
            elif name in CARRIED:
                carried.append(tuple(tag))
            elif name in ("d", "title", "summary", "image", "published_at") \
                    and name not in found:
                found[name] = value
        published_at = None
        try:
            published_at = int(found.get("published_at", "").strip()) or None
        except ValueError:
            pass
        return cls(identifier=found.get("d", ""), title=found.get("title", ""),
                   summary=found.get("summary", ""), image=found.get("image", ""),
                   hashtags=tuple(hashtags), published_at=published_at,
                   carried=tuple(carried))

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
        tags.extend(self.carried_tags(content))
        return tags
