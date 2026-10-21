# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Platform export archives (ZIP): Medium and Substack.

These are the ``.zip`` a writer downloads when leaving a platform. Both
carry HTML post bodies; the structures match Ghost's official migrators
(@tryghost/mg-medium-export, @tryghost/mg-substack):

  Medium    ``posts/*.html`` with microformat classes: ``.p-name``
            (title), ``.e-content`` (body), ``.p-summary`` (subtitle),
            ``.dt-published[datetime]``, ``.p-canonical[href]``,
            ``.p-author``. Files whose basename starts with ``draft``
            are drafts. The body repeats the title and subtitle
            (``.graf--title``, ``.graf--subtitle``) after a section
            divider; those are taken out, as the draft has its own.
  Substack  ``posts.csv`` (post_id, post_date, is_published, type, ...,
            title, subtitle, podcast_url) joined by ``post_id``
            (``<number>.<slug>``) to ``posts/<post_id>.html`` bodies.
            The subscribe and share buttons Substack puts in a body are
            taken out; a podcast's audio comes along as the episode.

Unzipping and CSV both come from the stdlib; Medium's microformats are
read with lxml (already a dependency via readability). Only *published*
posts are kept; bodies stay HTML for the pipeline's HTML-to-Markdown
pass.

Bounded: archives above ``MAX_ARCHIVE_BYTES`` and members above
``MAX_MEMBER_BYTES`` are rejected/skipped so a hostile ZIP cannot
exhaust memory (zip bombs decompress far larger than they store).
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple

import lxml.html

from i18n import _

from ...rss.parser import FeedItem
from .podcast import PodcastEpisode


MAX_ARCHIVE_BYTES = 256 * 1024 * 1024   # decompressed total across members
MAX_MEMBER_BYTES = 32 * 1024 * 1024     # any single member


class ArchiveError(Exception):
    """The archive can't be read (not a ZIP, or over the size bounds)."""


@dataclass(frozen=True)
class ArchiveResult:
    platform: Optional[str]  # "medium" | "substack" | None
    title: str
    items: Tuple[FeedItem, ...]


def looks_like_zip(data: bytes) -> bool:
    return isinstance(data, (bytes, bytearray)) and bytes(data[:4]) in (
        b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def _to_unix(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
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


def _read_member(zf: zipfile.ZipFile, name: str) -> Optional[bytes]:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_MEMBER_BYTES:
        return None
    return zf.read(name)


def detect_archive(names: List[str]) -> Optional[str]:
    if any(n == "posts.csv" or n.endswith("/posts.csv") for n in names):
        return "substack"
    if any(re.search(r"(^|/)posts/.*\.html$", n, re.IGNORECASE) for n in names):
        return "medium"
    return None


# --------------------------------------------------------------------------- #
# Medium                                                                      #
# --------------------------------------------------------------------------- #

def _class_xpath(class_name: str) -> str:
    return f"contains(concat(' ', normalize-space(@class), ' '), ' {class_name} ')"


def _by_class(doc, class_name: str):
    matches = doc.xpath(f"//*[{_class_xpath(class_name)}]")
    return matches[0] if matches else None


def _all_by_class(node, class_name: str) -> list:
    return node.xpath(f".//*[{_class_xpath(class_name)}]")


def _inner_html(node) -> str:
    parts = [node.text or ""]
    for child in node:
        parts.append(lxml.html.tostring(child, encoding="unicode"))
    return "".join(parts).strip()


def _text_of(node) -> str:
    return re.sub(r"\s+", " ", node.text_content()).strip() if node is not None else ""


def _drop(nodes: Iterable) -> None:
    for node in list(nodes):
        if node.getparent() is not None:
            node.drop_tree()


def _drop_leading_divider(content) -> None:
    """Medium opens every section with a divider (``<hr
    class="section-divider">`` in its own ``div``). Between sections it
    marks a break; before the first words it is a rule on top of the
    article, so that one goes."""
    if (content.text or "").strip():
        return
    for node in content.iter():
        if node is content or not isinstance(node.tag, str):
            continue               # the body itself, a comment
        if "section-divider" in (node.get("class") or "").split():
            _drop([node])
            return
        if (node.text or "").strip() or node.tag in ("img", "figure", "iframe"):
            return


def _medium_post(doc, name: str) -> Optional[FeedItem]:
    content_node = _by_class(doc, "e-content")
    if content_node is None:
        return None
    subtitle = _text_of(next(iter(_all_by_class(content_node, "graf--subtitle")), None))
    # The title and subtitle are the draft's own details, not its text.
    _drop(_all_by_class(content_node, "graf--title"))
    _drop(_all_by_class(content_node, "graf--subtitle"))
    _drop_leading_divider(content_node)
    cover = next((img.get("src") for img in content_node.xpath(".//figure//img")
                  if (img.get("src") or "").startswith(("http://", "https://"))), None)
    content_html = _inner_html(content_node)
    if not content_html:
        return None
    title_node = _by_class(doc, "p-name")
    published_node = _by_class(doc, "dt-published")
    canonical_node = _by_class(doc, "p-canonical")
    canonical = (canonical_node.get("href") or "").strip() if canonical_node is not None else ""
    tags = tuple(text for text in (_text_of(node) for node in _all_by_class(doc, "p-category"))
                 if text)
    return FeedItem(
        guid=canonical or name,
        title=_text_of(title_node) or "Untitled",
        link=canonical or None,
        summary=subtitle or _text_of(_by_class(doc, "p-summary")) or None,
        content_html=content_html,
        published_at=_to_unix(
            published_node.get("datetime") if published_node is not None else None),
        categories=tags,
        image=cover,
        author=_text_of(_by_class(doc, "p-author")) or None,
    )


def _extract_medium(zf: zipfile.ZipFile) -> List[FeedItem]:
    items: List[FeedItem] = []
    for name in zf.namelist():
        if not re.search(r"(^|/)posts/.*\.html$", name, re.IGNORECASE):
            continue
        base = name.rsplit("/", 1)[-1]
        if base.lower().startswith("draft"):
            continue  # Medium drafts
        data = _read_member(zf, name)
        if data is None:
            continue
        try:
            doc = lxml.html.document_fromstring(
                data.decode("utf-8", errors="replace"))
        except (lxml.etree.ParserError, ValueError):
            continue
        item = _medium_post(doc, name)
        if item is not None:
            items.append(item)
    return items


# --------------------------------------------------------------------------- #
# Substack                                                                    #
# --------------------------------------------------------------------------- #

# What Substack puts in a body that is the newsletter's, not the post's:
# subscribe forms, "Subscribe now" / "Share" / "Leave a comment" buttons,
# the "get the app" box. Matched as the start of a class name.
_SUBSTACK_FURNITURE = ("subscription-widget", "subscribe-widget", "button-wrapper",
                       "captioned-button-wrap", "install-substack-app", "share-dialog")
# A link to a post or to the subscribe page names the publication's
# own address (a custom domain included).
_SUBSTACK_OWN_LINK = re.compile(
    r"""href=["']https?://([a-z0-9.-]+)/(?:subscribe|p/)""", re.IGNORECASE)
_NOT_A_PUBLICATION = frozenset({"substack.com", "www.substack.com", "open.substack.com"})
_EMAIL_LIST = re.compile(r"(?:^|/)email_list\.([a-z0-9-]+)\.csv$", re.IGNORECASE)


def _without_substack_furniture(html: str) -> Tuple[str, Optional[str]]:
    """A Substack body without its furniture, and its first image (the
    cover, as Substack's own feed has it)."""
    try:
        root = lxml.html.fragment_fromstring(html, create_parent="div")
    except (lxml.etree.ParserError, ValueError):
        return html, None
    condition = " or ".join(
        f"starts-with(normalize-space(@class), '{name}') or contains(@class, ' {name}')"
        for name in _SUBSTACK_FURNITURE)
    _drop(root.xpath(f".//*[{condition}]"))
    _drop(root.xpath(f".//a[{_class_xpath('button')}]"))
    cover = next((img.get("src") for img in root.iter("img")
                  if (img.get("src") or "").startswith(("http://", "https://"))), None)
    return _inner_html(root), cover


def _publication_address(bodies: Iterable[str], names: Iterable[str]) -> Optional[str]:
    """The publication's own host, where its posts live: the one its
    bodies link to for posts and subscribing most often, or else the
    ``<name>.substack.com`` its subscriber list is named after."""
    hosts = Counter(
        host.lower() for body in bodies for host in _SUBSTACK_OWN_LINK.findall(body)
        if host.lower() not in _NOT_A_PUBLICATION)
    if hosts:
        return hosts.most_common(1)[0][0]
    for name in names:
        found = _EMAIL_LIST.search(name)
        if found:
            return f"{found.group(1).lower()}.substack.com"
    return None


def _substack_link(post_id: str, host: Optional[str]) -> Optional[str]:
    """``https://<host>/p/<slug>``: the slug follows the number in the
    post's id (``123456.my-post``)."""
    number, _dot, slug = post_id.partition(".")
    if not host or not slug.strip() or not number.isdigit():
        return None
    return f"https://{host}/p/{slug.strip()}"


def _extract_substack(zf: zipfile.ZipFile) -> List[FeedItem]:
    csv_name = next(
        (n for n in zf.namelist()
         if n == "posts.csv" or n.endswith("/posts.csv")),
        None,
    )
    if csv_name is None:
        return []
    data = _read_member(zf, csv_name)
    if data is None:
        return []
    rows = list(csv.DictReader(
        io.StringIO(data.decode("utf-8", errors="replace"))))

    body_by_id = {}
    for name in zf.namelist():
        if not re.search(r"(^|/)posts/.*\.html$", name, re.IGNORECASE):
            continue
        post_id = name.rsplit("/", 1)[-1][: -len(".html")]
        body_by_id[post_id] = name

    published: List[Tuple[dict, str]] = []
    for row in rows:
        if str(row.get("is_published", "")).upper() != "TRUE":
            continue
        member = body_by_id.get(str(row.get("post_id") or ""))
        content_html = ""
        if member:
            data = _read_member(zf, member)
            if data is not None:
                content_html = data.decode("utf-8", errors="replace").strip()
        if not content_html and not row.get("podcast_url"):
            continue
        published.append((row, content_html))
    host = _publication_address((body for _row, body in published), zf.namelist())

    items: List[FeedItem] = []
    for row, content_html in published:
        post_id = str(row.get("post_id") or "")
        cover = None
        if content_html:
            content_html, cover = _without_substack_furniture(content_html)
        audio = str(row.get("podcast_url") or "").strip()
        items.append(FeedItem(
            guid=f"substack-{post_id}",
            title=str(row.get("title") or "Untitled").strip(),
            link=_substack_link(post_id, host),
            summary=str(row.get("subtitle") or "").strip() or None,
            content_html=content_html,
            published_at=_to_unix(row.get("post_date")),
            # The CSV's "type" (newsletter, podcast, thread) says what
            # Substack made of the post, not what it is about.
            categories=(),
            image=cover,
            author=None,
            # A podcast episode's body is its audio, then its notes.
            podcast=(PodcastEpisode(audio=audio)
                     if audio.startswith(("http://", "https://")) else None),
        ))
    return items


# --------------------------------------------------------------------------- #
# Entry                                                                       #
# --------------------------------------------------------------------------- #

def extract_archive(data: bytes) -> ArchiveResult:
    """Unzip and extract a platform export archive.

    Raises :class:`ArchiveError` when the bytes aren't a readable ZIP
    or blow the size bounds; returns ``platform=None`` when the ZIP is
    valid but matches no known export layout.
    """
    if not looks_like_zip(data):
        raise ArchiveError("Not a ZIP archive")
    try:
        zf = zipfile.ZipFile(io.BytesIO(bytes(data)))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArchiveError(f"Could not read that archive: {exc}") from exc
    with zf:
        total = sum(info.file_size for info in zf.infolist())
        if total > MAX_ARCHIVE_BYTES:
            raise ArchiveError("That archive is too large to import")
        platform = detect_archive(zf.namelist())
        if platform is None:
            return ArchiveResult(platform=None, title="", items=())
        items = (
            _extract_substack(zf) if platform == "substack"
            else _extract_medium(zf)
        )
        title = _("Substack export") if platform == "substack" else _("Medium export")
        return ArchiveResult(
            platform=platform, title=title, items=tuple(items))
