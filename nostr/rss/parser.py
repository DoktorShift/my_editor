# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""RSS 2.0, Atom, and JSON Feed parsing.

Auto-detects format from the input string and returns a normalised
``Feed`` containing ``FeedItem`` records. Field extraction matches the
TypeScript reference at ``nostr-core/src/rss.ts`` so a given feed yields
the same drafts on either side.

The dataclasses are deliberately small. Anything Nostr-specific (d-tag
derivation, NIP-23 mapping, source-link footer) lives in sibling modules.
"""

from __future__ import annotations

import calendar
import io
import json
import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlsplit

import feedparser


FeedFormat = str  # one of: "rss", "atom", "jsonfeed"


@dataclass(frozen=True)
class FeedItem:
    """One normalised entry from a feed of any supported format."""

    guid: str
    title: str
    link: Optional[str]
    summary: Optional[str]
    content_html: str
    published_at: Optional[int]
    categories: Tuple[str, ...]
    image: Optional[str]
    author: Optional[str]
    # True when ``title`` is a placeholder derived from the URL slug
    # (sitemap-discovered items). Full-text recovery upgrades it to the
    # real article title once the page has been read.
    title_from_url: bool = False
    # Pre-rendered Markdown body from sources that emit Markdown
    # directly (Nostr long-form, NostrHub NIPs, MDX documents). When
    # set, the import pipeline uses it verbatim and skips HTML-to-
    # Markdown conversion and every recovery pass.
    content_markdown: Optional[str] = None
    # Podcasting 2.0 payload (a ``sources.podcast.PodcastEpisode``)
    # attached by the RSS resolver's enrichment pass when the feed item
    # carries an audio enclosure. The import pipeline renders it as the
    # episode's Markdown body.
    podcast: Optional[object] = None
    # True when only part of the text is kept (the Imports inbox keeps at
    # most 256 KiB of a post, as EINUNDZWANZIG STANDUP does). Importing
    # such a post reads its own page for the whole text.
    partial: bool = False


@dataclass(frozen=True)
class Feed:
    """A parsed feed envelope and its items."""

    format: FeedFormat
    title: str
    link: Optional[str]
    description: Optional[str]
    items: Tuple[FeedItem, ...] = field(default_factory=tuple)


def with_whole_links(feed: Feed, address: Optional[str]) -> Feed:
    """``feed`` with each item's relative link made whole against
    ``address``, where the feed was read (without one, against the site
    the feed names). A relative link left the post's own relative links
    and images, its "Originally published at" line and Open Original
    pointing nowhere (CP3 review L8). An item without a guid keeps the
    link as written as its guid (the parser gives it one), so its draft
    identifier stays what earlier imports gave (nostr/rss/dtag.py).

    The cover is made whole too, against the post's own page: a cover
    written as ``/media/a.jpg`` was refused when the article was signed,
    and the draft lost it."""
    base = address if _is_web_address(address) else feed.link
    if not _is_web_address(base):
        return feed
    items = []
    for item in feed.items:
        link = (item.link or "").strip()
        if link and not link.startswith("#") and not urlsplit(link).scheme:
            item = replace(item, link=urljoin(base, link))
        image = whole_url(item.image, item.link if _is_web_address(item.link) else base)
        if image != item.image:
            item = replace(item, image=image)
        items.append(item)
    return replace(feed, items=tuple(items))


def whole_url(url: Optional[str], base: Optional[str]) -> Optional[str]:
    """``url`` made whole against the web address ``base``: a relative
    or protocol-relative address gets the page's scheme and host. Any
    other address, and every address when ``base`` is not a web
    address, is returned as it is."""
    value = (url or "").strip()
    if not value or not _is_web_address(base):
        return url
    if value.startswith("//") or not urlsplit(value).scheme:
        return urljoin(base, value)
    return url


def _is_web_address(url: Optional[str]) -> bool:
    return urlsplit(url or "").scheme in ("http", "https")


class RssError(Exception):
    """Raised when a feed cannot be parsed or recognised."""

    def __init__(self, message: str, code: str = "RSS_ERROR") -> None:
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- #
# Shared helpers                                                               #
# --------------------------------------------------------------------------- #

_IMG_SRC_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)


def _first_image_from_html(html: str) -> Optional[str]:
    """Return the ``src`` of the first ``<img>`` tag in ``html``, if any."""
    if not html:
        return None
    match = _IMG_SRC_RE.search(html)
    return match.group(1) if match else None


def _struct_time_to_unix(st: Optional[time.struct_time]) -> Optional[int]:
    """Convert a feedparser ``struct_time`` (always UTC) to unix seconds."""
    if st is None:
        return None
    try:
        return int(calendar.timegm(st))
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_date_string(value: Optional[str]) -> Optional[int]:
    """Best-effort RFC-822 / ISO-8601 to unix seconds. JSON Feed only."""
    if not value:
        return None
    s = value.strip()
    if not s:
        return None
    # ISO 8601 with trailing Z is the JSON Feed canonical form.
    try:
        if s.endswith("Z"):
            dt = datetime.fromisoformat(s[:-1] + "+00:00")
        else:
            dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(s)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return None


def _clean_str(value: Any) -> str:
    """Coerce to a stripped string, never raising."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


# --------------------------------------------------------------------------- #
# RSS 2.0                                                                      #
# --------------------------------------------------------------------------- #

def _rss_image(entry: Mapping[str, Any], content_html: str) -> Optional[str]:
    """Image priority: image enclosure, media:content, media:thumbnail, body."""
    for enc in entry.get("enclosures") or ():
        ctype = (enc.get("type") or "").lower()
        href = enc.get("href") or enc.get("url")
        if href and ctype.startswith("image/"):
            return href

    media_content = entry.get("media_content") or ()
    for mc in media_content:
        url = mc.get("url") if isinstance(mc, Mapping) else None
        if url:
            return url

    media_thumb = entry.get("media_thumbnail") or ()
    for mt in media_thumb:
        url = mt.get("url") if isinstance(mt, Mapping) else None
        if url:
            return url

    return _first_image_from_html(content_html)


def _entry_categories(entry: Mapping[str, Any]) -> Tuple[str, ...]:
    out: List[str] = []
    for tag in entry.get("tags") or ():
        if isinstance(tag, Mapping):
            term = _clean_str(tag.get("term"))
            if term:
                out.append(term)
    return tuple(out)


def _content_html_from_entry(entry: Mapping[str, Any]) -> str:
    """feedparser exposes ``content:encoded`` / ``<content>`` via ``content``."""
    contents = entry.get("content") or ()
    for c in contents:
        if isinstance(c, Mapping):
            value = c.get("value")
            if value:
                return value
    return ""


def _rss_author(entry: Mapping[str, Any]) -> Optional[str]:
    return _clean_str(entry.get("author")) or None


def _entry_to_item(
    entry: Mapping[str, Any],
    *,
    image_for: Callable[[Mapping[str, Any], str], Optional[str]],
    published_for: Callable[[Mapping[str, Any]], Optional[time.struct_time]],
    author_for: Callable[[Mapping[str, Any]], Optional[str]],
) -> FeedItem:
    """Shared RSS/Atom entry normalisation.

    Only three fields are genuinely format-specific (image priority,
    publish-date fallback chain, author extraction); the caller supplies
    those as strategies and everything else is computed identically.
    """
    content_html = _content_html_from_entry(entry)
    summary = _clean_str(entry.get("summary"))
    # ``description`` and ``content:encoded`` coincide when the feed only
    # ships a description. Don't duplicate it into the summary slot.
    if not content_html:
        content_html = summary
        summary_for_item: Optional[str] = None
    else:
        summary_for_item = summary if summary and summary != content_html else None

    link = _clean_str(entry.get("link")) or None
    title = _clean_str(entry.get("title"))
    guid = _clean_str(entry.get("id")) or link or title

    return FeedItem(
        guid=guid,
        title=title,
        link=link,
        summary=summary_for_item,
        content_html=content_html,
        published_at=_struct_time_to_unix(published_for(entry)),
        categories=_entry_categories(entry),
        image=image_for(entry, content_html),
        author=author_for(entry),
    )


def _parse_rss(parsed: Any) -> Feed:
    channel = parsed.feed or {}
    items = [
        _entry_to_item(
            entry,
            image_for=_rss_image,
            # RSS 1.0 and many generators date an item only with
            # ``dc:date``, which feedparser files under "updated".
            published_for=lambda e: (
                e.get("published_parsed") or e.get("updated_parsed")
            ),
            author_for=_rss_author,
        )
        for entry in parsed.entries or ()
    ]
    return Feed(
        format="rss",
        title=_clean_str(channel.get("title")),
        link=_clean_str(channel.get("link")) or None,
        description=_clean_str(channel.get("description")) or None,
        items=tuple(items),
    )


# --------------------------------------------------------------------------- #
# Atom                                                                         #
# --------------------------------------------------------------------------- #

def _atom_author(entry: Mapping[str, Any]) -> Optional[str]:
    author = _clean_str(entry.get("author")) or None
    if author:
        return author
    for a in entry.get("authors") or ():
        if isinstance(a, Mapping):
            name = _clean_str(a.get("name"))
            if name:
                return name
    return None


def _parse_atom(parsed: Any) -> Feed:
    head = parsed.feed or {}
    items = [
        _entry_to_item(
            entry,
            image_for=lambda _e, html: _first_image_from_html(html),
            published_for=lambda e: (
                e.get("published_parsed") or e.get("updated_parsed")
            ),
            author_for=_atom_author,
        )
        for entry in parsed.entries or ()
    ]
    return Feed(
        format="atom",
        title=_clean_str(head.get("title")),
        link=_clean_str(head.get("link")) or None,
        description=_clean_str(head.get("subtitle")) or None,
        items=tuple(items),
    )


# --------------------------------------------------------------------------- #
# JSON Feed                                                                    #
# --------------------------------------------------------------------------- #

def _json_item_author(it: Mapping[str, Any]) -> Optional[str]:
    author = it.get("author")
    if isinstance(author, Mapping):
        name = _clean_str(author.get("name"))
        if name:
            return name
    authors = it.get("authors")
    if isinstance(authors, Sequence) and not isinstance(authors, (str, bytes)):
        for a in authors:
            if isinstance(a, Mapping):
                name = _clean_str(a.get("name"))
                if name:
                    return name
    return None


def _parse_json_feed(payload: Any) -> Feed:
    if not isinstance(payload, Mapping):
        raise RssError("Invalid JSON Feed: top-level value is not an object",
                       "INVALID_JSONFEED")
    raw_items = payload.get("items")
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
        raise RssError("Invalid JSON Feed: missing items array",
                       "INVALID_JSONFEED")

    items: List[FeedItem] = []
    for it in raw_items:
        if not isinstance(it, Mapping):
            continue
        content_html = (
            _clean_str(it.get("content_html"))
            or _clean_str(it.get("content_text"))
            or _clean_str(it.get("summary"))
        )
        title = _clean_str(it.get("title"))
        link = _clean_str(it.get("url")) or None
        guid = _clean_str(it.get("id")) or link or title

        tags = it.get("tags")
        categories: Tuple[str, ...] = ()
        if isinstance(tags, Sequence) and not isinstance(tags, (str, bytes)):
            categories = tuple(_clean_str(t) for t in tags if _clean_str(t))

        image = (
            _clean_str(it.get("image"))
            or _clean_str(it.get("banner_image"))
            or _first_image_from_html(content_html)
            or None
        )

        items.append(
            FeedItem(
                guid=guid,
                title=title,
                link=link,
                summary=_clean_str(it.get("summary")) or None,
                content_html=content_html,
                published_at=(_parse_date_string(it.get("date_published"))
                              or _parse_date_string(it.get("date_modified"))),
                categories=categories,
                image=image if isinstance(image, str) else None,
                author=_json_item_author(it),
            )
        )

    return Feed(
        format="jsonfeed",
        title=_clean_str(payload.get("title")),
        link=_clean_str(payload.get("home_page_url")) or None,
        description=_clean_str(payload.get("description")) or None,
        items=tuple(items),
    )


# --------------------------------------------------------------------------- #
# Public entry point                                                           #
# --------------------------------------------------------------------------- #

_XML_DECL_ENCODING = re.compile(
    r"""^(\ufeff?\s*<\?xml[^>]*?\bencoding\s*=\s*)(["'])[^"']*\2""",
    re.IGNORECASE)


def _as_stream(text: str) -> io.BytesIO:
    """``text`` as the stream feedparser reads.

    Never the bare string: feedparser takes a string that reads as an
    address (``http://``, ``file://``) or as a file name for something to
    open, so a body that is only such an address would have it fetch
    past the network guard, or read a file on this computer.

    ``text`` is already decoded (fetch.py, or the file's reader). Its
    XML declaration still names the encoding the bytes had on the wire,
    and feedparser would decode the UTF-8 it is given with that one
    again: a ``windows-1252`` feed came out as "GrÃ¼ÃŸe". The
    declaration is made to say what the bytes now are."""
    return io.BytesIO(
        _XML_DECL_ENCODING.sub(r"\1\2utf-8\2", text, count=1).encode("utf-8"))


def parse_feed(text: str) -> Feed:
    """Auto-detect and parse an RSS, Atom, or JSON Feed payload.

    Raises ``RssError`` if the input doesn't look like any supported format.
    """
    if not text:
        raise RssError("Empty feed payload", "EMPTY_FEED")

    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise RssError(f"Invalid JSON Feed: {exc}", "INVALID_JSONFEED") from exc
        return _parse_json_feed(payload)

    parsed = feedparser.parse(_as_stream(text), sanitize_html=False)
    version = (getattr(parsed, "version", "") or "").lower()
    if version.startswith("atom"):
        return _parse_atom(parsed)
    if version.startswith("rss") or version.startswith("cdf"):
        return _parse_rss(parsed)

    # feedparser's auto-detection failed. If there are entries we can still
    # try treating it as RSS, otherwise give up with a clear message.
    if parsed.entries:
        return _parse_rss(parsed)
    raise RssError("Unrecognised feed format", "UNKNOWN_FORMAT")
