# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The details a post's own page states about it.

A feed (or a sitemap, or a link) often leaves out what the page itself
says: the cover, a description, when it was published, its tags and
author. Pages state them for link previews and search engines, in three
places, read here best first:

1. JSON-LD (``<script type="application/ld+json">``): an ``Article``,
   ``BlogPosting`` or ``NewsArticle``, also inside an ``@graph``;
2. Open Graph and article tags (``og:image``, ``article:published_time``,
   ``article:tag``), and Twitter's (``twitter:image``);
3. plain ``<meta name="description">`` and ``<meta name="author">``.

:func:`fill_from_page` only fills what the item lacks: what the source
itself wrote always wins. Pure (no Qt, no network).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..rss.dates import to_unix
from ..rss.normalize import cover_address
from ..rss.parser import FeedItem
from ..rss.urls import is_web

_ARTICLE_TYPES = {"article", "blogposting", "newsarticle", "techarticle",
                  "report", "scholarlyarticle", "socialmediaposting", "webpage"}
_MAX_TAGS = 12
# A picture named as the site's default for link previews
# ("default-ogimage.png", "og-default.jpg"): every post would share it.
_SITE_DEFAULT = re.compile(r"(^|[/_-])(default[-_]?og|og[-_]?default|default[-_]?share)[^/]*$",
                           re.IGNORECASE)


@dataclass(frozen=True)
class PageMeta:
    title: str = ""
    description: str = ""
    image: str = ""
    published_at: Optional[int] = None
    author: str = ""
    tags: Tuple[str, ...] = ()


def read_page_meta(html: str, *, url: Optional[str] = None) -> PageMeta:
    """What the page at ``url`` states about its post. Never raises: an
    unreadable page states nothing."""
    if not html or not isinstance(html, str):
        return PageMeta()
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:  # noqa: BLE001, a page we cannot read states nothing
        return PageMeta()
    meta = _MetaTags(soup)
    ld = _json_ld_article(soup)

    title = _text(ld.get("headline")) or meta.first("og:title", "twitter:title")
    description = (_text(ld.get("description"))
                   or meta.first("og:description", "description", "twitter:description"))
    image = _image(ld.get("image")) or meta.first("og:image", "og:image:url",
                                                  "og:image:secure_url", "twitter:image",
                                                  "twitter:image:src")
    published = (to_unix(ld.get("datePublished"))
                 or to_unix(meta.first("article:published_time", "og:published_time",
                                     "date", "dc.date", "dcterms.created"))
                 or to_unix(ld.get("dateCreated")))
    author = _person(ld.get("author")) or _name(meta.first("author", "article:author",
                                                           "twitter:creator"))
    tags = _keywords(ld.get("keywords")) or tuple(meta.all("article:tag"))

    if _SITE_DEFAULT.search(urlsplit(image).path):
        image = ""  # the site's picture for every page, not this post's
    return PageMeta(
        title=title,
        description=description,
        image=cover_address(image, url),
        published_at=published,
        author=author,
        tags=tuple(dict.fromkeys(t for t in tags if t))[:_MAX_TAGS],
    )


def fill_from_page(item: FeedItem, page: PageMeta, *, page_title: str = "") -> FeedItem:
    """``item`` with what it lacks taken from its page. A title made from
    the address (a sitemap's) gives way to the page's own."""
    changes: dict = {}
    if not item.image and page.image:
        changes["image"] = page.image
    if not (item.summary or "").strip() and page.description:
        changes["summary"] = page.description
    if item.published_at is None and page.published_at is not None:
        changes["published_at"] = page.published_at
    if not item.categories and page.tags:
        changes["categories"] = page.tags
    if not (item.author or "").strip() and page.author:
        changes["author"] = page.author
    title = page.title or page_title
    if item.title_from_url and title:
        changes["title"] = title
        changes["title_from_url"] = False
    return replace(item, **changes) if changes else item


# -- reading ------------------------------------------------------------------ #

class _MetaTags:
    def __init__(self, soup: BeautifulSoup) -> None:
        self._values: dict = {}
        for tag in soup.find_all("meta"):
            key = (tag.get("property") or tag.get("name") or tag.get("itemprop") or "")
            value = " ".join((tag.get("content") or "").split())
            if key and value:
                self._values.setdefault(key.strip().lower(), []).append(value)

    def first(self, *keys: str) -> str:
        for key in keys:
            values = self._values.get(key)
            if values:
                return values[0]
        return ""

    def all(self, key: str) -> List[str]:
        return list(self._values.get(key, ()))


def _json_ld_article(soup: BeautifulSoup) -> dict:
    """The page's own article in its JSON-LD, or ``{}``."""
    found: List[dict] = []
    for script in soup.find_all("script", type=lambda t: t and "ld+json" in t.lower()):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (ValueError, TypeError):
            continue
        found.extend(_nodes(data))
    for wanted in (_ARTICLE_TYPES - {"webpage"}, {"webpage"}):
        for node in found:
            if _types(node) & wanted:
                return node
    return {}


def _nodes(data: Any) -> Iterable[dict]:
    if isinstance(data, list):
        for entry in data:
            yield from _nodes(entry)
    elif isinstance(data, dict):
        yield data
        graph = data.get("@graph")
        if isinstance(graph, list):
            yield from _nodes(graph)


def _types(node: dict) -> set:
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return {str(k).lower() for k in kinds if k}


def _text(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _image(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _text(value.get("url") or value.get("contentUrl"))
    if isinstance(value, list):
        for entry in value:
            image = _image(entry)
            if image:
                return image
    return ""


def _person(value: Any) -> str:
    if isinstance(value, list):
        names = [n for n in (_person(entry) for entry in value) if n]
        return ", ".join(dict.fromkeys(names))
    if isinstance(value, dict):
        return _text(value.get("name"))
    return _name(value if isinstance(value, str) else "")


def _name(value: str) -> str:
    """A name, not an address: ``article:author`` is often a profile URL."""
    value = (value or "").strip()
    if not value or is_web(value):
        return ""
    return value.lstrip("@") if value.startswith("@") and " " not in value else value


def _keywords(value: Any) -> Tuple[str, ...]:
    if isinstance(value, str):
        return tuple(k.strip() for k in value.split(",") if k.strip())
    if isinstance(value, list):
        return tuple(k.strip() for k in value if isinstance(k, str) and k.strip())
    return ()
