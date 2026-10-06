# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What an address or a file brings in: a source to follow, posts to
import once, or a list of sources.

The Imports window's sheets (Follow a Website, Import a File, Import a
Link) ask these questions; the answers are plain functions so they are
the same in every sheet and tested on their own:

- :func:`kind_of` names what an address is, in the words the sheets use
  (EINUNDZWANZIG STANDUP's sourceKind): a **feed** (checked while the
  app runs), an **author** (someone's Nostr articles), a **collection**
  (a sitemap, a folder of Markdown files), or a **single** post that is
  imported once and not kept as a source.
- :func:`is_list_address` tells an address of a list of sources (OPML)
  from one of a source.
- :func:`read_export` reads an export file (a Medium or Substack ZIP, a
  WordPress or Ghost export, a feed, a Markdown file, an OPML list) into
  posts or into a list of sources. It runs off the UI thread; parsing a
  large export takes a moment. A WordPress or Ghost export and a feed
  are read by the same readers as a pasted feed (the controller hands
  their text on); the rest is read here.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from i18n import _

from ..rss.parser import FeedItem
from .errors import ERROR_CODES, SourceError, friendly_message
from .registry import ResolveInput, detect_resolver
from .sources.archive import ArchiveError, extract_archive, looks_like_zip
from .sources.mdx import mdx_to_markdown
from .sources.nostr import extract_nostr_entity
from .sources.opml import OpmlDocument, is_opml, parse_opml

FEED, AUTHOR, COLLECTION, SINGLE = "feed", "author", "collection", "single"
FOLLOWABLE = (FEED, AUTHOR, COLLECTION)
FEED_FORMATS = ("rss", "atom", "jsonfeed")


MAX_FILE_BYTES = 64 * 1024 * 1024
MARKDOWN_SUFFIXES = (".md", ".mdx", ".markdown")
TOO_LARGE = _("That file is too large to import (over {size} MB).")
NOT_AN_EXPORT = _("That ZIP doesn't look like a Medium or Substack export, or it has no "
                  "published posts.")

_GITHUB_FOLDER = re.compile(r"^https?://github\.com/[^/]+/[^/]+/tree/", re.IGNORECASE)
_FIRST_HEADING = re.compile(r"\A#[ \t]+(.+?)[ \t#]*(?:\n|\Z)")
_OPML_ADDRESS = re.compile(r"\.opml(?:$|[?#])", re.IGNORECASE)


def kind_of(url: str, feed_format: str = "") -> str:
    """What the address ``url`` is (one of FEED, AUTHOR, COLLECTION,
    SINGLE), or "" when no reader claims it. ``feed_format`` is what
    reading it gave, when known: an address the feed reader found only a
    sitemap at is a collection."""
    url = (url or "").strip()
    resolver = detect_resolver(ResolveInput(url=url)) if url else None
    if resolver is None:
        return ""
    name = resolver.id
    if name in ("nostr", "nostrhub"):
        entity = extract_nostr_entity(url)
        if entity is None or entity.type in ("npub", "nprofile"):
            return AUTHOR      # an npub, an nprofile or a name@domain address
        return SINGLE          # one article, one note
    if name == "mdx":
        return COLLECTION if _GITHUB_FOLDER.match(url) else SINGLE
    if name == "sitemap":
        return COLLECTION
    if name == "rss":
        if feed_format and feed_format not in FEED_FORMATS:
            return COLLECTION
        return FEED
    return SINGLE


def kind_word(kind: str) -> str:
    """What a source is, as a found card names it."""
    words = {FEED: _("Feed"), AUTHOR: _("Nostr author"), COLLECTION: _("Collection")}
    return words.get(kind) or _("Single post")


def is_list_address(url: str) -> bool:
    """Whether ``url`` names a list of sources (an .opml file)."""
    return bool(_OPML_ADDRESS.search((url or "").strip()))


def looks_like_markup(text: str) -> bool:
    """Whether pasted text is XML or HTML (it needs its address so the
    links in it work)."""
    return text.lstrip().startswith("<")


@dataclass(frozen=True)
class Export:
    """What an export file holds: posts, or a list of sources."""

    label: str
    items: Tuple[FeedItem, ...] = ()
    sources: Optional[OpmlDocument] = None
    text: str = ""          # a file the importer still has to read


def read_export(path: str) -> Export:
    """Read the file at ``path``. Raises ValueError with words for the
    person when it cannot be imported."""
    label = os.path.basename(path)
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise ValueError(_("Couldn't read that file: {error}").format(error=exc)) from exc
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(TOO_LARGE.format(size=MAX_FILE_BYTES // (1024 * 1024)))
    if looks_like_zip(data):
        try:
            result = extract_archive(data)
        except ArchiveError as exc:
            raise ValueError(friendly_message(SourceError(
                str(exc), ERROR_CODES.ARCHIVE_UNREADABLE))) from exc
        if result.platform is None or not result.items:
            raise ValueError(NOT_AN_EXPORT)
        return Export(label=label, items=tuple(result.items))
    text = data.decode("utf-8", errors="replace")
    if is_opml(text):
        return Export(label=label, sources=parse_opml(text))
    if label.lower().endswith(MARKDOWN_SUFFIXES):
        return Export(label=label, items=(markdown_item(text, label, data),))
    return Export(label=label, text=text)


def markdown_item(text: str, label: str, data: bytes) -> FeedItem:
    """One post from a Markdown file. Its identity is its content: the
    same file imported again is found as imported already, an edited one
    is a new post."""
    doc = mdx_to_markdown(text)
    title, markdown = doc.title, doc.markdown
    if not title:
        # No title in its front matter: a first-level heading on the first
        # line is the title (and leaves the body, so it is not there twice).
        heading = _FIRST_HEADING.match(markdown)
        if heading:
            title, markdown = heading.group(1).strip(), markdown[heading.end():].lstrip()
    if not title:
        stem = os.path.splitext(label)[0].replace("-", " ").replace("_", " ").strip()
        title = stem[:1].upper() + stem[1:] or label
    return FeedItem(
        guid="file:" + hashlib.sha256(data).hexdigest()[:32],
        title=title, link="", summary=doc.summary or None, content_html="",
        published_at=None, categories=(), image=None, author=None,
        content_markdown=markdown)


def opml_titles(document: OpmlDocument, shown: int = 8) -> Tuple[List[str], int]:
    """The first ``shown`` titles of a list of sources, and how many more."""
    titles = [feed.title or feed.xml_url for feed in document.feeds]
    return titles[:shown], max(0, len(titles) - shown)
