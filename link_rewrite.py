# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Links in notes that follow a file when it is renamed or moved.

A note points at its pictures, and at other notes and files, by
relative links: ``![](Trip_media/beach.png)``, ``[Plan](../Work/plan.md)``.
When the folder sidebar renames or moves a file or a folder
(folder_library.py), every note under the library folder that pointed at
it is changed to point at its new place, and a note that moved itself
has its own relative links worked out again from where it is now.

What counts as a link: inline links and pictures (with ``<...>``
destinations, percent-encoding, a title, a ``?query`` or a
``#fragment``), reference definitions (``[id]: path``), and the
``src``/``href`` of HTML ``<img>``, ``<a>``, ``<video>``, ``<audio>`` and
``<source>`` tags. Web and mail addresses, absolute paths, front
matter, HTML comments, math in R Markdown and links inside code (fenced
and indented blocks, R Markdown chunks, inline code spans) are never
touched. A note is read the way a CommonMark reader reads it, in time
proportional to its length whatever is in it.

Each link keeps the way it was written: percent-encoded stays encoded,
``<...>`` stays in brackets, and a plain one gets brackets only when its
new address needs them (a space or a parenthesis), as the editor writes
links itself (markdown_writer._destination). A "#", "?" or "%" in a new
name, and a ":" in its first part, are percent-encoded, as they would
otherwise read as a fragment, a query, an escape or an address.
Everything else in a note stays byte for byte: its encoding, its byte
order mark, its line endings. A note in an older encoding than UTF-8
keeps its bytes; it is updated when the new names are plain ASCII and
named as not updated otherwise. Notes locked with a password, files
that are not text, and links to notes elsewhere (a symbolic link is
never written through) are left alone.
"""

from __future__ import annotations

import bisect
import errno
import html
import logging
import os
import re
import stat
import sys
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple
from urllib.parse import quote, unquote

import atomic_file
import file_kinds
import front_matter
import locked_files
import markdown_writer

log = logging.getLogger(__name__)

# How many files and folders a walk for notes looks at, at most: a
# library is a folder of writing, not a whole disk.
WALK_LIMIT = 100_000

_SCHEME = re.compile(r"\A[A-Za-z][A-Za-z0-9+.\-]*:")
_PERCENT = re.compile(r"%[0-9A-Fa-f]{2}")
_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
# Characters a percent-encoded link writes as they are.
_SAFE = "/!$&'*+,;=:@~-._"


# -- finding links ------------------------------------------------------------
#
# A note is read the way a CommonMark reader reads it, in one pass over
# its lines and one pass over each paragraph, so a note of any shape
# costs time in proportion to its length.
#
# The lines give the blocks: front matter (YAML or TOML at the top),
# fenced code (a fence indented up to three spaces in its container),
# indented code (four spaces or more, not inside a paragraph), HTML
# blocks (a comment, <pre>, <script> and the like are not prose; in the
# other HTML blocks the tags count and Markdown does not), list items
# and block quotes as the containers indentation is measured in, and
# the paragraphs and headings links are written in.
#
# A paragraph is read from left to right once: a run of backticks opens
# a code span closed by the next run of as many (found by bisection, not
# by a search), an HTML comment or tag is passed over whole (the src and
# href of a tag count), math in an R Markdown note is passed over the
# way pandoc reads it ($...$ and $$...$$; plain Markdown has no math),
# and a "[" that a "]" closes, followed by "(", is a link. Every search a
# link's destination needs (the closing ">", quote or parenthesis) is a
# lookup in positions found once per note.

_ASCII_PUNCTUATION = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)?")
_FENCE = re.compile(r"(`{3,}|~{3,})([^\r\n]*)")
_ATX = re.compile(r"#{1,6}(?:[ \t]|$)")
_THEMATIC = re.compile(r"(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
_BULLET = re.compile(r"(?:[-+*]|([0-9]{1,9})[.)])(?=[ \t]|$)")
_DEFINITION = re.compile(
    r"\[(?!\^)((?:[^\[\]\\\r\n]|\\.){1,999})\]:[ \t]*(?:(?:\r\n|\r|\n)[ \t]*)?"
    r"(<[^<>\r\n]*>|[^\s<][^\s]*)")
_SPACE = re.compile(r"[ \t]*(?:(?:\r\n|\r|\n)[ \t]*)?")

# HTML, as CommonMark reads it.
_ATTRIBUTE = (r"[ \t\r\n]+([A-Za-z_:][A-Za-z0-9_.:-]*)(?:[ \t\r\n]*=[ \t\r\n]*"
              r"""(?:([^ \t\r\n"'=<>`]+)|'([^']*)'|"([^"]*)"))?""")
_ATTRIBUTE_AT = re.compile(_ATTRIBUTE)
_OPEN_TAG = re.compile(r"<([A-Za-z][A-Za-z0-9-]*)(?:" + _ATTRIBUTE + r")*+[ \t\r\n]*+/?>")
_CLOSE_TAG = re.compile(r"</[A-Za-z][A-Za-z0-9-]*[ \t\r\n]*>")
_AUTOLINK = re.compile(r"<[A-Za-z][A-Za-z0-9+.\-]{1,31}:[^\x00-\x20<>]*>")
_LINK_TAG_NAMES = frozenset({"img", "a", "video", "audio", "source"})
_LINK_ATTRIBUTES = frozenset({"src", "href", "poster"})
_HTML_REGION = re.compile(r"<!--|<(?:img|a|video|audio|source)(?=[ \t\r\n/>])", re.I)
_RAW_TEXT_TAGS = r"(?:pre|script|style|textarea)"
_BLOCK_TAGS = (
    r"(?:address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|"
    r"details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|"
    r"h1|h2|h3|h4|h5|h6|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav|"
    r"noframes|ol|optgroup|option|p|param|search|section|summary|table|tbody|td|tfoot|th|"
    r"thead|title|tr|track|ul)")
# The HTML blocks that end at a line holding their end (types 1 to 5),
# as (start, end); the others (types 6 and 7) end at a blank line.
_HTML_ENDS = (
    (re.compile(r"<" + _RAW_TEXT_TAGS + r"(?:[ \t>]|$)", re.I),
     re.compile(r"</" + _RAW_TEXT_TAGS + r">", re.I)),
    (re.compile(r"<!--"), re.compile(r"-->")),
    (re.compile(r"<\?"), re.compile(r"\?>")),
    (re.compile(r"<![A-Za-z]"), re.compile(r">")),
    (re.compile(r"<!\[CDATA\["), re.compile(r"\]\]>")),
)
_HTML_BLOCK = re.compile(r"</?" + _BLOCK_TAGS + r"(?:[ \t]|/?>|$)", re.I)
_HTML_ALONE = re.compile(r"(?:" + _OPEN_TAG.pattern + r"|" + _CLOSE_TAG.pattern + r")[ \t]*$")
_HTML_ALONE_EXCEPT = re.compile(r"</?" + _RAW_TEXT_TAGS + r"(?![A-Za-z0-9-])", re.I)


@dataclass
class _Link:
    start: int              # where the destination is written in the text
    end: int
    path: str               # the destination as written, without query or fragment
    suffix: str             # its ?query and #fragment, as written
    style: str              # "angle", "raw", "loose" (a plain one with spaces) or "html"
    quote_char: str = ""    # an HTML attribute's quote


def _split(token: str, start: int, end: int, style: str) -> _Link:
    """A link from its written destination: the path, and the ?query and
    #fragment after it."""
    cut = min((k for k in (token.find("?"), token.find("#")) if k >= 0), default=len(token))
    return _Link(start, end, token[:cut], token[cut:], style)


def _front_matter_end(text: str) -> int:
    """Where the front matter at the top of ``text`` ends (0 without one)."""
    if not text.startswith(("---", "+++", "﻿")):
        return 0
    front, body = front_matter.split(text)
    return len(text) - len(body) if front is not None else 0


def _skip_indent(text: str, pos: int, end: int, col: int) -> Tuple[int, int]:
    """Past the spaces and tabs at ``pos``: (position, column), a tab
    going on to the next multiple of four."""
    while pos < end:
        ch = text[pos]
        if ch == " ":
            col += 1
        elif ch == "\t":
            col += 4 - col % 4
        else:
            break
        pos += 1
    return pos, col


def _quote_markers(text: str, pos: int, end: int) -> Tuple[int, int, int]:
    """Past the block quote markers that start a line: (position,
    column, how many)."""
    col = depth = 0
    while True:
        at, at_col = _skip_indent(text, pos, end, col)
        if at_col - col > 3 or at >= end or text[at] != ">":
            return pos, col, depth
        depth += 1
        pos, col = at + 1, at_col + 1
        if pos < end and text[pos] == " ":
            pos, col = pos + 1, col + 1
        elif pos < end and text[pos] == "\t":
            pos, col = pos + 1, col + 4 - col % 4


def _html_start(text: str, pos: int, end: int, in_paragraph: bool):
    """The HTML block a line starts at ``pos``: its end pattern (types 1
    to 5), "" for one that ends at a blank line, None for no HTML block."""
    if text[pos] != "<":
        return None
    for start, ending in _HTML_ENDS:
        if start.match(text, pos, end):
            return ending
    if _HTML_BLOCK.match(text, pos, end):
        return ""
    if not in_paragraph and _HTML_ALONE.match(text, pos, end) \
            and not _HTML_ALONE_EXCEPT.match(text, pos, end):
        return ""
    return None


def _starts_block(text: str, pos: int, end: int, in_paragraph: bool) -> bool:
    """Whether the line at ``pos`` begins a block of its own (and so ends
    a paragraph) rather than going on with one."""
    ch = text[pos]
    if ch in "`~":
        fence = _FENCE.match(text, pos, end)
        return bool(fence) and not (ch == "`" and "`" in fence.group(2))
    if ch == "#":
        return bool(_ATX.match(text, pos, end))
    if ch == ">":
        return True
    if ch == "<":
        return _html_start(text, pos, end, in_paragraph) is not None
    if ch in "*-_" and _THEMATIC.match(text, pos, end):
        return True
    bullet = _BULLET.match(text, pos, end)
    if bullet is None:
        return False
    if in_paragraph:
        # A list interrupts a paragraph only with something in its first
        # item, and a numbered one only when it starts at 1.
        if bullet.group(1) is not None and bullet.group(1) != "1":
            return False
        return _skip_indent(text, bullet.end(), end, 0)[0] < end
    return True


class _Blocks:
    """Where links can be written in a note: the prose (paragraphs and
    headings, read for inline links), the HTML blocks (read for tags
    only) and, for each paragraph, where its lines start (a reference
    definition opens a paragraph)."""

    def __init__(self) -> None:
        self.prose: List[Tuple[int, int]] = []
        self.html: List[Tuple[int, int]] = []
        self.paragraphs: List[List[int]] = []


def _blocks(text: str) -> _Blocks:
    found = _Blocks()
    items: List[Tuple[int, int]] = []           # open list items: (content column, quotes)
    fence: Optional[Tuple[str, int, int, int]] = None   # (character, length, column, quotes)
    indented: Optional[Tuple[int, int]] = None  # open indented code: (column, quotes)
    html: Optional[Tuple[object, int]] = None   # open HTML block: (its end, where it began)
    paragraph: Optional[list] = None            # [start, end, line starts, quotes]

    def close_paragraph() -> None:
        nonlocal paragraph
        if paragraph is not None:
            found.prose.append((paragraph[0], paragraph[1]))
            found.paragraphs.append(paragraph[2])
            paragraph = None

    for line in _LINE.finditer(text, _front_matter_end(text)):
        line_start = line.start()
        if line_start == line.end():
            break
        line_end = line.end()
        while line_end > line_start and text[line_end - 1] in "\r\n":
            line_end -= 1
        pos, col, quotes = _quote_markers(text, line_start, line_end)
        at, at_col = _skip_indent(text, pos, line_end, col)
        blank = at >= line_end

        if fence is not None:
            char, length, fence_col, fence_quotes = fence
            if blank:
                continue
            if quotes >= fence_quotes and at_col >= fence_col:
                closing = _FENCE.match(text, at, line_end) if at_col - fence_col <= 3 else None
                if closing and closing.group(1)[0] == char \
                        and len(closing.group(1)) >= length and not closing.group(2).strip():
                    fence = None
                continue
            fence = None                        # its container ended, and the code with it
        if indented is not None:
            if blank or (quotes == indented[1] and at_col - indented[0] >= 4):
                continue
            indented = None
        if html is not None:
            ending, began = html
            if ending == "":
                if blank:
                    found.html.append((began, line_start))
                    html = None
                continue
            if ending.search(text, pos, line_end):
                html = None
            continue
        if blank:
            close_paragraph()
            continue

        keep = 0
        while keep < len(items) and items[keep][1] <= quotes and at_col >= items[keep][0]:
            keep += 1
        base = max(col, items[keep - 1][0]) if keep else col
        relative = at_col - base
        starts = relative <= 3 and _starts_block(text, at, line_end, paragraph is not None)
        if paragraph is not None and not starts and quotes <= paragraph[3]:
            paragraph[1] = line_end             # it goes on (lazily, perhaps)
            paragraph[2].append(at)
            continue
        close_paragraph()
        del items[keep:]

        # List items, one inside another on the same line too.
        while relative <= 3 and text[at] in "-+*0123456789":
            bullet = _BULLET.match(text, at, line_end)
            if bullet is None or (text[at] in "*-" and _THEMATIC.match(text, at, line_end)):
                break
            marker_col = at_col + bullet.end() - at
            at, at_col = _skip_indent(text, bullet.end(), line_end, marker_col)
            content_col = marker_col + 1 if at >= line_end or at_col - marker_col > 4 \
                else at_col
            items.append((content_col, quotes))
            base, relative = content_col, at_col - content_col
            if at >= line_end:
                break
        if at >= line_end:
            continue
        if relative >= 4:
            indented = (base, quotes)
            continue
        opening = _FENCE.match(text, at, line_end)
        if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):
            fence = (opening.group(1)[0], len(opening.group(1)), base, quotes)
            continue
        ending = _html_start(text, at, line_end, False)
        if ending is not None:
            if ending == "" or not ending.search(text, at + 2, line_end):
                html = (ending, at)
            continue
        if _ATX.match(text, at, line_end):
            found.prose.append((at, line_end))
            continue
        if text[at] in "*-_" and _THEMATIC.match(text, at, line_end):
            continue
        paragraph = [at, line_end, [at], quotes]
    close_paragraph()
    if html is not None and html[0] == "":
        found.html.append((html[1], len(text)))
    return found


class _Scan:
    """One note's text, with the positions of the characters a link is
    made of found once, so that every search is a bisection."""

    def __init__(self, text: str, math: bool):
        self.text = text
        self.special = re.compile(r"[\\`<\[\]$]" if math else r"[\\`<\[\]]")
        self.escaped = set()
        for run in re.finditer(r"\\+", text):
            if len(run.group()) % 2 and run.end() < len(text) \
                    and text[run.end()] in _ASCII_PUNCTUATION:
                self.escaped.add(run.end())
        self._lists: Dict[str, List[int]] = {}
        self._backticks: Optional[Dict[int, List[int]]] = None
        self._matches: Optional[Dict[int, int]] = None
        self._closes: Dict[int, Optional[int]] = {}

    def _positions(self, key: str) -> List[int]:
        found = self._lists.get(key)
        if found is None:
            text = self.text
            if key == "space":
                found = [m.start() for m in re.finditer(r"[\x00-\x20\x7f]", text)]
            elif key == "newline":
                found = [m.start() for m in re.finditer(r"[\r\n]", text)]
            elif key == "-->":
                found = [m.start() for m in re.finditer(r"(?=-->)", text)]
            elif key == "$$":
                found = [m.start() for m in re.finditer(r"(?=\$\$)", text)
                         if m.start() not in self.escaped]
            elif key == "$":
                # A closing $ follows something other than a space and
                # comes before something other than a digit (pandoc).
                found = [m.start() for m in re.finditer(r"(?<=\S)\$(?![0-9])", text)
                         if m.start() not in self.escaped]
            elif key.startswith("any"):
                found = [m.start() for m in re.finditer(re.escape(key[3:]), text)]
            else:
                found = [m.start() for m in re.finditer(re.escape(key), text)
                         if m.start() not in self.escaped]
            self._lists[key] = found
        return found

    def next(self, key: str, at: int, end: int) -> Optional[int]:
        """The first position of ``key`` from ``at`` on, before ``end``
        (unescaped, unless the key starts with "any")."""
        found = self._positions(key)
        index = bisect.bisect_left(found, at)
        return found[index] if index < len(found) and found[index] < end else None

    def count(self, key: str, start: int, end: int) -> int:
        found = self._positions(key)
        return bisect.bisect_left(found, end) - bisect.bisect_left(found, start)

    def backtick_closer(self, length: int, at: int, end: int) -> Optional[int]:
        """Where the code span opened by ``length`` backticks before
        ``at`` ends (after its closing run), or None when no run of as
        many follows before ``end``."""
        if self._backticks is None:
            self._backticks = {}
            for run in re.finditer(r"`+", self.text):
                self._backticks.setdefault(len(run.group()), []).append(run.start())
        starts = self._backticks.get(length, [])
        index = bisect.bisect_left(starts, at)
        if index < len(starts) and starts[index] < end:
            return starts[index] + length
        return None

    def matching_parenthesis(self, at: int) -> Optional[int]:
        """The ``)`` that closes the ``(`` at ``at``, or None."""
        if self._matches is None:
            self._matches = {}
            open_ones: List[int] = []
            for m in re.finditer(r"[()]", self.text):
                if m.start() in self.escaped:
                    continue
                if m.group() == "(":
                    open_ones.append(m.start())
                elif open_ones:
                    self._matches[open_ones.pop()] = m.start()
        return self._matches.get(at)

    def closes(self, at: int, end: int) -> Optional[int]:
        """Where an inline link that may end at ``at`` (an optional title,
        then ``)``) ends, after its ``)``; None when it does not."""
        if at in self._closes:
            return self._closes[at]
        text = self.text
        i = _SPACE.match(text, at, end).end()
        result = None
        if i < end and text[i] in "\"'(":
            closing = self.next(")" if text[i] == "(" else text[i], i + 1, end)
            if closing is not None:
                i = _SPACE.match(text, closing + 1, end).end()
                if i < end and text[i] == ")":
                    result = i + 1
        elif i < end and text[i] == ")":
            result = i + 1
        self._closes[at] = result
        return result


def _destination(scan: _Scan, at: int, end: int) -> Optional[Tuple[_Link, int]]:
    """The destination of an inline link whose ``(`` is just before
    ``at``, and where the link ends; None when what follows is not one."""
    text = scan.text
    i = _SPACE.match(text, at, end).end()
    if i >= end:
        return None
    if text[i] == "<":
        close = scan.next(">", i + 1, end)
        stops = [k for k in (scan.next("<", i + 1, end), scan.next("newline", i + 1, end))
                 if k is not None]
        if close is None or (stops and min(stops) < close):
            return None
        after = scan.closes(close + 1, end)
        if after is None:
            return None
        return _split(text[i + 1:close], i, close + 1, "angle"), after
    space = scan.next("space", i, end)
    space = end if space is None else space
    closing = scan.matching_parenthesis(at - 1)
    if closing is not None and closing <= space and closing < end:
        if closing == i:
            return None                         # no destination at all
        return _split(text[i:closing], i, closing, "raw"), closing + 1
    if space > i and scan.count("(", i, space) == scan.count(")", i, space):
        after = scan.closes(space, end)
        if after is not None:
            return _split(text[i:space], i, space, "raw"), after
    # A plain destination with spaces, as some apps write: not Markdown,
    # but meant as a link, and kept as written.
    loose_end = scan.next("any)", i, end)
    line_end = scan.next("newline", i, end)
    if loose_end is None or (line_end is not None and line_end < loose_end):
        return None
    if any(scan.next("any" + ch, i, loose_end) is not None for ch in "(\"'<>"):
        return None
    token = text[i:loose_end].rstrip()
    if not token:
        return None
    return _split(token, i, i + len(token), "loose"), loose_end + 1


def _tag_links(text: str, tag) -> List[_Link]:
    """The src, href and poster of an HTML tag that names a file."""
    if tag.group(1).lower() not in _LINK_TAG_NAMES:
        return []
    links = []
    pos = tag.start() + 1 + len(tag.group(1))
    while True:
        attribute = _ATTRIBUTE_AT.match(text, pos, tag.end())
        if attribute is None:
            return links
        pos = attribute.end()
        if attribute.group(1).lower() not in _LINK_ATTRIBUTES:
            continue
        for group, quote_char in ((4, '"'), (3, "'"), (2, "")):
            if attribute.group(group) is not None:
                start, end = attribute.start(group), attribute.end(group)
                if quote_char:
                    start, end = start - 1, end + 1
                link = _split(html.unescape(attribute.group(group)), start, end, "html")
                link.quote_char = quote_char
                links.append(link)


def _raw_html(scan: _Scan, at: int, end: int, links: List[_Link]) -> int:
    """Past the HTML at ``at`` (a comment, an autolink, a tag), with the
    files a tag names added to ``links``; one character on for a ``<``
    that begins none of them."""
    text = scan.text
    if text.startswith("<!--", at):
        close = scan.next("-->", at + 2, end)
        return close + 3 if close is not None else at + 4
    following = text[at + 1:at + 2]
    if following.isascii() and following.isalpha():
        autolink = _AUTOLINK.match(text, at, end)
        if autolink:
            return autolink.end()
        tag = _OPEN_TAG.match(text, at, end)
        if tag:
            links.extend(_tag_links(text, tag))
            return tag.end()
    elif following == "/":
        tag = _CLOSE_TAG.match(text, at, end)
        if tag:
            return tag.end()
    return at + 1


def _math(scan: _Scan, at: int, end: int) -> int:
    """Past the math at ``at`` as pandoc reads it in R Markdown; one
    character on for a ``$`` that opens none."""
    text = scan.text
    if text.startswith("$$", at):
        close = scan.next("$$", at + 2, end)
        return close + 2 if close is not None else at + 2
    if at + 1 < end and not text[at + 1].isspace():
        close = scan.next("$", at + 2, end)
        if close is not None:
            return close + 1
    return at + 1


def _inline_links(scan: _Scan, start: int, end: int, links: List[_Link]) -> None:
    """The links of the paragraph or heading between ``start`` and ``end``."""
    text = scan.text
    openers: List[bool] = []    # each open "[": whether a "!" came first (a picture)
    inactive = 0     # the "[" below this many make no link (a link holds none; a picture may)
    i = start
    while True:
        found = scan.special.search(text, i, end)
        if found is None:
            return
        i = found.start()
        ch = text[i]
        if ch == "\\":
            i += 2 if i + 1 < end and text[i + 1] in _ASCII_PUNCTUATION else 1
        elif ch == "`":
            j = i
            while j < end and text[j] == "`":
                j += 1
            closer = scan.backtick_closer(j - i, j, end)
            i = closer if closer is not None else j
        elif ch == "<":
            i = _raw_html(scan, i, end, links)
        elif ch == "$":
            i = _math(scan, i, end)
        elif ch == "[":
            openers.append(i > start and text[i - 1] == "!" and (i - 1) not in scan.escaped)
            i += 1
        else:                                   # "]"
            i += 1
            if not openers:
                continue
            image = openers.pop()
            active = image or len(openers) >= inactive
            inactive = min(inactive, len(openers))
            if not active or i >= end or text[i] != "(":
                continue
            made = _destination(scan, i + 1, end)
            if made is None:
                continue
            link, i = made
            links.append(link)
            if not image:
                inactive = len(openers)


def _links(text: str, math: bool = False) -> List[_Link]:
    """Every link destination in ``text`` outside code, comments and
    front matter, in order; ``math`` for an R Markdown note, whose
    ``$...$`` is math."""
    blocks = _blocks(text)
    scan = _Scan(text, math)
    links: List[_Link] = []
    for start, end in blocks.prose:
        _inline_links(scan, start, end, links)
    for start, end in blocks.html:
        pos = start
        for found in _HTML_REGION.finditer(text, start, end):
            if found.start() >= pos:
                pos = _raw_html(scan, found.start(), end, links)
    for lines in blocks.paragraphs:
        index = 0
        while index < len(lines):
            definition = _DEFINITION.match(text, lines[index])
            if definition is None:
                break
            token = definition.group(2)
            if token.startswith("<"):
                links.append(_split(token[1:-1], definition.start(2), definition.end(2),
                                    "angle"))
            else:
                links.append(_split(token, definition.start(2), definition.end(2), "raw"))
            index += 1
            while index < len(lines) and lines[index] < definition.end():
                index += 1
    links.sort(key=lambda link: link.start)
    return links


# -- where a link points ------------------------------------------------------

# Address schemes: a link that starts with one of these names no file.
# Any other "word:" at its start is a file with a colon in its name when
# such a file is there ("Todo:old.md" is a fine name on a Mac or Linux).
_KNOWN_SCHEMES = frozenset({
    "about", "blob", "bitcoin", "callto", "chrome", "data", "dav", "davs", "facetime", "feed",
    "file", "ftp", "ftps", "geo", "git", "gopher", "http", "https", "im", "imap", "irc",
    "ircs", "javascript", "ldap", "lightning", "magnet", "mailto", "maps", "market", "matrix",
    "message", "mms", "news", "nntp", "nostr", "notes", "obsidian", "onenote", "pop",
    "resource", "rtsp", "sftp", "sip", "sips", "skype", "slack", "sms", "smb", "spotify",
    "ssh", "steam", "svn", "tel", "telnet", "urn", "vnc", "webcal", "ws", "wss", "x-apple",
    "xmpp", "zoommtg", "zotero",
})


def is_local(path: str) -> bool:
    """Whether a destination may be a relative path on this computer: no
    known address scheme, no drive letter, not absolute. One that starts
    with an unknown "word:" counts only where it names a file that is
    there or that moved (``_rewritten``, ``link_targets``)."""
    if not path or path.startswith(("/", "\\", "#", "?")):
        return False
    scheme = _SCHEME.match(path)
    if scheme is None:
        return True
    name = scheme.group()[:-1].lower()
    return len(name) > 1 and name not in _KNOWN_SCHEMES


def _needs_a_file(path: str) -> bool:
    """Whether ``path`` reads as an address unless a file of that name is
    there (it starts with an unknown "word:")."""
    return _SCHEME.match(path) is not None


def _legacy(path: str) -> Optional[str]:
    """A destination from a note that is not UTF-8, its bytes read as
    Windows or Latin-1 text, or None when it is plain UTF-8."""
    if not any("\udc80" <= ch <= "\udcff" for ch in path):
        return None
    data = path.encode("utf-8", "surrogateescape")
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _readings(link: _Link) -> List[str]:
    """The relative paths a destination may mean: with its escapes and
    percent-encoding read, and, for a name that really has a "%" in it,
    as written."""
    path = link.path if link.style == "html" else _ESCAPE.sub(r"\1", link.path)
    readings = []
    if _PERCENT.search(path):
        readings.append(unquote(path))
    readings.append(path)
    legacy = _legacy(path)
    if legacy is not None:
        readings.append(legacy)
    return readings


def _resolve(folder: str, relative: str) -> str:
    return os.path.normpath(os.path.join(folder, relative.replace("/", os.sep)))


_FOLDS_CASE = sys.platform in ("darwin", "win32")


def _parts(path: str) -> List[str]:
    """``path`` as names that compare the way this system's file names do:
    whatever the Unicode normalization (the Mac stores ü decomposed, a
    note writes it composed), and without case on a Mac and on Windows."""
    parts = []
    for part in os.path.normpath(os.path.abspath(path)).split(os.sep):
        part = unicodedata.normalize("NFC", part)
        parts.append(part.casefold() if _FOLDS_CASE else part)
    return parts


def _nfc(path: str) -> str:
    return unicodedata.normalize("NFC", os.path.normpath(os.path.abspath(path)))


def is_within(path: str, folder: str) -> bool:
    """Whether ``path`` is ``folder`` or anything in it, compared the way
    this system compares file names."""
    inner, outer = _parts(path), _parts(folder)
    return inner[:len(outer)] == outer


class Moves:
    """Where each moved file or folder went, and so where anything inside
    a moved folder went. ``moves`` is a dict or (old, new) pairs; for an
    old path named twice the first pair counts."""

    def __init__(self, moves):
        pairs = moves.items() if isinstance(moves, dict) else moves
        seen = set()
        found = []
        for old, new in pairs:
            key = tuple(_parts(old))
            if key not in seen:
                seen.add(key)
                found.append((list(key), os.path.abspath(old), os.path.abspath(new)))
        self._moves = sorted(found, key=lambda item: -len(item[0]))

    def __bool__(self) -> bool:
        return bool(self._moves)

    def new_place(self, path: str) -> Optional[str]:
        """Where ``path`` is now, or None when it did not move."""
        parts = _parts(path)
        for old_parts, _old, new in self._moves:
            if parts[:len(old_parts)] == old_parts:
                rest = os.path.normpath(os.path.abspath(path)).split(os.sep)[len(old_parts):]
                return os.path.join(new, *rest) if rest else new
        return None

    def inverse(self) -> "Moves":
        return Moves([(new, old) for _key, old, new in self._moves])


# -- rewriting ----------------------------------------------------------------


def _unambiguous(relative: str) -> str:
    """``relative`` with what would change a link's meaning
    percent-encoded: a "#" (a fragment would begin), a "?" (a query), a
    "%" (an escape) and a ":" in the first name (an address scheme).
    Read back (``_readings``), it is the same name again."""
    first = relative.split("/", 1)[0]
    if not any(ch in relative for ch in "#?%") and ":" not in first:
        return relative
    value = relative.replace("%", "%25").replace("#", "%23").replace("?", "%3F")
    first, slash, rest = value.partition("/")
    return first.replace(":", "%3A") + slash + rest


def _quoted(relative: str, safe: str) -> str:
    """``relative`` percent-encoded, a colon in its first name too."""
    value = quote(relative, safe=safe)
    first, slash, rest = value.partition("/")
    return first.replace(":", "%3A") + slash + rest


def _new_destination(link: _Link, relative: str, reading: str) -> str:
    """The text that replaces ``link``'s destination when it points at
    ``relative`` now, written the way the link was."""
    if reading.startswith("./") and not relative.startswith("../"):
        relative = "./" + relative
    if reading.endswith("/") and not relative.endswith("/"):
        relative += "/"
    # Percent-encoded throughout as written (not only where a name had
    # to be, as _unambiguous writes it): it stays so.
    path = link.path if link.style == "html" else _ESCAPE.sub(r"\1", link.path)
    encoded = (path != reading and _PERCENT.search(path) is not None
               and _unambiguous(reading) != path)
    if link.style == "html":
        value = _quoted(relative, _SAFE + "()") if encoded else _unambiguous(relative)
        value += link.suffix
        if link.quote_char:
            entity = "&quot;" if link.quote_char == '"' else "&#39;"
            return link.quote_char + value.replace(link.quote_char, entity) + link.quote_char
        if any(ch.isspace() or ch in "\"'=<>`" for ch in value):
            return '"' + value.replace('"', "&quot;") + '"'
        return value
    value = _quoted(relative, _SAFE) if encoded else _unambiguous(relative)
    if link.style == "angle":
        return "<" + value.replace("<", "%3C").replace(">", "%3E") + link.suffix + ">"
    if encoded or link.style == "loose":
        return value + link.suffix
    if link.style == "raw" and "\\" in link.path and " " not in value:
        return re.sub(r"([()])", r"\\\1", value) + link.suffix
    return markdown_writer._destination(value + link.suffix)


def link_changes(text: str, old_folder: str, new_folder: str, moves: Moves,
                 math: bool) -> List[Tuple[int, int, str]]:
    """What changes in ``text`` of a note that was in ``old_folder`` and
    is in ``new_folder`` now, for every relative link to point where its
    file is after ``moves``: (start, end, new destination)."""
    moved_note = _parts(old_folder) != _parts(new_folder)
    changes = []
    for link in _links(text, math):
        if not is_local(link.path):
            continue
        readings = _readings(link)
        chosen = None
        for reading in readings:
            target = _resolve(old_folder, reading)
            moved_to = moves.new_place(target)
            if moved_to is not None:
                chosen = (reading, moved_to)
                break
        if chosen is None:
            if not moved_note:
                continue
            # The file stayed; the note did not. Of the readings, the one
            # that names a file on the disk is meant; a link to nothing
            # (broken already, or words that only look like a link) stays
            # as it is.
            reading = next((r for r in readings if os.path.lexists(_resolve(old_folder, r))),
                           None)
            if reading is None:
                continue
            chosen = (reading, _resolve(old_folder, reading))
        reading, target = chosen
        if _nfc(_resolve(new_folder, reading)) == _nfc(target):
            continue                              # still right as written, case and all
        try:
            relative = _shortest_relative(target, new_folder).replace(os.sep, "/")
        except ValueError:                        # another drive on Windows
            continue
        changes.append((link.start, link.end, _new_destination(link, relative, reading)))
    return changes


def _shortest_relative(target: str, folder: str) -> str:
    """``target`` relative to ``folder``: by the names as given, or, when a
    symbolic link stands between them (a note moved into a sidebar folder
    by way of a link to it), by where they really are, whichever climbs
    fewer folders and still leads to the same file."""
    relative = os.path.relpath(target, folder)
    real_target = os.path.join(os.path.realpath(os.path.dirname(target)),
                               os.path.basename(target))
    real_folder = os.path.realpath(folder)
    if (real_target, real_folder) == (target, folder):
        return relative
    other = os.path.relpath(real_target, real_folder)
    if other.split(os.sep).count(os.pardir) < relative.split(os.sep).count(os.pardir) \
            and os.path.realpath(os.path.join(folder, other)) == os.path.realpath(target):
        return other
    return relative


def _applied(text: str, changes: List[Tuple[int, int, str]]) -> str:
    parts = []
    at = 0
    for start, end, replacement in changes:
        parts.append(text[at:start])
        parts.append(replacement)
        at = end
    parts.append(text[at:])
    return "".join(parts)


def _rewritten(text: str, old_folder: str, new_folder: str, moves: Moves,
               math: bool = False) -> str:
    """``text`` of a note that was in ``old_folder`` and is in
    ``new_folder`` now, with every relative link pointing where its file
    is after ``moves``."""
    return _applied(text, link_changes(text, old_folder, new_folder, moves, math))


# -- reading and writing notes ------------------------------------------------

_BOMS = ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"))
# A note larger than this is not read for its links (it is named as not
# updated instead): a note is writing, not a data dump.
NOTE_BYTES_LIMIT = 64 * 1024 * 1024


@dataclass
class _Note:
    """A note as read: its text, and how to write it back byte for byte.
    ``foreign`` is a note in an older encoding than UTF-8 (Latin-1,
    Windows): its bytes are kept as they are, and only plain ASCII may
    be written into it."""
    text: str
    encoding: str
    bom: bytes
    data: bytes
    foreign: bool = False

    def encoded(self, text: str) -> bytes:
        if self.foreign:
            return text.encode("utf-8", "surrogateescape")
        return self.bom + text.encode(self.encoding)


class _Unreadable(Exception):
    """A note that is there but could not be read for its links."""


def _regular_file_bytes(path: str, limit: int) -> bytes:
    """The bytes of the regular file at ``path`` (never a link, a pipe or
    a device: one of those would hang or flood the read), at most
    ``limit`` of them. Raises OSError, or _Unreadable when it is larger."""
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise OSError(errno.EINVAL, "not a regular file", path)
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) \
        | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OSError(errno.EINVAL, "not a regular file", path)
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise _Unreadable(path)
    return data


def _read_note(path: str) -> Optional[_Note]:
    """A note as written, line endings and all; None for a note locked
    with a password, one that is not a regular file of its own (a link
    to a file elsewhere is left to where it points) or one that cannot
    be read. Raises _Unreadable for one too large to read."""
    try:
        data = _regular_file_bytes(path, NOTE_BYTES_LIMIT)
    except OSError as error:
        log.warning("Cannot read %s to update its links: %s", path, error)
        return None
    if locked_files.is_locked(data):
        return None
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            body = data[len(bom):]
            break
    else:
        bom, encoding, body = b"", "utf-8", data
    try:
        text, _newline = atomic_file.decode_text_as_written(body, encoding=encoding)
        return _Note(text, encoding, bom, data)
    except (UnicodeDecodeError, ValueError):
        if bom or b"\x00" in data:
            log.info("Not updating links in %s: it is not text", path)
            return None
    # Text in an older encoding: read so that every byte comes back.
    return _Note(data.decode("utf-8", "surrogateescape"), "utf-8", b"", data, foreign=True)


def _write_note(path: str, note: _Note, text: str) -> bool:
    """Write ``text`` into ``path`` in the note's own encoding (see
    ``_write_bytes``)."""
    try:
        data = note.encoded(text)
    except UnicodeEncodeError as error:
        log.warning("Cannot update the links in %s: %s", path, error)
        return False
    return _write_bytes(path, note.data, data)


def _write_bytes(path: str, read: bytes, data: bytes) -> bool:
    """Replace the note at ``path`` by ``data``, unless it no longer holds
    ``read`` (the editor saved it since it was read) or is no longer a
    regular file of its own. The look and the replacement happen in the
    note's turn (atomic_file.one_writer), so a save of the editor cannot
    land between them and be replaced unseen."""
    try:
        with atomic_file.one_writer(path):
            if _regular_file_bytes(path, NOTE_BYTES_LIMIT) != read:
                log.info("Not updating the links in %s: it changed meanwhile", path)
                return False
            atomic_file.save_document(path, data)
        return True
    except (OSError, _Unreadable) as error:
        log.warning("Cannot update the links in %s: %s", path, error)
        return False


def is_note(path: str) -> bool:
    return file_kinds.extension(path) in file_kinds.NOTE_EXTS


def _is_rmarkdown(path: str) -> bool:
    return file_kinds.extension(path) == ".rmd"


def _own_regular_file(path: str) -> bool:
    """Whether ``path`` is a regular file itself, not a link to one."""
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def notes_under(root: str) -> Iterator[str]:
    """Every note in ``root`` and its folders, hidden folders and
    document packages left out, nearest first. Only notes that are files
    of their own: a link to a note, or to a folder, is not followed (the
    note it points to is someone else's, or found where it really is)."""
    if not root or not os.path.isdir(root):
        return
    seen = 0
    queue = deque([root])
    while queue:
        folder = queue.popleft()
        try:
            with os.scandir(folder) as found:
                children = list(found)
        except OSError:
            continue
        for child in children:
            seen += 1
            if seen > WALK_LIMIT:
                log.info("Stopped looking for notes after %d entries in %s", WALK_LIMIT, root)
                return
            if file_kinds.is_hidden_name(child.name):
                continue
            try:
                if child.is_dir(follow_symlinks=False):
                    if file_kinds.extension(child.name) not in file_kinds.PACKAGE_EXTS:
                        queue.append(child.path)
                elif is_note(child.name) and child.is_file(follow_symlinks=False):
                    yield child.path
            except OSError:
                continue


def _same_note(path: str, names: Iterable[str]) -> bool:
    parts = _parts(path)
    return any(_parts(name) == parts for name in names)


@dataclass
class NoteChange:
    """One note whose links a rewrite changes: where it is now (``note``)
    and was before the moves (``was``), the bytes it held when it was read,
    and the bytes it gets (None when it cannot be updated: too large to
    read, or in an older encoding that cannot hold a new name)."""
    note: str
    was: str
    read: bytes
    new: Optional[bytes]


@dataclass
class OpenNote:
    """A note the editor has open with unsaved changes, so the rewrite left
    it to the editor: where the note is now and was, and the moves (old
    path to new path, the note's own among them when it moved) for the
    editor to point the open document's links at, as one edit of its own."""
    note: str
    was: str
    moves: Dict[str, str]


@dataclass
class Rewrite:
    """What a rewrite did: the notes it changed, those whose links should
    have changed but could not be (a note in an older encoding that a new
    name with letters beyond ASCII would garble, one that changed on the
    disk meanwhile, could not be written or is too large to read), and
    the open notes with unsaved changes it left to the editor."""
    changed: List[str] = field(default_factory=list)
    not_updated: List[str] = field(default_factory=list)
    left_open: List[OpenNote] = field(default_factory=list)


def _notes_to_rewrite(roots: Iterable[str], moves: Dict[str, str]) -> List[str]:
    """The notes under ``roots``, and those that moved out of them, each
    once: one file reached by two names (a symbolic link to a folder
    holds it too) is taken by the first, the name under a sidebar folder."""
    notes: List[str] = []
    known = set()

    def take(note: str) -> None:
        try:
            info = os.lstat(note)
            key = (info.st_dev, info.st_ino)
        except OSError:
            key = tuple(_parts(note))
        if key not in known:
            known.add(key)
            notes.append(note)

    for root in roots:
        for note in notes_under(root):
            take(note)
    for new in moves.values():
        if os.path.islink(new):
            continue                    # a link moved; what it points to did not
        candidates = [new] if is_note(new) else (notes_under(new) if os.path.isdir(new) else [])
        for note in candidates:
            if _own_regular_file(note):
                take(note)
    return notes


def plan_rewrite(roots, moves: Dict[str, str],
                 progress: Optional[Callable[[int, int], None]] = None,
                 aliases: Iterable[Tuple[str, str]] = ()) -> List[NoteChange]:
    """The notes under ``roots`` (one folder or several) whose relative
    links must change for ``moves`` (old path to new path, files or
    folders, already done on the disk), the notes that moved themselves
    included, and what each should hold; nothing is written. Each note is
    read once; ``progress(done, total)`` is told as they are read. This is
    the long part, and needs nothing but the disk: a worker thread can
    do it. ``aliases`` are the same moves by other names (by way of a
    symbolic link to a folder), so notes that name them so follow too."""
    if not moves:
        return []
    pairs = list(moves.items()) + list(aliases)
    forward = Moves(pairs)
    backward = Moves([(new, old) for old, new in pairs])
    notes = _notes_to_rewrite([roots] if isinstance(roots, str) else roots or [], moves)
    planned: List[NoteChange] = []
    for number, note in enumerate(notes):
        if progress is not None:
            progress(number, len(notes))
        old_note = backward.new_place(note) or note
        try:
            read = _read_note(note)
        except _Unreadable:
            log.info("Not updating links in %s: it is too large", note)
            planned.append(NoteChange(note, old_note, b"", None))
            continue
        if read is None:
            continue
        changes = link_changes(read.text, os.path.dirname(old_note), os.path.dirname(note),
                               forward, _is_rmarkdown(note))
        if not changes:
            continue
        if read.foreign and not all(replacement.isascii() for _s, _e, replacement in changes):
            log.info("Not updating links in %s: its encoding cannot hold the new names", note)
            planned.append(NoteChange(note, old_note, read.data, None))
            continue
        try:
            new = read.encoded(_applied(read.text, changes))
        except UnicodeEncodeError:
            new = None
        planned.append(NoteChange(note, old_note, read.data, new))
    if progress is not None:
        progress(len(notes), len(notes))
    return planned


def apply_change(change: NoteChange, moves: Dict[str, str], unsaved, result: Rewrite) -> None:
    """Write one planned change in the note's turn (atomic_file.one_writer),
    so the editor's save of it is either seen here or comes after:
    unless the editor has the note open with unsaved changes (``unsaved``,
    a set of paths or a callable giving them, asked within the turn; the
    note is left to the editor then), or the note changed on the disk
    since it was read. ``result`` hears what happened."""
    if change.new is None:
        result.not_updated.append(change.note)
        return
    with atomic_file.one_writer(change.note):
        open_ones = unsaved() if callable(unsaved) else unsaved
        if open_ones and (_same_note(change.note, open_ones) or _same_note(change.was, open_ones)):
            result.left_open.append(OpenNote(change.note, change.was, dict(moves)))
            return
        if _write_bytes(change.note, change.read, change.new):
            result.changed.append(change.note)
        else:
            result.not_updated.append(change.note)


def rewrite_links(roots, moves: Dict[str, str], skip_notes=frozenset(),
                  progress: Optional[Callable[[int, int], None]] = None) -> Rewrite:
    """Point every relative link in the notes under ``roots`` where its
    file is after ``moves`` (``plan_rewrite``, then ``apply_change`` for
    each note), here and now. Notes in ``skip_notes`` (by their old or new
    path: the open notes with unsaved changes, a set or a callable asked
    again right before each note is written) are left to the editor."""
    result = Rewrite()
    for change in plan_rewrite(roots, moves, progress):
        apply_change(change, moves, skip_notes, result)
    return result


def rewrite_after_moves(root: str, moves: Dict[str, str],
                        skip_notes=frozenset()) -> List[str]:
    """``rewrite_links``, the notes it changed."""
    return rewrite_links(root, moves, skip_notes).changed


def rewrite_note(note: str, moves: Dict[str, str]) -> bool:
    """Point the links of the one note ``note`` (which stays where it is)
    at the new places of ``moves``; whether it changed."""
    try:
        read = _read_note(note)
    except _Unreadable:
        return False
    if read is None:
        return False
    folder = os.path.dirname(os.path.abspath(note))
    changes = link_changes(read.text, folder, folder, Moves(moves), _is_rmarkdown(note))
    if not changes or (read.foreign and not all(r.isascii() for _s, _e, r in changes)):
        return False
    return _write_note(note, read, _applied(read.text, changes))


def linking_to(notes: Iterable[str], paths: Iterable[str]) -> List[str]:
    """The notes of ``notes`` with a relative link to any of ``paths`` (or
    to anything in them), other than those among ``paths`` themselves:
    the open notes a rename or move left to the editor."""
    olds = [_parts(path) for path in paths]
    found = []
    for note in sorted(notes):
        if any(_parts(note) == old for old in olds):
            continue
        pointed = [_parts(target) for target in link_targets(note)]
        if any(p[:len(old)] == old for p in pointed for old in olds):
            found.append(note)
    return found


def link_targets(note: str) -> List[str]:
    """The absolute paths of the files ``note``'s relative links point at
    (whether they exist or not; a name that reads as an address only when
    no such file is there)."""
    try:
        read = _read_note(note)
    except _Unreadable:
        return []
    if read is None:
        return []
    folder = os.path.dirname(os.path.abspath(note))
    targets = []
    for link in _links(read.text, _is_rmarkdown(note)):
        if is_local(link.path):
            for reading in _readings(link):
                target = _resolve(folder, reading)
                if not _needs_a_file(reading) or os.path.lexists(target):
                    targets.append(target)
    return targets


def find_references(root: str, targets: List[str]) -> Dict[str, List[str]]:
    """For each of ``targets`` (files or folders), the notes under ``root``
    with a relative link to it or to anything in it."""
    wanted = [(target, _parts(target)) for target in targets]
    found: Dict[str, List[str]] = {target: [] for target in targets}
    for note in notes_under(root):
        pointed = [_parts(path) for path in link_targets(note)]
        for target, parts in wanted:
            if any(p[:len(parts)] == parts for p in pointed):
                found[target].append(note)
    return found
