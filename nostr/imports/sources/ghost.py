# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ghost export parsing.

Unlike Medium/Substack (ZIPs), a Ghost export is a single JSON file
downloaded from Settings -> Import/Export. Structure (Ghost developer
docs + real exports):

    { "db": [ { "meta": {...}, "data": { "posts": [ {
        "title", "slug", "html", "status", "published_at",
        "feature_image", "custom_excerpt", ... } ] } } ] }

``status`` is ``published`` / ``draft`` / ``scheduled``; ``published_at``
is an ISO string (older exports use epoch ms). The rendered body lives
in ``html``; posts carrying only ``mobiledoc``/``lexical`` and no
``html`` are skipped (converting those formats is a separate, heavy
concern). Only published posts are kept. Never throws.

Since Ghost 4 the export writes the blog's own address as the
placeholder ``__GHOST_URL__`` (``__GHOST_URL__/content/images/a.jpg``)
and does not say what it stands for: :func:`needs_site_address` tells
the importer to ask once, and ``site_url`` fills it in. With it each
post also gets its own address (``site/slug/``), for its attribution
line and the relative links in it. Tags (public ones; ``#internal``
tags stay inside Ghost) and authors come from the export's join tables,
and the post's meta or Open Graph description stands in for a missing
excerpt.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from i18n import _

from ...rss.parser import FeedItem


GHOST_URL = "__GHOST_URL__"


def needs_site_address(text: Optional[str]) -> bool:
    """Whether a Ghost export writes the blog's address as a placeholder
    (its pictures and links point nowhere without the real one)."""
    return is_ghost_export(text) and GHOST_URL in (text or "")


def is_ghost_export(text: Optional[str]) -> bool:
    """Cheap "is this a Ghost export JSON?" test."""
    if not isinstance(text, str):
        return False
    return bool(
        re.search(r'"db"\s*:\s*\[', text[:4000])
        and re.search(r'"posts"\s*:', text[:200000])
    )


def _to_unix(value) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        # Older exports use epoch milliseconds.
        seconds = float(value) / 1000 if value > 10**12 else float(value)
        return int(seconds)
    raw = str(value).strip()
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except ValueError:
        return None


@dataclass(frozen=True)
class GhostExport:
    title: str
    items: Tuple[FeedItem, ...]


def parse_ghost_export(text: str, *, site_url: str = "") -> GhostExport:
    """Published, html-bodied posts as ``FeedItem``s. ``site_url`` is the
    blog's address, for the ``__GHOST_URL__`` placeholder and each post's
    own address. Never throws."""
    try:
        payload = json.loads(text or "")
    except (ValueError, TypeError):
        return GhostExport(title="", items=())

    db = payload.get("db") if isinstance(payload, dict) else None
    root = db[0] if isinstance(db, list) and db else payload
    data = root.get("data") if isinstance(root, dict) else None
    posts = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(posts, list):
        return GhostExport(title="", items=())

    site = (site_url or "").strip().rstrip("/")
    whole = lambda value: str(value or "").replace(GHOST_URL, site).strip()  # noqa: E731
    tags = _joined(data, "tags", "posts_tags", "tag_id",
                   lambda tag: "" if str(tag.get("name") or "").startswith("#")
                   or tag.get("visibility") == "internal" else str(tag.get("name") or ""))
    authors = _joined(data, "users", "posts_authors", "author_id",
                      lambda user: str(user.get("name") or ""))
    descriptions = {}
    for meta in data.get("posts_meta") or ():
        if isinstance(meta, dict) and meta.get("post_id"):
            descriptions[meta["post_id"]] = (meta.get("meta_description")
                                             or meta.get("og_description") or "")

    items: List[FeedItem] = []
    seen: set = set()
    for post in posts:
        if not isinstance(post, dict):
            continue
        if post.get("status") and post.get("status") != "published":
            continue
        content_html = whole(post.get("html"))
        if not content_html:
            continue  # mobiledoc/lexical-only posts need a converter we don't have
        guid = "ghost-" + str(
            post.get("id") or post.get("uuid") or post.get("slug")
            or post.get("title") or len(items)
        )
        if guid in seen:
            continue
        seen.add(guid)
        post_id = post.get("id")
        slug = str(post.get("slug") or "").strip()
        # A post first published elsewhere names that page; any other
        # lives at the blog's address and its slug.
        canonical = whole(post.get("canonical_url"))
        if canonical.startswith(("http://", "https://")):
            link = canonical
        elif site.startswith(("http://", "https://")) and slug:
            link = f"{site}/{slug}/"
        else:
            link = ""
        items.append(FeedItem(
            guid=guid,
            title=str(post.get("title") or "Untitled").strip(),
            link=link or None,
            summary=(str(post.get("custom_excerpt") or "").strip()
                     or str(descriptions.get(post_id) or "").strip() or None),
            content_html=content_html,
            published_at=_to_unix(post.get("published_at")),
            categories=tuple(t for t in tags.get(post_id, ()) if t),
            image=whole(post.get("feature_image")) or None,
            author=", ".join(a for a in authors.get(post_id, ()) if a) or None,
        ))
    return GhostExport(title=_("Ghost export"), items=tuple(items))


def _joined(data: dict, table: str, join: str, key: str, value) -> dict:
    """Post id to the values of ``table`` that ``join`` links it to, in
    the export's own order (``sort_order``)."""
    rows = {row.get("id"): row for row in data.get(table) or () if isinstance(row, dict)}
    links = sorted((row for row in data.get(join) or () if isinstance(row, dict)),
                   key=lambda row: row.get("sort_order") or 0)
    found: dict = {}
    for row in links:
        target = rows.get(row.get(key))
        if target is not None:
            found.setdefault(row.get("post_id"), []).append(value(target))
    return found
