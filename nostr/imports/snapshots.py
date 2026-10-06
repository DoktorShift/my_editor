# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A post as the imports inbox keeps it: a snapshot of one feed item.

A post is stored the moment a source is checked and never changed
afterwards (an edit on the source never touches a post already here,
and never a draft made from it). :func:`to_snapshot` and
:func:`from_snapshot` turn a :class:`FeedItem` into plain JSON and back,
podcast details included.

The figures a list row shows are computed the way EINUNDZWANZIG STANDUP
computes them (src/utils/feedItemStats.js), so both apps show the same
numbers for the same post: :func:`reading_minutes` (words of the
tag-stripped body over 225, rounded, at least 1 when there are words),
:func:`image_count` (``<img`` tags), :func:`excerpt` (the summary, or the
first 200 characters of the plain text) and :func:`cover` (the item's
image, else the first image in the body).
"""

from __future__ import annotations

import dataclasses
import html
import re
from typing import Any, Dict, Iterable, List

from ..rss.dtag import derive_identifier
from ..rss.normalize import html_to_markdown
from ..rss.parser import FeedItem
from .constants import IDENTIFIER_PREFIX
from .images import scan_markdown_images
from .sources.podcast import PodcastEpisode, ValueRecipient

EXCERPT_CHARS = 200

_TAG = re.compile(r"<[^>]+>")
_IMG = re.compile(r"<img\b", re.IGNORECASE)
_IMG_SRC = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)
_SPACE = re.compile(r"\s+")
_FIELDS = ("guid", "title", "link", "summary", "content_html", "published_at",
           "image", "author", "title_from_url", "content_markdown")


def identifier_of(item: FeedItem) -> str:
    """The draft identifier an import of ``item`` gets (the same in
    STANDUP, nostr/rss/dtag.py)."""
    return derive_identifier(guid=item.guid, link=item.link, title=item.title,
                             prefix=IDENTIFIER_PREFIX)


def to_snapshot(item: FeedItem) -> Dict[str, Any]:
    data = {name: getattr(item, name) for name in _FIELDS}
    data["categories"] = list(item.categories)
    episode = item.podcast
    if dataclasses.is_dataclass(episode):
        data["podcast"] = dataclasses.asdict(episode)
    return data


def from_snapshot(data: Dict[str, Any]) -> FeedItem:
    podcast = None
    raw = data.get("podcast")
    if isinstance(raw, dict) and raw.get("audio"):
        values = tuple(ValueRecipient(**v) for v in raw.get("value") or ()
                       if isinstance(v, dict))
        known = {f.name for f in dataclasses.fields(PodcastEpisode)}
        podcast = PodcastEpisode(**{**{k: v for k, v in raw.items() if k in known},
                                    "value": values})
    published = data.get("published_at")
    return FeedItem(
        guid=str(data.get("guid") or ""),
        title=str(data.get("title") or ""),
        link=data.get("link") or None,
        summary=data.get("summary") or None,
        content_html=str(data.get("content_html") or ""),
        published_at=int(published) if isinstance(published, (int, float)) else None,
        categories=tuple(str(c) for c in data.get("categories") or ()),
        image=data.get("image") or None,
        author=data.get("author") or None,
        title_from_url=bool(data.get("title_from_url")),
        content_markdown=data.get("content_markdown") or None,
        podcast=podcast,
    )


def plain_text(body_html: str) -> str:
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", body_html or ""))).strip()


def reading_minutes(body_html: str) -> int:
    words = len(_TAG.sub(" ", body_html or "").split())
    return max(1, int(words / 225 + 0.5)) if words else 0


def image_count(body_html: str) -> int:
    return len(_IMG.findall(body_html or ""))


def excerpt(item: FeedItem) -> str:
    summary = plain_text(item.summary or "")
    if summary:
        return summary[:EXCERPT_CHARS * 2]
    text = plain_text(item.content_html) or (item.content_markdown or "").strip()
    return text[:EXCERPT_CHARS]


def images_to_copy(items: Iterable[FeedItem]) -> List[str]:
    """Every image an import of ``items`` would copy, in order, once: each
    post's cover, then the images of the Markdown the import makes of it
    (its own Markdown, or the one made from its HTML). Built the way the
    pipeline works, so an address left unchecked in the review is the
    very address the import leaves alone. Converts HTML: run it off the
    UI thread for many posts."""
    found: List[str] = []
    for item in items:
        markdown = item.content_markdown or html_to_markdown(item.content_html or "")
        for url in [item.image or "", *scan_markdown_images(markdown)]:
            if url and url not in found and url.lower().startswith(("http://", "https://")):
                found.append(url)
    return found


def has_images(item: FeedItem) -> bool:
    """Whether an import of ``item`` may copy an image (cheap)."""
    return bool(item.image) or "<img" in (item.content_html or "").lower() or "![" in (
        item.content_markdown or "")


def cover(item: FeedItem) -> str:
    if item.image:
        return item.image
    match = _IMG_SRC.search(item.content_html or "")
    return match.group(1) if match else ""
