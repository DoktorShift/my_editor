# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a post's own page adds when its source gave only part of it.

One rule for the import (pipeline.py) and for the Imports window's
article view (imports_controller.py), so the article shown is the draft
made:

- :func:`page_wanted`: whether the page is read, for its text (the
  source gave a teaser, or a link to the post) and for its details (the
  post has no cover);
- :func:`what_the_page_adds`: the post's whole text (Readability) when
  it clearly beats what the source gave, and the details the page
  states (page_meta.py) where the post has none.

Pure; :func:`what_the_page_adds` is CPU-heavy, so callers run it off the
UI thread.
"""

from __future__ import annotations

from typing import Optional, Tuple

from ..rss.normalize import ArticleTemplate, ends_as_teaser, html_to_markdown, reading_text
from ..rss.parser import FeedItem
from ..rss.urls import is_web
from .constants import THIN_CONTENT_CHARS
from .fulltext import extract_readable_content
from .page_meta import fill_from_page, read_page_meta


def is_teaser(body: str) -> bool:
    """Whether a body is only the start of its post or a link to it: it
    says few words (addresses do not count), or it ends the way a
    teaser does ("[…]", "Continue reading")."""
    return len(reading_text(body)) < THIN_CONTENT_CHARS or ends_as_teaser(body)


def page_wanted(item: FeedItem, template: ArticleTemplate, *,
                fetch_full_text: bool) -> Tuple[bool, bool]:
    """``(for its text, for its details)``: whether the post's page is
    read. A post kept only in part is read for its text whatever the
    option (the part alone would be a cut draft). Never for a post whose
    source gives its text as Markdown, a podcast episode (its show notes
    are its text), or one without a web page."""
    if not is_web(item.link) or (item.content_markdown or "").strip() \
            or getattr(item.podcast, "audio", ""):
        return False, False
    wants_text = item.partial or (fetch_full_text and is_teaser(template.body))
    return wants_text, fetch_full_text and not template.image


def what_the_page_adds(item: FeedItem, body: str, page_html: str, *,
                       wants_text: bool) -> Optional[Tuple[FeedItem, str]]:
    """``(item, body)`` with what the post's page adds, or None when it
    adds nothing. Its text is taken only on a clear win over ``body``: a
    bad extraction (navigation, a footer) is usually shorter, not longer;
    for a post kept only in part, any more text is a win."""
    page = read_page_meta(page_html, url=item.link)
    content = extract_readable_content(page_html, url=item.link) if wants_text else None
    markdown = html_to_markdown(content.html, base_url=item.link).strip() if content else ""
    whole = fill_from_page(item, page, page_title=content.title if content else "")
    needed = len(body) if item.partial else max(THIN_CONTENT_CHARS, len(body) * 2)
    text = markdown if markdown and len(markdown) > needed else None
    if text is None and (item.partial or whole is item):
        return None
    return whole, text if text is not None else body
