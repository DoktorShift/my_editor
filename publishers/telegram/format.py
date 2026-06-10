"""Markdown to Telegram HTML, with auto-split for the 4096-char limit.

Telegram exposes two formatting modes; we use HTML because:

* The escape rules are small (``<``, ``>``, ``&``).
* The accepted tag list is fixed and unambiguous.
* Round-tripping is deterministic.

The accepted tags (per https://core.telegram.org/bots/api#html-style)
are: ``<b>``, ``<i>``, ``<u>``, ``<s>``, ``<span class="tg-spoiler">``,
``<a href="...">``, ``<code>``, ``<pre>``, ``<pre><code
class="language-X">``, ``<blockquote>``, ``<blockquote expandable>``.

This converter aims for an honest mapping of standard markdown to
those tags. Anything outside the mapping is preserved as literal
text (HTML-escaped). The result is plain Telegram HTML that round-
trips reliably; rich constructs that Telegram cannot render (tables,
headings, lists with semantics) are rewritten to readable ASCII.

The first inline image (``![alt](url)``) is extracted as ``Photo`` for
the caller to send via ``sendPhoto``; remaining images stay in the
body as text URLs. This matches how Telegram clients render link
previews for image URLs in plain messages.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


# Per the API docs:
TEXT_LIMIT: int = 4096
CAPTION_LIMIT: int = 1024


# ---------------------------------------------------------------------------- #
# Public dataclasses                                                           #
# ---------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Photo:
    """One inline image extracted from the body."""

    src: str
    """Either an http(s) URL or a local filesystem path."""

    alt: str = ""

    @property
    def is_local(self) -> bool:
        return not (self.src.startswith("http://") or self.src.startswith("https://"))


@dataclass(frozen=True)
class Formatted:
    """The fully prepared payload for one publish, before per-chat dispatch."""

    chunks: Tuple[str, ...]
    """One or more HTML strings, each <= ``TEXT_LIMIT`` characters."""

    photo: Optional[Photo]
    """First inline image, or ``None`` if the body has no images."""

    @property
    def is_threaded(self) -> bool:
        return len(self.chunks) > 1

    @property
    def fits_caption(self) -> bool:
        return len(self.chunks) == 1 and len(self.chunks[0]) <= CAPTION_LIMIT


# ---------------------------------------------------------------------------- #
# Entry point                                                                  #
# ---------------------------------------------------------------------------- #


def format_for_send(markdown: str, *, auto_split: bool = True) -> Formatted:
    """Convert ``markdown`` into a ready-to-send :class:`Formatted` payload."""
    photo, body_md = _extract_first_image(markdown)
    body_html = _md_to_html(body_md)
    chunks = _split_if_needed(body_html, auto_split=auto_split)
    return Formatted(chunks=tuple(chunks), photo=photo)


# ---------------------------------------------------------------------------- #
# Image extraction                                                             #
# ---------------------------------------------------------------------------- #


_IMG_RE = re.compile(r"!\[([^\]]*)\]\(\s*(\S+?)\s*\)")


def _extract_first_image(text: str) -> Tuple[Optional[Photo], str]:
    match = _IMG_RE.search(text)
    if not match:
        return None, text
    alt = match.group(1) or ""
    src = match.group(2) or ""
    if not src:
        return None, text
    # Remove the image markup, plus a trailing blank line if it
    # leaves one (so the body doesn't start with whitespace).
    before = text[: match.start()]
    after = text[match.end():]
    stripped = (before.rstrip("\n") + "\n\n" + after.lstrip("\n")).strip("\n")
    return Photo(src=src.strip(), alt=alt.strip()), stripped


# ---------------------------------------------------------------------------- #
# Markdown to Telegram HTML                                                    #
# ---------------------------------------------------------------------------- #
#
# A tiny, intentionally limited markdown subset. We intentionally do not
# pull in a full markdown library: the surface we accept is small, the
# output Telegram tolerates is even smaller, and a 200-line regex pass is
# easier to audit than a third-party dep.

_FENCE_RE = re.compile(r"^```([A-Za-z0-9_+-]*)\n(.*?)\n```", re.MULTILINE | re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+?)`")
_LINK_RE = re.compile(r"\[([^\]]+?)\]\(\s*(\S+?)\s*\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_BOLD_UNDERSCORE_RE = re.compile(r"__(.+?)__", re.DOTALL)
_ITALIC_STAR_RE = re.compile(r"(?<![*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\w)")
_ITALIC_UNDERSCORE_RE = re.compile(r"(?<![_\w])_(?!\s)([^_\n]+?)(?<!\s)_(?!\w)")
_STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL)
_SPOILER_RE = re.compile(r"\|\|(.+?)\|\|", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_HR_RE = re.compile(r"^---+\s*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^[ \t]*[-*+]\s+(.+)$", re.MULTILINE)
_NUMBERED_RE = re.compile(r"^[ \t]*(\d+)\.\s+(.+)$", re.MULTILINE)
# By the time this regex runs the source has been HTML-escaped, so a
# leading ``>`` from the user input has become ``&gt;``. Match both
# forms so we handle copy-pasted blockquotes from either world.
_QUOTE_BLOCK_RE = re.compile(r"((?:^(?:>|&gt;)[^\n]*\n?)+)", re.MULTILINE)
_QUOTE_LINE_PREFIX_RE = re.compile(r"^(?:>|&gt;) ?")

_PLACEHOLDER_OPEN = "\x00\x01"
_PLACEHOLDER_CLOSE = "\x00\x02"


def _md_to_html(text: str) -> str:
    """Convert a small markdown subset to Telegram-flavored HTML."""
    if not text.strip():
        return ""

    # 1. Pull out fenced code blocks first so other rules never touch
    #    their contents.
    placeholders: List[str] = []

    def _stash_fence(match: re.Match) -> str:
        lang = match.group(1).strip()
        body = match.group(2)
        escaped = html.escape(body, quote=False)
        if lang:
            tag = (
                f'<pre><code class="language-{html.escape(lang, quote=True)}">'
                f"{escaped}</code></pre>"
            )
        else:
            tag = f"<pre>{escaped}</pre>"
        placeholders.append(tag)
        return f"{_PLACEHOLDER_OPEN}{len(placeholders) - 1}{_PLACEHOLDER_CLOSE}"

    text = _FENCE_RE.sub(_stash_fence, text)

    # 2. Pull out inline code spans next.
    def _stash_inline(match: re.Match) -> str:
        escaped = html.escape(match.group(1), quote=False)
        placeholders.append(f"<code>{escaped}</code>")
        return f"{_PLACEHOLDER_OPEN}{len(placeholders) - 1}{_PLACEHOLDER_CLOSE}"

    text = _INLINE_CODE_RE.sub(_stash_inline, text)

    # 3. Stash links so their URLs are never touched by other rules.
    def _stash_link(match: re.Match) -> str:
        label = html.escape(match.group(1), quote=False)
        url = match.group(2)
        url_attr = html.escape(url, quote=True)
        placeholders.append(f'<a href="{url_attr}">{label}</a>')
        return f"{_PLACEHOLDER_OPEN}{len(placeholders) - 1}{_PLACEHOLDER_CLOSE}"

    text = _LINK_RE.sub(_stash_link, text)

    # 4. Escape the residual text now, so nothing we emit below can
    #    inject HTML.
    text = html.escape(text, quote=False)

    # 5. Apply inline formatting.
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _BOLD_UNDERSCORE_RE.sub(r"<b>\1</b>", text)
    text = _STRIKE_RE.sub(r"<s>\1</s>", text)
    text = _SPOILER_RE.sub(r"<tg-spoiler>\1</tg-spoiler>", text)
    text = _ITALIC_STAR_RE.sub(r"<i>\1</i>", text)
    text = _ITALIC_UNDERSCORE_RE.sub(r"<i>\1</i>", text)

    # 6. Block-level transforms.
    text = _HEADING_RE.sub(lambda m: f"<b>{m.group(2).upper()}</b>", text)
    text = _HR_RE.sub("———", text)
    text = _BULLET_RE.sub(r"• \1", text)
    text = _NUMBERED_RE.sub(r"\1. \2", text)

    def _quote_block(match: re.Match) -> str:
        block = match.group(1)
        lines = []
        for line in block.splitlines():
            if _QUOTE_LINE_PREFIX_RE.match(line):
                lines.append(_QUOTE_LINE_PREFIX_RE.sub("", line))
        inner = "\n".join(lines).rstrip("\n")
        if not inner:
            return ""
        if len(lines) > 8:
            return f"<blockquote expandable>{inner}</blockquote>\n"
        return f"<blockquote>{inner}</blockquote>\n"

    text = _QUOTE_BLOCK_RE.sub(_quote_block, text)

    # 7. Restore placeholders.
    def _restore(match: re.Match) -> str:
        return placeholders[int(match.group(1))]

    text = re.sub(
        re.escape(_PLACEHOLDER_OPEN) + r"(\d+)" + re.escape(_PLACEHOLDER_CLOSE),
        _restore,
        text,
    )

    # 8. Normalize: trim trailing whitespace, collapse 3+ blank lines.
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


# ---------------------------------------------------------------------------- #
# Length splitting                                                              #
# ---------------------------------------------------------------------------- #


_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*?>")


def _split_if_needed(html_body: str, *, auto_split: bool) -> List[str]:
    if not html_body:
        return [""]
    if len(html_body) <= TEXT_LIMIT:
        return [html_body]
    if not auto_split:
        return [html_body[:TEXT_LIMIT]]

    chunks = _split_html(html_body, TEXT_LIMIT - 16)  # room for "(N/N)\n"

    if len(chunks) <= 1:
        return chunks

    total = len(chunks)
    return [f"({i + 1}/{total})\n{c}" for i, c in enumerate(chunks)]


def _split_html(html_body: str, limit: int) -> List[str]:
    """Split ``html_body`` along paragraph boundaries, never breaking tags.

    Strategy:
      1. Split on double-newline boundaries.
      2. Greedily fill chunks under ``limit``.
      3. If a single paragraph exceeds ``limit``, split it on single
         newlines, then by raw character (worst case).

    Open/close tag balance is restored per chunk so each one is valid
    standalone HTML for Telegram's parser.
    """
    paragraphs = html_body.split("\n\n")
    chunks: List[str] = []
    buf = ""

    def _flush() -> None:
        nonlocal buf
        if buf.strip():
            chunks.append(buf.rstrip("\n"))
        buf = ""

    for para in paragraphs:
        if not para.strip():
            continue
        candidate = (buf + ("\n\n" if buf else "") + para).rstrip("\n")
        if len(candidate) <= limit:
            buf = candidate
            continue
        # Doesn't fit. Flush, then start fresh.
        _flush()
        if len(para) <= limit:
            buf = para
            continue
        # The paragraph itself is too big. Split on single newlines.
        for line in _split_oversized(para, limit):
            if not line:
                continue
            candidate = (buf + ("\n" if buf else "") + line).rstrip("\n")
            if len(candidate) <= limit:
                buf = candidate
            else:
                _flush()
                buf = line

    _flush()

    # Repair any HTML tag balance broken by the cuts.
    return [_close_open_tags(c) for c in chunks]


def _split_oversized(text: str, limit: int) -> List[str]:
    """Split a single oversized paragraph on lines, then fall back to chars."""
    parts: List[str] = []
    for line in text.split("\n"):
        if len(line) <= limit:
            parts.append(line)
            continue
        # Last resort: split mid-line. Try to break at a whitespace
        # boundary near the limit.
        i = 0
        while i < len(line):
            cut = min(i + limit, len(line))
            # Walk back to the previous space if we'd otherwise break a word.
            if cut < len(line):
                back = line.rfind(" ", i, cut)
                if back > i + limit // 2:
                    cut = back
            parts.append(line[i:cut].strip())
            i = cut
    return parts


_VOID_TAGS = frozenset({"br", "hr", "img"})


def _close_open_tags(chunk: str) -> str:
    """Append closing tags for any tag opened but not closed in ``chunk``.

    Telegram's HTML parser is strict: an unclosed ``<b>`` returns
    400 ``can't parse entities``. Splitting at paragraph boundaries
    means tag spans almost never cross the cut, but blockquotes / pre
    fences could in pathological inputs. We close every dangling open
    tag in LIFO order so the parser is happy.
    """
    stack: List[str] = []
    pos = 0
    for match in _TAG_RE.finditer(chunk):
        tag_text = match.group()
        # Skip over content; we only care about tag bookkeeping.
        pos = match.end()
        if tag_text.startswith("</"):
            # closing tag
            name = tag_text[2:-1].strip().split()[0].lower()
            if stack and stack[-1] == name:
                stack.pop()
            continue
        # opening tag
        inner = tag_text[1:-1].strip()
        if not inner or inner.endswith("/"):
            continue
        name = inner.split()[0].lower()
        if name in _VOID_TAGS:
            continue
        stack.append(name)
    pos = pos  # silence unused warning
    while stack:
        name = stack.pop()
        chunk += f"</{name}>"
    return chunk
