# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""WordPress WXR (eXtended RSS) export parsing.

WXR is the ``.xml`` file WordPress produces at Tools -> Export: RSS 2.0
plus a ``wp:`` namespace carrying every post, page, draft, attachment,
and nav-menu item of a site. It is NOT a live feed (a live ``/feed/``
never carries the ``wp:`` namespace), so detection can never misfire on
an ordinary feed; it arrives as a pasted or uploaded file.

Only real content the user meant to publish is kept: ``wp:post_type``
of ``post`` (and, by default, ``page``) with ``wp:status`` of
``publish``. Attachments, nav menus, drafts, and scheduled posts are
skipped.

Parsed with a CDATA-masking regex scan: ``content:encoded`` bodies are
CDATA-wrapped HTML that routinely contains ``<tag>`` markup and can
even contain a literal ``</item>``, which would tear a naive structural
scan. Masking every CDATA section behind a placeholder first makes the
structure scan safe; fields are unmasked on extraction, and the XML
entities of a field outside CDATA decoded (a title read
"Tom &amp; Jerry").

A post's body in the export is what its author typed, not the HTML its
page shows: WordPress makes paragraphs of the blank lines of a classic
post (``wpautop``) and expands its shortcodes (``[caption]``,
``[gallery]``, ``[embed]``, ``[video]``) only when the page is shown.
:func:`post_html` does both, so a classic post keeps its paragraphs
and its captions and galleries show. The featured image (the post's
``_thumbnail_id``, an attachment item of the same export) is the cover,
the author is the display name the export lists for the login in
``dc:creator``, and tags come with categories.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from ...rss.dates import to_unix
from ...rss.parser import FeedItem


_WP_NS_RE = re.compile(
    r"xmlns:wp\s*=\s*[\"']https?://wordpress\.org/export/", re.IGNORECASE)
_WXR_VERSION_RE = re.compile(r"<wp:wxr_version[\s>]", re.IGNORECASE)

_ITEM_RE = re.compile(r"<item\b[^>]*>([\s\S]*?)</item>", re.IGNORECASE)
_CATEGORY_RE = re.compile(
    r"<category\b([^>]*)>([\s\S]*?)</category>", re.IGNORECASE)
_TITLE_RE = re.compile(r"<title\b[^>]*>([\s\S]*?)</title>", re.IGNORECASE)
_CDATA_RE = re.compile(r"<!\[CDATA\[([\s\S]*?)\]\]>")
_POSTMETA_RE = re.compile(r"<wp:postmeta\b[^>]*>([\s\S]*?)</wp:postmeta>", re.IGNORECASE)
_AUTHOR_RE = re.compile(r"<wp:author\b[^>]*>([\s\S]*?)</wp:author>", re.IGNORECASE)
_ATTRIBUTE_RE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'\]>]+))""")


def is_wxr(text: Optional[str]) -> bool:
    """Cheap, specific "is this a WordPress export?" test."""
    if not isinstance(text, str):
        return False
    head = text[:4000]
    return bool(_WP_NS_RE.search(head) or _WXR_VERSION_RE.search(head))


def _mask_cdata(text: str) -> Tuple[str, Callable[[str], str]]:
    """Stash CDATA sections behind placeholders; returns (masked, unmask).

    ``unmask`` decodes the XML entities of what lies outside the CDATA
    sections and restores the sections as written."""
    blocks: List[str] = []

    def _stash(match: re.Match) -> str:
        blocks.append(match.group(1))
        return f"\x00C{len(blocks) - 1}\x00"

    masked = _CDATA_RE.sub(_stash, str(text))

    def _unmask(value: str) -> str:
        return re.sub(
            r"\x00C(\d+)\x00",
            lambda m: blocks[int(m.group(1))]
            if int(m.group(1)) < len(blocks) else "",
            html.unescape(str(value)),
        )

    return masked, _unmask


def _field(body: str, name: str, unmask: Callable[[str], str]) -> str:
    match = re.search(
        rf"<{name}\b[^>]*>([\s\S]*?)</{name}>", body, re.IGNORECASE)
    return unmask(match.group(1)).strip() if match else ""


def _attributes(text: str) -> dict:
    return {m.group(1).lower(): html.unescape(m.group(2) if m.group(2) is not None
                                             else m.group(3) if m.group(3) is not None
                                             else m.group(4))
            for m in _ATTRIBUTE_RE.finditer(text or "")}


@dataclass(frozen=True)
class WxrExport:
    title: str
    items: Tuple[FeedItem, ...]


def parse_wxr(text: str, *, include_pages: bool = True) -> WxrExport:
    """Parse WXR text into published posts (and pages) as ``FeedItem``s.

    The body is made the HTML its page shows (:func:`post_html`) and
    stays in ``content_html`` so the pipeline's normal HTML-to-Markdown
    pass handles it. Never throws on malformed input; unknown shapes just
    yield fewer items.
    """
    masked, unmask = _mask_cdata(text or "")

    title_match = _TITLE_RE.search(masked)
    export_title = unmask(title_match.group(1)).strip() if title_match else ""

    authors = {}
    for match in _AUTHOR_RE.finditer(masked):
        login = _field(match.group(1), "wp:author_login", unmask)
        name = _field(match.group(1), "wp:author_display_name", unmask)
        if login and name:
            authors[login] = name

    bodies = [m.group(1) for m in _ITEM_RE.finditer(masked)]
    attachments = {}
    for body in bodies:
        if _field(body, "wp:post_type", unmask) == "attachment":
            url = _field(body, "wp:attachment_url", unmask) or _field(body, "guid", unmask)
            post_id = _field(body, "wp:post_id", unmask)
            if post_id and url:
                attachments[post_id] = url

    items: List[FeedItem] = []
    seen: set = set()
    for body in bodies:
        if _field(body, "wp:status", unmask) != "publish":
            continue
        post_type = _field(body, "wp:post_type", unmask)
        if post_type != "post" and not (include_pages and post_type == "page"):
            continue

        post_id = _field(body, "wp:post_id", unmask)
        post_name = _field(body, "wp:post_name", unmask)
        link = _field(body, "link", unmask)
        item_title = _field(body, "title", unmask)
        guid = f"wp-{post_id}" if post_id else (post_name or link or item_title)
        if not guid or guid in seen:
            continue
        seen.add(guid)

        categories: List[str] = []
        for cat in _CATEGORY_RE.finditer(body):
            attributes = _attributes(cat.group(1))
            if attributes.get("domain", "category") not in ("category", "post_tag"):
                continue  # post formats, products' own taxonomies
            if attributes.get("nicename") == "uncategorized":
                continue
            name = unmask(cat.group(2)).strip()
            if name and name not in categories:
                categories.append(name)

        meta = {}
        for block in _POSTMETA_RE.finditer(body):
            key = _field(block.group(1), "wp:meta_key", unmask)
            if key:
                meta.setdefault(key, _field(block.group(1), "wp:meta_value", unmask))
        creator = _field(body, "dc:creator", unmask)

        items.append(FeedItem(
            guid=guid,
            title=item_title or post_name or "Untitled",
            link=link or None,
            summary=_field(body, "excerpt:encoded", unmask) or None,
            content_html=post_html(_field(body, "content:encoded", unmask),
                                   attachments=attachments),
            # The post's time in UTC; pubDate (RFC 822) for a row that
            # has none ("0000-00-00 00:00:00").
            published_at=(to_unix(_field(body, "wp:post_date_gmt", unmask))
                          or to_unix(_field(body, "pubDate", unmask))),
            categories=tuple(categories),
            image=attachments.get(meta.get("_thumbnail_id", "")) or None,
            author=authors.get(creator, creator) or None,
        ))
    return WxrExport(title=export_title, items=tuple(items))


# -- a post's body as its page shows it ---------------------------------------- #

_BLOCK_START = re.compile(
    r"^\s*<(p|div|h[1-6]|ul|ol|li|dl|blockquote|pre|table|thead|tbody|tr|figure|"
    r"figcaption|hr|form|address|section|article|aside|header|footer|nav|details|"
    r"iframe|video|audio|!--)\b", re.IGNORECASE)
_PRE = re.compile(r"<pre\b[\s\S]*?</pre>", re.IGNORECASE)
_SHORTCODE = re.compile(r"\[(\[?)(/?)([A-Za-z][\w-]*)([^\]]*?)(/?)\](\]?)")
_CAPTION = re.compile(r"\[(caption|wp_caption)\b([^\]]*)\]([\s\S]*?)\[/\1\]", re.IGNORECASE)
_EMBED = re.compile(r"\[embed\b[^\]]*\]([\s\S]*?)\[/embed\]", re.IGNORECASE)
_MEDIA = re.compile(r"\[(video|audio|playlist)\b([^\]]*)\](?:[\s\S]*?\[/\1\])?",
                    re.IGNORECASE)
_GALLERY = re.compile(r"\[gallery\b([^\]]*)\]", re.IGNORECASE)
# Jetpack's embed shortcodes: [youtube URL], [youtube=URL], [vimeo ID],
# [tweet ID], [soundcloud url="..."], [spotify id="spotify:track:ID"].
_URL_SHORTCODE = re.compile(r"\[(youtube|vimeo|soundcloud|spotify|tweet)(?:=|\s+)([^\]]*)\]",
                            re.IGNORECASE)
_JETPACK_SIZE = re.compile(r"&(?:w|h)=\d+")
_CODE = re.compile(r"<(pre|code)\b[\s\S]*?</\1>", re.IGNORECASE)
_CAPTION_IMAGE = re.compile(r"^\s*((?:<a\b[^>]*>\s*)?<img\b[^>]*>(?:\s*</a>)?)\s*([\s\S]*)$",
                            re.IGNORECASE)


def post_html(raw: str, *, attachments: Optional[dict] = None) -> str:
    """A post's stored body as the HTML its page shows: shortcodes
    expanded or, when unknown (a page builder's), their tags dropped and
    their words kept; a classic post's blank lines made paragraphs. Code
    (``<pre>``, ``<code>``) and a shortcode written as text (``[[name]]``)
    are left as they are."""
    if not raw or not raw.strip():
        return ""
    attachments = attachments or {}
    kept: List[str] = []

    def _aside(text: str) -> str:
        kept.append(text)
        return f"\x00K{len(kept) - 1}\x00"

    text = _CODE.sub(lambda m: _aside(m.group(0)), raw)
    # [[name]] is how WordPress writes a shortcode as text: it shows as
    # written, less one pair of brackets.
    text = re.sub(r"\[(\[[^\[\]]+\])\]", lambda m: _aside(m.group(1)), text)
    text = _CAPTION.sub(_caption, text)
    text = _EMBED.sub(lambda m: _link_line(m.group(1).strip()), text)
    text = _URL_SHORTCODE.sub(lambda m: _jetpack_embed(m) or _aside(m.group(0)), text)
    text = _MEDIA.sub(_media, text)
    text = _GALLERY.sub(lambda m: _gallery(m.group(1), attachments), text)
    closed = {m.group(1).lower() for m in re.finditer(r"\[/([A-Za-z][\w-]*)\]", text)}
    text = _SHORTCODE.sub(lambda m: _drop_shortcode(m, closed), text)
    text = re.sub(r"\x00K(\d+)\x00", lambda m: kept[int(m.group(1))], text)
    if "<!-- wp:" not in text:
        text = autop(text)
    return text.strip()


def autop(text: str) -> str:
    """WordPress's ``wpautop``, as much of it as an import needs: blank
    lines part paragraphs, a single line break inside one is a ``<br>``,
    and blocks (``<pre>`` kept whole) are left as they are."""
    kept: List[str] = []

    def _keep(match: re.Match) -> str:
        kept.append(match.group(0))
        return f"\x00P{len(kept) - 1}\x00"

    text = _PRE.sub(_keep, text.replace("\r\n", "\n").replace("\r", "\n"))
    out = []
    for chunk in re.split(r"\n\s*\n", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith("\x00P") or _BLOCK_START.match(chunk):
            out.append(chunk)
        else:
            out.append("<p>" + re.sub(r"\s*\n\s*", "<br>\n", chunk) + "</p>")
    joined = "\n\n".join(out)
    return re.sub(r"\x00P(\d+)\x00", lambda m: kept[int(m.group(1))], joined)


def _caption(match: re.Match) -> str:
    inner = match.group(3)
    attributes = _attributes(match.group(2))
    found = _CAPTION_IMAGE.match(inner)
    if not found:
        return inner
    image, caption = found.group(1), (found.group(2) or attributes.get("caption", "")).strip()
    if not caption:
        return f"<figure>{image}</figure>"
    return f"<figure>{image}<figcaption>{caption}</figcaption></figure>"


def _jetpack_embed(match: re.Match) -> str:
    """The page a Jetpack embed shortcode shows, as a line with its
    address, or "" for a form not read here (the caller keeps it as
    written)."""
    name, rest = match.group(1).lower(), match.group(2).strip()
    tokens = rest.split()
    if tokens and tokens[0].startswith(("http://", "https://")):
        # Jetpack reads its own size options off the address:
        # [youtube https://youtu.be/ID&w=640&h=360].
        return _link_line(_JETPACK_SIZE.sub("", tokens[0]))
    attributes = _attributes(rest)
    value = (attributes.get("url") or attributes.get("id")
             or (tokens[0] if tokens and "=" not in tokens[0] else "")).strip()
    if value.startswith(("http://", "https://")):
        return _link_line(value)
    if name == "vimeo" and value.isdigit():
        return _link_line(f"https://vimeo.com/{value}")
    if name == "tweet" and value.isdigit():
        return _link_line(f"https://twitter.com/i/web/status/{value}")
    if name == "spotify" and value.startswith("spotify:"):
        kind, _colon, key = value[len("spotify:"):].partition(":")
        if kind and key:
            return _link_line(f"https://open.spotify.com/{kind}/{key}")
    return ""


def _media(match: re.Match) -> str:
    attributes = _attributes(match.group(2))
    # The address attributes of WordPress's [video] and [audio].
    for key in ("src", "mp4", "m4v", "webm", "ogv", "flv", "mp3", "m4a", "ogg", "wav", "flac"):
        if attributes.get(key, "").startswith(("http://", "https://")):
            return _link_line(attributes[key])
    return ""


def _gallery(attribute_text: str, attachments: dict) -> str:
    ids = [i.strip() for i in _attributes(attribute_text).get("ids", "").split(",")]
    images = [f'<img src="{html.escape(attachments[i], quote=True)}">'
              for i in ids if i in attachments]
    return "<p>" + "".join(images) + "</p>" if images else ""


def _drop_shortcode(match: re.Match, closed: set) -> str:
    """A shortcode nothing above expanded (a page builder's, a plugin's)
    loses its tags and keeps the words between them. Only what is surely
    a shortcode: attributes, a builder-style name (``et_pb_text``,
    ``vc_row``) or a closing tag in the post. "[see below]" and
    "[e-mail]" are text."""
    _open, closing, name, attributes, _self, _close = match.groups()
    if closing or "=" in attributes or "_" in name or name.lower() in closed:
        return ""
    return match.group(0)


def _link_line(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return ""
    safe = html.escape(url, quote=True)
    return f'<p><a href="{safe}">{safe}</a></p>'
