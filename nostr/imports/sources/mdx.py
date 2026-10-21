# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MDX / Markdown to clean Markdown conversion.

MDX is Markdown with JSX mixed in (Astro/Next content). Importing it
into a long-form draft means producing plain, portable Markdown while
losing as little structure as possible: headings, prose, emphasis,
lists, code, and tables survive.

Pipeline (all pure, no I/O):

  1. Split off and parse the front matter: the title, and the details a
     static site keeps there (date, tags, description, cover, author).
  2. Protect code (fenced blocks, indented blocks in a plain Markdown
     file, inline code spans) so nothing below rewrites it.
  3. Drop ESM ``import`` / ``export`` statements.
  4. Neutralise MDX expressions: the ``{' '}`` whitespace token becomes
     a space; JSX comment expressions are removed.
  5. Turn the embed components sites use most (``<YouTube>``,
     ``<Vimeo>``, ``<Tweet>``, ``<Image>``) into what a Nostr client
     shows: a line with the video's address, or a Markdown image. Then
     remove the other JSX *component* tags (uppercase-named, plus
     ``<Fragment>``), keeping their children.
  6. Convert the remaining HTML islands (blank-line-delimited blocks
     that start with a block-level tag) to Markdown via the shared
     converter; tidy stray inline HTML inside Markdown blocks. Whole-
     document conversion would escape every Markdown construct, so only
     the HTML blocks are converted, which lines up with CommonMark's own
     rule that an HTML block is delimited by blank lines.
  7. Make relative images whole against the document's own address,
     restore code and tidy whitespace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

import front_matter

from ...rss.dates import to_unix

from ...rss.markdown import code_spans, image_destinations, protect, restore
from ...rss.normalize import html_to_markdown
from ...rss.urls import is_web, link_target, whole_url


# --------------------------------------------------------------------------- #
# Frontmatter                                                                 #
# --------------------------------------------------------------------------- #

def parse_frontmatter(raw: str) -> Tuple[Dict[str, str], str]:
    """Split ``---`` front matter off the top (front_matter.split, the one
    reader of it): its values as texts, a list's items joined by commas."""
    values, _mapping, body = _split_frontmatter(raw)
    return values, body


def _split_frontmatter(raw: str) -> Tuple[Dict[str, str], Mapping[str, Any], str]:
    """The front matter's values as texts, as a YAML (or TOML) parser
    reads them (dates as dates, ``cover:`` with ``image`` under it as a
    mapping), and the body after it."""
    front, body = front_matter.split(str(raw or ""))
    if front is None:
        return {}, {}, str(raw or "")
    values: Dict[str, str] = {}
    for item in front.entries:
        value = item.value
        if item.key and value is not None:
            values[item.key] = value if isinstance(value, str) else ", ".join(value)
    return values, front_matter.parsed(front) or {}, body


# The keys static site generators keep a post's details under, as their
# documentation and starter blogs write them: Jekyll, Hugo (publishDate,
# images, PaperMod's cover.image, Ananke's featured_image), Astro (pubDate, heroImage), Eleventy,
# Gatsby, Next.js (coverImage, excerpt, author.name), Docusaurus
# (keywords, inline authors), dev.to (cover_image). Best first.
_DATE_KEYS = ("date", "pubDate", "publishDate")
_TAG_KEYS = ("tags", "categories", "category")
_SUMMARY_KEYS = ("description", "summary", "excerpt")
_IMAGE_KEYS = ("image", "cover", "heroImage", "coverImage", "cover_image", "featured_image",
               "images")


@dataclass(frozen=True)
class _Details:
    published_at: Optional[int] = None
    categories: Tuple[str, ...] = ()
    summary: str = ""
    image: str = ""
    author: str = ""


def _details(values: Mapping[str, str], mapping: Mapping[str, Any]) -> _Details:
    """A post's details from its front matter, whatever the generator
    called them. The parsed mapping is read first, as it holds what the
    texts cannot (a date, Hugo's ``cover: {image: ...}``); the texts
    stand in for a front matter a YAML parser refuses."""
    lowered = {str(key).lower(): value for key, value in (mapping or {}).items()}
    texts = {str(key).lower(): value for key, value in (values or {}).items()}

    def first(keys: Tuple[str, ...]) -> Any:
        for key in keys:
            for source in (lowered, texts):
                value = source.get(key.lower())
                if value not in (None, "", [], {}):
                    return value
        return None

    tags: List[str] = []
    for key in _TAG_KEYS:
        tags += _texts(lowered.get(key.lower(), texts.get(key.lower())))
    return _Details(
        published_at=to_unix(first(_DATE_KEYS)),
        categories=tuple(tags or _texts(first(("keywords",)))),
        summary=_plain(first(_SUMMARY_KEYS)),
        image=_image_of(first(_IMAGE_KEYS)),
        author=", ".join(_names(first(("author", "authors")))),
    )


def _plain(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _texts(value: Any) -> List[str]:
    """A list of tags as written: a YAML list (of texts, or of mappings
    with a ``label`` as Docusaurus writes them), or a text of comma-
    separated ones."""
    if isinstance(value, (list, tuple)):
        return [text for item in value
                for text in _texts(item.get("label") if isinstance(item, Mapping) else item)]
    if isinstance(value, (str, int, float)):
        return [part.strip() for part in str(value).split(",") if part.strip()]
    return []


def _names(value: Any) -> List[str]:
    """Author names: a text, a list of them, or a mapping with a name."""
    if isinstance(value, Mapping):
        return _names(value.get("name"))
    if isinstance(value, (list, tuple)):
        return [name for item in value for name in _names(item)]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _image_of(value: Any) -> str:
    """The cover's address, however it is written: a text, the first of
    a list (``images:``), or a mapping with it under ``image``, ``src``
    or ``url`` (Hugo PaperMod's ``cover: {image: ...}``)."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return next((found for found in map(_image_of, value) if found), "")
    if isinstance(value, Mapping):
        return next((found for found in (_image_of(value.get(key))
                                          for key in ("image", "src", "url"))
                     if found), "")
    return ""


# --------------------------------------------------------------------------- #
# Code protection                                                             #
# --------------------------------------------------------------------------- #

_INDENTED_RE = re.compile(r"^(?: {4}|\t)")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")


def _indented_code_spans(text: str) -> List[Tuple[int, int]]:
    """Markdown's indented code blocks: lines indented by four spaces (or
    a tab) after a blank line, where no list item is going on. MDX has no
    indented code (its indentation nests JSX), so this is read for a
    plain Markdown file only."""
    lines = text.split("\n")
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    spans: List[Tuple[int, int]] = []
    previous = ""                      # the last line with text
    blank_before = True
    index = 0
    while index < len(lines):
        line = lines[index]
        if (_INDENTED_RE.match(line) and line.strip() and blank_before
                and not _LIST_ITEM_RE.match(previous) and not _INDENTED_RE.match(previous)):
            end = index
            while end < len(lines) and (_INDENTED_RE.match(lines[end]) or not lines[end].strip()):
                end += 1
            while end > index and not lines[end - 1].strip():
                end -= 1               # trailing blank lines stay outside
            spans.append((starts[index], starts[end] - 1))
            previous, blank_before, index = lines[end - 1], False, end
            continue
        if line.strip():
            previous = line
        blank_before = not line.strip()
        index += 1
    return spans


# --------------------------------------------------------------------------- #
# MDX / JSX stripping                                                         #
# --------------------------------------------------------------------------- #

# Precise ESM matchers so a prose line that merely starts with "import"
# or "export" is never mistaken for code.
_ESM_IMPORT_RE = re.compile(
    r"^\s*import\s+(?:[^\n]*\bfrom\b\s*)?['\"][^'\"]+['\"]\s*;?\s*$")
_ESM_EXPORT_RE = re.compile(
    r"^\s*export\s+(?:default|const|let|var|async|function|class|\{)[^\n]*$")

# Component tags are uppercase-named (React/Astro convention) or
# <Fragment>. ``[^>]`` spans newlines, so multi-line opening tags match.
_JSX_COMPONENT_TAG_RE = re.compile(r"</?[A-Z][A-Za-z0-9.]*(?:\s[^>]*)?/?>")

_MDX_COMMENT_RE = re.compile(r"\{/\*[\s\S]*?\*/\}")
_MDX_SPACE_TOKEN_RE = re.compile(r"\{\s*['\"]\s*['\"]\s*\}")
_HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")

# An opening embed component: its name and its attributes.
_EMBED_TAG_RE = re.compile(r"<([A-Z][A-Za-z0-9]*)(\s[^>]*?)?/?>")
_JSX_ATTR_RE = re.compile(
    r"([A-Za-z_][\w:.-]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'"
    r"|\{\s*[\"'`]([^\"'`]*)[\"'`]\s*\}|\{\s*(\d+)\s*\})")


def _strip_esm(text: str) -> str:
    return "\n".join(
        line for line in text.split("\n")
        if not _ESM_IMPORT_RE.match(line) and not _ESM_EXPORT_RE.match(line)
    )


def _strip_mdx_expressions(text: str) -> str:
    text = _MDX_COMMENT_RE.sub("", text)
    text = _HTML_COMMENT_RE.sub("", text)
    return _MDX_SPACE_TOKEN_RE.sub(" ", text)


def _jsx_attrs(raw: str) -> Dict[str, str]:
    """A component's attributes whose value is written out (a text or a
    number); one computed from a variable cannot be read here."""
    attrs: Dict[str, str] = {}
    for match in _JSX_ATTR_RE.finditer(raw or ""):
        value = next((group for group in match.groups()[1:] if group is not None), "")
        attrs[match.group(1).lower()] = value.strip()
    return attrs


def _embed_markdown(name: str, attrs: Mapping[str, str]) -> Optional[str]:
    """What an embed component becomes: a line with the video's or the
    tweet's address (a Nostr client shows a player for it), or a Markdown
    image. The attributes are the ones astro-embed (``id``: an id or an
    address), mdx-embed (``youTubeId``, ``vimeoId``, ``tweetLink``),
    react-lite-youtube-embed (``<LiteYouTubeEmbed id>``) and next/image
    (``src``, ``alt``) document. None for any other
    component, which is stripped like the rest."""
    lowered = name.lower()
    if lowered in ("youtube", "liteyoutubeembed"):
        return _address_or(attrs.get("id") or attrs.get("youtubeid"),
                           "https://www.youtube.com/watch?v={}")
    if lowered == "vimeo":
        return _address_or(attrs.get("id") or attrs.get("vimeoid"), "https://vimeo.com/{}")
    if lowered == "tweet":
        link = attrs.get("tweetlink")  # "user/status/123"
        return _address_or(attrs.get("id") or (f"https://twitter.com/{link}" if link else ""),
                           "https://twitter.com/i/web/status/{}")
    if lowered in ("image", "img"):
        source = attrs.get("src") or ""
        if not source:
            return ""
        alt = re.sub(r"[\[\]]", "", attrs.get("alt") or "")
        return f"![{alt}]({link_target(source)})"
    return None


def _address_or(value: Optional[str], page: str) -> str:
    """``value`` when it is an address already, else the page its id
    names; "" for none."""
    value = (value or "").strip()
    if is_web(value):
        return value
    return page.format(value) if value else ""


def _map_embed_components(text: str) -> str:
    def _replace(match: re.Match) -> str:
        line = _embed_markdown(match.group(1), _jsx_attrs(match.group(2) or ""))
        if line is None:
            return match.group(0)      # not an embed: stripped below
        return f"\n\n{line}\n\n" if line else ""
    return _EMBED_TAG_RE.sub(_replace, text)


def _strip_jsx_components(text: str) -> str:
    return _JSX_COMPONENT_TAG_RE.sub("", text)


# --------------------------------------------------------------------------- #
# HTML islands to Markdown                                                    #
# --------------------------------------------------------------------------- #

# CommonMark's HTML blocks: a paragraph that starts with one of these
# tags is HTML. One that starts with an inline tag (``<strong>``,
# ``<a>``, ``<img>``) is a Markdown paragraph with some HTML in it, and
# converting it whole would escape the Markdown around that HTML.
_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "center", "details",
    "dialog", "dd", "div", "dl", "dt", "fieldset", "figcaption", "figure",
    "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr",
    "iframe", "li", "main", "nav", "ol", "p", "picture", "pre", "section",
    "summary", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    "video", "audio", "script", "style", "html", "body",
})
_HTML_BLOCK_START_RE = re.compile(r"^</?([a-zA-Z][a-zA-Z0-9-]*)(?=[\s/>])")
_HTML_ATTR_RE = re.compile(r"([A-Za-z_:][\w:.-]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")


def _html_attrs(raw: str) -> Dict[str, str]:
    return {m.group(1).lower(): (m.group(2) if m.group(2) is not None else m.group(3))
            for m in _HTML_ATTR_RE.finditer(raw or "")}


def _inline_image(match: re.Match) -> str:
    attrs = _html_attrs(match.group(1))
    source = (attrs.get("src") or "").strip()
    if not source:
        return ""
    alt = re.sub(r"[\[\]]", "", attrs.get("alt") or "")
    return f"![{alt}]({link_target(source)})"


def _inline_link(match: re.Match) -> str:
    href = (_html_attrs(match.group(1)).get("href") or "").strip()
    text = match.group(2)
    return f"[{text}]({link_target(href)})" if href else text


def _clean_inline_html(block: str) -> str:
    """Light cleanup of stray inline HTML in an otherwise-Markdown block."""
    block = re.sub(r"</?(?:strong|b)>", "**", block, flags=re.IGNORECASE)
    block = re.sub(r"</?(?:em|i)>", "_", block, flags=re.IGNORECASE)
    block = re.sub(r"</?code>", "`", block, flags=re.IGNORECASE)
    block = re.sub(r"<br\s*/?>", "  \n", block, flags=re.IGNORECASE)
    block = re.sub(r"<img\b([^>]*?)/?>", _inline_image, block, flags=re.IGNORECASE)
    block = re.sub(r"<a\b([^>]*)>([\s\S]*?)</a>", _inline_link, block, flags=re.IGNORECASE)
    block = re.sub(r"</?span(?:\s[^>]*)?>", "", block, flags=re.IGNORECASE)
    block = re.sub(r"</?div(?:\s[^>]*)?>", "", block, flags=re.IGNORECASE)
    return block


def _convert_html_islands(text: str, base_url: Optional[str] = None) -> str:
    blocks = re.split(r"\n[ \t]*\n", text)
    out: List[str] = []
    for block in blocks:
        trimmed = block.strip()
        if not trimmed:
            continue
        opening = _HTML_BLOCK_START_RE.match(trimmed)
        if opening and opening.group(1).lower() in _BLOCK_TAGS:
            try:
                markdown = html_to_markdown(trimmed, base_url=base_url).strip()
            except Exception:  # noqa: BLE001, keep the block over dropping content
                markdown = ""
            if markdown:
                out.append(markdown)
                continue
        cleaned = _clean_inline_html(block)
        if cleaned.strip():
            out.append(cleaned)
    return "\n\n".join(out)


# --------------------------------------------------------------------------- #
# Relative addresses                                                          #
# --------------------------------------------------------------------------- #

def whole_address(url: str, base: Optional[str]) -> str:
    """``url`` made whole against the document's own address ``base``
    (``./diagram.png`` next to the file). A path from the site's root
    (``/images/a.png``) cannot be placed when the document is read from
    raw.githubusercontent.com, where the root is the repository host's,
    not the site's: it stays as written."""
    value = (url or "").strip()
    if value.startswith("/") and not value.startswith("//") \
            and is_web(base) and urlsplit(base).hostname == "raw.githubusercontent.com":
        return value
    return whole_url(value, base) or ""


def _whole_images(text: str, base: Optional[str]) -> str:
    if not is_web(base):
        return text
    for start, end, url in reversed(image_destinations(text)):
        text = text[:start] + link_target(whole_address(url, base)) + text[end:]
    return text


# --------------------------------------------------------------------------- #
# Public entry                                                                #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class MdxDocument:
    title: str
    markdown: str
    summary: str
    frontmatter: Dict[str, str] = field(default_factory=dict)
    published_at: Optional[int] = None
    categories: Tuple[str, ...] = ()
    # The cover as a whole web address, or "" when there is none (or it
    # is a path that cannot be placed).
    image: str = ""
    author: str = ""


def mdx_to_markdown(
    raw: str,
    *,
    base_url: Optional[str] = None,
    indented_code: bool = True,
) -> MdxDocument:
    """Convert one MDX/Markdown document to clean Markdown.

    ``base_url`` is where the document is read from (a raw GitHub
    address), against which relative images and the cover are made
    whole. ``indented_code`` is False for an ``.mdx`` file, where
    indentation nests JSX rather than starting a code block.
    """
    values, mapping, body = _split_frontmatter(raw)
    details = _details(values, mapping)
    # Code is kept from every rewrite below; a plain Markdown file also
    # has indented code.
    code: List[str] = []
    text = protect(body, code_spans(body), code)
    if indented_code:
        text = protect(text, _indented_code_spans(text), code)

    text = _strip_esm(text)
    text = _strip_mdx_expressions(text)
    text = _map_embed_components(text)
    text = _strip_jsx_components(text)
    text = _convert_html_islands(text, base_url)
    text = _whole_images(text, base_url)
    text = restore(text, code)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    image = whole_address(details.image, base_url)
    return MdxDocument(
        title=(values.get("title") or "").strip(),
        markdown=text,
        summary=details.summary,
        frontmatter=values,
        published_at=details.published_at,
        categories=details.categories,
        image=image if is_web(image) else "",
        author=details.author,
    )
