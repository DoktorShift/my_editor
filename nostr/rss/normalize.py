# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Normalise a ``FeedItem`` into kwargs for ``nostr.publisher.build_article``.

Two layers:

- :func:`html_to_markdown` runs ``markdownify`` with TurndownService-aligned
  options so the output matches the TypeScript reference byte-for-byte
  whenever the input HTML round-trips cleanly.
- :func:`item_to_article` produces a small :class:`ArticleTemplate` value
  the importer feeds to ``build_article`` (which then becomes the inner
  event for a NIP-37 draft wrap).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple
from urllib.parse import urlsplit

from markdownify import ATX, markdownify as _markdownify

from .clean import clean_html, code_language
from .dtag import derive_identifier
from .parser import FeedItem, whole_url


_SOURCE_FOOTER_SEPARATOR = "\n\n---\n\n"


@dataclass(frozen=True)
class ArticleTemplate:
    """Inputs for ``build_article``. The signing pubkey is supplied by
    the caller; everything else comes from the feed item."""

    slug: str
    content: str
    title: str
    summary: str
    image: str
    published_at: Optional[int]
    hashtags: Tuple[str, ...]


def html_to_markdown(html: str, *, base_url: Optional[str] = None) -> str:
    """HTML to Markdown with TS-reference-aligned options.

    The HTML is cleaned first (clean.py): ``base_url`` (the post's own
    page) makes relative links and images whole, since a feed's
    ``<a href="/about">`` or ``<img src="/media/a.jpg">`` points nowhere
    in a draft or an article read elsewhere (CP1 review O3); lazy
    images, embeds, video and audio, and page furniture are settled
    there too. A code block keeps the language it names.
    """
    if not html:
        return ""
    return _markdownify(
        clean_html(html, base_url=base_url),
        heading_style=ATX,
        bullets="-",
        strong_em_symbol="_",
        code_language_callback=code_language,
    )


# WordPress's default category, as its translations name it: a post
# nobody filed is not about "uncategorized".
_UNFILED = frozenset({
    "uncategorized", "uncategorised", "allgemein", "sincategoría",
    "sincategoria", "nonclassé", "nonclasse", "senzacategoria",
    "geencategorie", "semcategoria", "bezkategorii", "okategoriserade",
})

_SUMMARY_CHARS = 280
_SPACE = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")
_SPACE_BEFORE_MARK = re.compile(r"\s+([.,;:!?)])")
_EMAIL = re.compile(r"^[^@\s()<>]+@[^@\s()<>]+\.[^@\s()<>]+$")
_EMAIL_WITH_NAME = re.compile(r"^\S+@\S+\s*\((.+)\)$")
_READ_MORE = re.compile(r"\s*(\[(…|\.\.\.|&hellip;)\]|…|\.\.\.)\s*"
                        r"(continue reading.*|read more.*|weiterlesen.*)?$",
                        re.IGNORECASE)
_APPEARED_FIRST = re.compile(r"\s*\b(The post|Der Beitrag)\b.+\b(appeared first on|"
                             r"erschien zuerst auf)\b.*$", re.IGNORECASE)
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_MARKS = re.compile(r"(^|\s)[#>]+\s|(?<!\\)[*_`~]+|<(https?://[^>]+)>")
_MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!<>|~])")


def hashtag(raw: str) -> str:
    """One category as a Nostr hashtag: no ``#``, lower case, no spaces
    (a ``t`` tag with a space is a hashtag no client can link)."""
    return "".join((raw or "").strip().lstrip("#").lower().split())


def _merge_hashtags(
    item_categories: Iterable[str],
    extra: Iterable[str],
) -> Tuple[str, ...]:
    """As hashtags (:func:`hashtag`), deduped in first-seen order.
    WordPress's default "Uncategorized" is left out."""
    seen: list[str] = []
    categories = [hashtag(raw) for raw in item_categories]
    for tag in (*(c for c in categories if c not in _UNFILED),
                *(hashtag(raw) for raw in extra)):
        if tag and tag not in seen:
            seen.append(tag)
    return tuple(seen)


def author_name(raw: Optional[str]) -> str:
    """The name to credit a post to, or "" when the source names nobody.

    RSS writes an author as an e-mail address, often with the name after
    it (``jane@example.com (Jane Doe)``): the name is kept, an address
    alone is never published."""
    value = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", raw or ""))).strip()
    match = _EMAIL_WITH_NAME.match(value)
    if match:
        value = match.group(1).strip()
    if _EMAIL.match(value):
        return ""
    return value


def plain_summary(summary_html: Optional[str]) -> str:
    """A feed's summary as the plain text NIP-23 asks for: no tags, no
    entities, one line, without the "[…]" a teaser ends on or the
    "The post X appeared first on Y." line SEO plugins add."""
    text = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", summary_html or ""))).strip()
    text = _SPACE_BEFORE_MARK.sub(r"\1", text)
    text = _APPEARED_FIRST.sub("", text).strip()
    trimmed = _READ_MORE.sub("", text).strip()
    if trimmed != text and trimmed:
        text = trimmed + "…"
    return _shorten(text)


def summary_from_body(markdown: str) -> str:
    """A summary made of the article's opening words, for a post whose
    source gives none: what a reader sees first, cut at a word."""
    paragraphs = []
    in_code = False
    for block in re.split(r"\n\s*\n", markdown or ""):
        stripped = block.strip()
        if stripped.startswith("```"):
            in_code = not in_code or stripped.count("```") > 1
            continue
        if in_code or not stripped or stripped.startswith(("#", "|", "---", "*Originally")):
            continue
        text = _MD_MARKS.sub(lambda m: m.group(1) or m.group(2) or "",
                             _MD_LINK.sub(r"\1", _MD_IMAGE.sub(" ", stripped)))
        text = _MD_ESCAPE.sub(r"\1", text)
        text = _SPACE.sub(" ", text).strip()
        if text:
            paragraphs.append(text)
        if sum(len(p) for p in paragraphs) >= _SUMMARY_CHARS // 2:
            break
    return _shorten(" ".join(paragraphs))


_TEASER_END = re.compile(
    r"(\[(…|\.\.\.)\]|…|\.\.\.|\[?(continue reading|read more|read the full|"
    r"weiterlesen|mehr lesen|lire la suite|leer más|seguir leyendo|"
    r"continua a leggere|lees verder)[^\n]*?(\]\([^)]*\))?)\s*$",
    re.IGNORECASE)


def ends_as_teaser(markdown: str) -> bool:
    """Whether a body ends the way a teaser does ("[…]", "…", "Continue
    reading"): the post goes on on its page, however long the part in
    the feed is (WordPress's "Summary" feeds give 55 words)."""
    return bool(_TEASER_END.search((markdown or "").strip()))


def _shorten(text: str) -> str:
    if len(text) <= _SUMMARY_CHARS:
        return text
    cut = text[:_SUMMARY_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:.-")
    return cut + "…"


def source_link_footer(link: str, author: Optional[str] = None) -> str:
    """Footer block appended to a feed item's body when ``link`` is set,
    crediting the post's author when the source names one.

    Public so other callers (notably the long-form resolver) can compute
    its length to decide whether the pre-footer body is "thin", without
    re-implementing the format.
    """
    name = author_name(author)
    if name:
        credit = _MD_SPECIAL.sub(r"\\\g<0>", name)
        return (f"{_SOURCE_FOOTER_SEPARATOR}*Originally published by {credit} "
                f"at [{link}]({link})*")
    return f"{_SOURCE_FOOTER_SEPARATOR}*Originally published at [{link}]({link})*"


_MD_SPECIAL = re.compile(r"[\\`*_\[\]<>]")


def item_to_article(
    item: FeedItem,
    *,
    hashtags: Iterable[str] = (),
    identifier_prefix: Optional[str] = None,
    append_source_link: bool = True,
) -> ArticleTemplate:
    """Convert a :class:`FeedItem` to an :class:`ArticleTemplate`.

    The d-tag identifier is derived deterministically from
    ``(guid, link, title)`` so re-importing the same feed produces the
    same draft slot on the relay (idempotent replacement).

    Content precedence: a source-supplied ``content_markdown`` (Nostr
    long-form, NostrHub NIPs, MDX) is authoritative and taken verbatim;
    otherwise the item's HTML body is converted.

    The details are made fit to publish whatever the source wrote: the
    summary as plain text (or the opening words when the source has
    none), the cover as a whole address, the hashtags without spaces.
    """
    if (item.content_markdown or "").strip():
        markdown = str(item.content_markdown).strip()
    else:
        markdown = html_to_markdown(item.content_html, base_url=item.link).strip()
    markdown = _without_title_heading(markdown, item.title)
    body = markdown
    if append_source_link and item.link:
        markdown = (markdown + source_link_footer(item.link, item.author)).strip()

    slug = derive_identifier(
        guid=item.guid,
        link=item.link,
        title=item.title,
        prefix=identifier_prefix,
    )

    return ArticleTemplate(
        slug=slug,
        content=markdown,
        title=item.title or "",
        summary=plain_summary(item.summary) or summary_from_body(body),
        image=cover_address(item.image, item.link),
        published_at=item.published_at,
        hashtags=_merge_hashtags(item.categories, hashtags),
    )


_HEADING = re.compile(r"\A#{1,2}[ \t]+(.+?)[ \t#]*(\n+|\Z)")


def _without_title_heading(markdown: str, title: Optional[str]) -> str:
    """The body without a first heading that only repeats the title (a
    page read for its full text, a Markdown file): the title is shown
    above the article already."""
    match = _HEADING.match(markdown)
    if match and title and _comparable(match.group(1)) == _comparable(title):
        return markdown[match.end():].lstrip()
    return markdown


def _comparable(text: str) -> str:
    return "".join(ch for ch in html.unescape(text).lower() if ch.isalnum())


def cover_address(image: Optional[str], page: Optional[str]) -> str:
    """The cover as a whole web address (made whole against the post's
    page), or "" for anything else (a ``data:`` picture, a bare path
    with no page to place it)."""
    value = whole_url(image, page) or ""
    return value.strip() if urlsplit(value.strip()).scheme in ("http", "https") else ""
