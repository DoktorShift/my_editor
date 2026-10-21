# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading and writing imported Markdown: where its code is, where its
images' addresses are, code kept safe from a rewrite, and text written
so it reads back as text. Pure; one place for every importer step that
looks into Markdown (image copying, summaries, Markdown, wiki and
Bluesky sources), so they agree on what is code and what is syntax.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

# -- Markdown image destinations ------------------------------------------- #
#
# Where each image's address sits in Markdown, so it can be read and
# replaced without touching anything around it. Written for what
# imported Markdown holds (markdownify's output, and Markdown files and
# Nostr articles as people write them): ``![alt](url "title")`` with
# brackets in the alt and parentheses in the address
# (``Foo_(bar).jpg``), ``<url>`` destinations, reference images
# (``![alt][ref]`` and their ``[ref]: url`` line) and raw ``<img src>``.
# Code (fenced blocks and inline spans) is never read: an image there is
# an example, and rewriting it corrupted code samples.

# A fence at any depth: inside a list item (indented by the item) or an
# MDX component.
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_BLANK_LINE = re.compile(r"\n[ \t]*\n")
_REF_DEFINITION = re.compile(
    r"^ {0,3}\[([^\]]+)\]:[ \t]*(<[^>\n]*>|\S+)", re.MULTILINE)
_HTML_IMG_SRC = re.compile(
    r"""<img\b[^>]*?\ssrc\s*=\s*(["'])([^"']+)\1""", re.IGNORECASE)


def image_destinations(markdown: str) -> List[Tuple[int, int, str]]:
    """``(start, end, url)`` of every image address outside code, in
    order; ``markdown[start:end]`` is the address as written."""
    code = code_spans(markdown)

    def in_code(index: int) -> bool:
        return any(start <= index < end for start, end in code)

    definitions = {}
    for match in _REF_DEFINITION.finditer(markdown):
        if not in_code(match.start()):
            start, end = match.span(2)
            if markdown[start] == "<":
                start, end = start + 1, end - 1
            definitions.setdefault(reference_label(match.group(1)), (start, end))

    found: List[Tuple[int, int, str]] = []
    index = markdown.find("![")
    while index != -1:
        if not in_code(index) and (index == 0 or markdown[index - 1] != "\\"):
            span = _image_at(markdown, index, definitions)
            if span is not None:
                found.append((span[0], span[1], markdown[span[0]:span[1]]))
        index = markdown.find("![", index + 2)
    for match in _HTML_IMG_SRC.finditer(markdown):
        if not in_code(match.start()):
            found.append((match.start(2), match.end(2), match.group(2)))
    # Two images may share one reference: its address is there once.
    return sorted(set(found))


def _image_at(markdown: str, index: int, definitions: dict) -> Optional[Tuple[int, int]]:
    alt_end = _closing(markdown, index + 1, "[", "]")
    if alt_end is None or alt_end + 1 >= len(markdown):
        return None
    after = alt_end + 1
    if markdown[after] == "[":
        label_end = _closing(markdown, after, "[", "]")
        if label_end is None:
            return None
        label = markdown[after + 1:label_end] or markdown[index + 2:alt_end]
        return definitions.get(reference_label(label))
    if markdown[after] != "(":
        return definitions.get(reference_label(markdown[index + 2:alt_end]))
    position = after + 1
    while position < len(markdown) and markdown[position] in " \t\n":
        position += 1
    if position < len(markdown) and markdown[position] == "<":
        end = markdown.find(">", position)
        return (position + 1, end) if end != -1 else None
    start, depth = position, 0
    while position < len(markdown):
        char = markdown[position]
        if char == "\\":
            position += 2
            continue
        if char in " \t\n":
            break
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                break
            depth -= 1
        position += 1
    return (start, position) if position > start else None


def _closing(text: str, index: int, opening: str, closing: str) -> Optional[int]:
    """Index of the bracket that closes the one at ``index``."""
    depth = 0
    position = index
    while position < len(text):
        char = text[position]
        if char == "\\":
            position += 2
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return position
        elif char == "\n" and text[position + 1:position + 2] == "\n":
            return None
        position += 1
    return None


def code_block_spans(markdown: str) -> List[Tuple[int, int]]:
    """``(start, end)`` of every fenced code block: a fence of three or
    more backticks or tildes, closed by a fence of the same character at
    least as long (an unclosed one runs to the end, as CommonMark reads
    it)."""
    spans: List[Tuple[int, int]] = []
    offset, fence, fence_start = 0, "", 0
    for line in markdown.splitlines(keepends=True):
        match = _FENCE.match(line)
        if fence:
            closing = match.group(1) if match else ""
            if closing and closing[0] == fence[0] and len(closing) >= len(fence) \
                    and not line.strip()[len(closing):].strip():
                spans.append((fence_start, offset + len(line)))
                fence = ""
        elif match:
            fence, fence_start = match.group(1), offset
        offset += len(line)
    if fence:
        spans.append((fence_start, len(markdown)))
    return spans


def code_spans(markdown: str) -> List[Tuple[int, int]]:
    """``(start, end)`` of every fenced code block and every inline code
    span (a run of backticks closed by a run of the same length, within
    one paragraph), in order."""
    spans = code_block_spans(markdown)
    blocks = list(spans)
    position = 0
    while True:
        start = markdown.find("`", position)
        if start == -1:
            break
        inside = next((end for begin, end in blocks if begin <= start < end), None)
        if inside is not None:
            position = inside
            continue
        run = len(markdown[start:]) - len(markdown[start:].lstrip("`"))
        end = markdown.find("`" * run, start + run)
        while end != -1 and markdown[end + run:end + run + 1] == "`":
            end = markdown.find("`" * run, end + run + 1)
        if end == -1 or _BLANK_LINE.search(markdown, start, end):
            position = start + run
            continue
        spans.append((start, end + run))
        position = end + run
    return sorted(spans)


def without_code_blocks(markdown: str) -> str:
    """``markdown`` without its fenced code blocks, for reading its prose
    (inline code is part of a sentence and stays)."""
    for start, end in reversed(code_block_spans(markdown)):
        markdown = markdown[:start] + markdown[end:]
    return markdown


# A placeholder for code kept aside: private-use characters, which no
# document writes and which the HTML converter passes through untouched.
_CODE_TOKEN = re.compile("\ue000(\\d+)\ue001")
# What Markdown reads as syntax in running text: a character a writer
# typed there is escaped, and an escape is read back as the character.
_SPECIAL = re.compile(r"([\\`*_\[\]<~])")
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!<>|~=])")


def protect(markdown: str, spans: Iterable[Tuple[int, int]],
            kept: List[str]) -> str:
    """``markdown`` with each span (code, from :func:`code_spans`) set
    aside in ``kept`` behind a placeholder no rewrite touches."""
    out, last = [], 0
    for start, end in sorted(spans):
        if start < last:
            continue
        out.append(markdown[last:start])
        kept.append(markdown[start:end])
        out.append(f"\ue000{len(kept) - 1}\ue001")
        last = end
    out.append(markdown[last:])
    return "".join(out)


def restore(text: str, kept: List[str]) -> str:
    """The code :func:`protect` set aside, back in place (also code set
    aside inside code set aside later)."""
    def back(match: re.Match) -> str:
        index = int(match.group(1))
        return kept[index] if index < len(kept) else match.group(0)

    for _round in range(len(kept) + 1):
        restored = _CODE_TOKEN.sub(back, text)
        if restored == text:
            break
        text = restored
    return text


def escape(text: str) -> str:
    """Plain text as Markdown that reads back as the same text."""
    return _SPECIAL.sub(r"\\\1", text)


def unescape(text: str) -> str:
    """Markdown's backslash escapes read back as their characters."""
    return _ESCAPE.sub(r"\1", text)


def reference_label(text: str) -> str:
    """A reference label as Markdown matches it: case and runs of
    whitespace do not count."""
    return " ".join(text.split()).casefold()
