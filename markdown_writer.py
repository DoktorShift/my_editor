#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One way from the editor's document to Markdown, for every place that needs it.

Saving a .md file, publishing an article and saving a draft all write the
same Markdown, from :func:`document_to_markdown`. A short note (kind 1) is
plain text by convention, so :func:`document_to_note_text` writes the same
walk without markup: lists keep their markers, links keep their address,
and nothing shows as stray asterisks in an app that does not render
Markdown.

What is written follows what the editor shows ("Markdown first"):

- Bold, italic, strikethrough, inline code and links become their
  Markdown. Underline and colors have no Markdown and stay in local
  files only; they are dropped here.
- Headings, block quotes, fenced code, horizontal rules, tables and task
  lists that a .md file brought in are written back as such.
- Lists: Qt's own lists (from a .md file) and the editor's typed bullets
  ("    • item", see doc_walk) both become "- item", nested by depth.
- Lines: a paragraph that came from Markdown (it carries paragraph
  spacing) ends with a blank line. Lines typed one under the other keep
  being separate lines (a hard line break), and an empty line between
  them starts a new paragraph. Shift+Enter is a hard line break too.
  Leading spaces that indent a typed line become non-breaking spaces,
  so the indent shows instead of turning the line into a code block.
- Characters Markdown would read as markup in typed text (``*``, ``_``
  next to a word boundary, a backtick, brackets, ``<``, ``~``, a leading
  ``#`` or ``>``) are escaped, so they show as typed. A line starting
  with "- " or "1. " is left alone: it reads as the list it looks like.
  Web addresses and nostr: links are never escaped, so they stay links.

Images are written by the caller's ``image_target(QTextImageFormat)``,
which returns the text to put in the image's place (``![alt](url)`` or a
bare URL) or None to leave it out. The caller decides where an image
points (a Blossom URL, a sidecar file); this module never does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from PySide6.QtGui import (
    QFont, QTextBlockFormat, QTextCharFormat, QTextFormat, QTextListFormat, QTextTable,
)

from doc_walk import (
    BULLET_MARKER,
    LINE_SEPARATOR,
    OBJECT_REPLACEMENT,
    bullet_depth,
    iter_block_runs,
    iter_blocks,
    parse_bullet_line,
    skip_prefix,
)

ImageTarget = Callable[[object], Optional[str]]

MARKDOWN = "markdown"
NOTE = "note"

# Non-breaking space: keeps a typed indent visible in rendered Markdown.
NBSP = "\u00a0"
# Indent per nesting level of a list. Four columns is past the content of
# both "- " and "1. " markers, and short of the four extra columns that
# would turn a nested item into a code block.
LIST_INDENT = 4

_ORDERED_STYLES = {
    QTextListFormat.Style.ListDecimal,
    QTextListFormat.Style.ListLowerAlpha,
    QTextListFormat.Style.ListUpperAlpha,
    QTextListFormat.Style.ListLowerRoman,
    QTextListFormat.Style.ListUpperRoman,
}

# Web addresses and nostr: links, written as they are: escaping inside
# them would break autolinking in the apps that read them.
_LINK_RE = re.compile(r"(?:https?://|wss?://|nostr:)\S+", re.IGNORECASE)
_ALWAYS_ESCAPE = set("\\*`[]<~")
_LINE_START_ESCAPE = re.compile(r"^(#|>|=+\s*$)")


# --------------------------------------------------------------------------- #
# The walk: the document as a list of blocks                                  #
# --------------------------------------------------------------------------- #

@dataclass
class _Span:
    text: str = ""
    bold: bool = False
    italic: bool = False
    strike: bool = False
    code: bool = False
    href: str = ""
    raw: bool = False          # an image's text from the caller, written as is


@dataclass
class _Block:
    kind: str                  # paragraph, heading, item, quote, code, rule, table, empty
    spans: List[_Span] = field(default_factory=list)
    level: int = 0             # heading level, quote level, list depth
    ordered: bool = False
    number: int = 1
    task: Optional[bool] = None
    spaced: bool = False       # carries paragraph spacing (came from Markdown)
    indent: str = ""           # leading spaces typed before the text
    fence: str = ""
    language: str = ""
    code_text: str = ""
    rows: List[List[List[_Span]]] = field(default_factory=list)
    bullet_text: str = ""      # a typed bullet line, exactly as typed
    group: int = -1            # which list an item belongs to


def _is_bold(fmt) -> bool:
    return fmt.fontWeight() >= QFont.Weight.DemiBold


def _is_code(fmt) -> bool:
    return bool(fmt.fontFixedPitch())


def _spans_of(runs, image_target: Optional[ImageTarget]) -> List[_Span]:
    spans: List[_Span] = []
    for text, fmt in runs:
        if fmt.isImageFormat():
            if image_target is None:
                continue
            target = image_target(fmt.toImageFormat())
            if target:
                spans.append(_Span(text=target, raw=True))
            continue
        text = text.replace(OBJECT_REPLACEMENT, "")
        if not text:
            continue
        href = fmt.anchorHref() if fmt.isAnchor() else ""
        spans.append(_Span(text=text, bold=_is_bold(fmt), italic=fmt.fontItalic(),
                           strike=fmt.fontStrikeOut(), code=_is_code(fmt),
                           href=href or ""))
    return _merged(spans)


def _plain_weight(span: _Span) -> _Span:
    span.bold = False
    return span


def _merged(spans: List[_Span]) -> List[_Span]:
    """Join neighbours that read the same, so one bold word split across
    two fragments is not written as two bold words."""
    out: List[_Span] = []
    for span in spans:
        if (out and not span.raw and not out[-1].raw
                and (out[-1].bold, out[-1].italic, out[-1].strike, out[-1].code,
                     out[-1].href) == (span.bold, span.italic, span.strike, span.code,
                                       span.href)):
            out[-1].text += span.text
        else:
            out.append(span)
    return out


def _block_runs(block):
    return list(iter_block_runs(block))


def _paragraph_block(block, image_target) -> _Block:
    fmt = block.blockFormat()
    runs = _block_runs(block)
    text = block.text()
    spaced = fmt.topMargin() > 0 or fmt.bottomMargin() > 0
    spaces, is_bullet = parse_bullet_line(text)
    if is_bullet:
        return _Block("item", _spans_of(skip_prefix(runs, spaces + len(BULLET_MARKER)),
                                        image_target),
                      level=bullet_depth(spaces), spaced=spaced, bullet_text=text)
    if not text.strip(OBJECT_REPLACEMENT + " \t") and not any(
            f.isImageFormat() for _t, f in runs):
        return _Block("empty", spaced=spaced)
    indent = ""
    if spaces:
        indent = " " * spaces
        runs = skip_prefix(runs, spaces)
    heading = fmt.headingLevel()
    if heading:
        # A heading is bold by its own format; that is not emphasis.
        spans = _merged([_plain_weight(span) for span in _spans_of(runs, image_target)])
        return _Block("heading", spans, level=heading, spaced=True)
    quote = fmt.property(QTextFormat.Property.BlockQuoteLevel)
    if isinstance(quote, int) and quote > 0:
        return _Block("quote", _spans_of(runs, image_target), level=quote, spaced=spaced)
    return _Block("paragraph", _spans_of(runs, image_target), spaced=spaced, indent=indent)


def _list_block(block, text_list, image_target) -> _Block:
    style = text_list.format().style()
    marker = block.blockFormat().marker()
    task = None
    if marker == QTextBlockFormat.MarkerType.Checked:
        task = True
    elif marker == QTextBlockFormat.MarkerType.Unchecked:
        task = False
    start = text_list.format().start() if hasattr(text_list.format(), "start") else 1
    return _Block("item", _spans_of(_block_runs(block), image_target),
                  level=max(1, text_list.format().indent()),
                  ordered=style in _ORDERED_STYLES,
                  number=max(1, start) + text_list.itemNumber(block),
                  task=task, spaced=block.blockFormat().bottomMargin() > 0,
                  group=text_list.objectIndex())


def _table_block(table: QTextTable, image_target) -> _Block:
    rows = []
    for r in range(table.rows()):
        row = []
        for c in range(table.columns()):
            cell = table.cellAt(r, c)
            spans: List[_Span] = []
            it = cell.begin()
            first = True
            while not it.atEnd():
                block = it.currentBlock()
                if block.isValid():
                    if not first:
                        spans.append(_Span(text=" "))
                    spans.extend(_spans_of(_block_runs(block), image_target))
                    first = False
                it += 1
            if r == 0:
                # The header row is bold by its own format.
                spans = [_plain_weight(span) for span in spans]
            row.append(_merged(spans))
        rows.append(row)
    return _Block("table", rows=rows, spaced=True)


def _blocks_of_frame(frame, image_target) -> List[_Block]:
    blocks: List[_Block] = []
    it = frame.begin()
    while not it.atEnd():
        child = it.currentFrame()
        block = it.currentBlock()
        if child is not None:
            if isinstance(child, QTextTable):
                blocks.append(_table_block(child, image_target))
            else:
                blocks.extend(_blocks_of_frame(child, image_target))
        elif block.isValid():
            blocks.append(_one_block(block, image_target))
        it += 1
    return _joined_code(blocks)


def _one_block(block, image_target) -> _Block:
    fmt = block.blockFormat()
    if fmt.hasProperty(QTextFormat.Property.BlockTrailingHorizontalRulerWidth):
        return _Block("rule", spaced=True)
    fence = fmt.property(QTextFormat.Property.BlockCodeFence)
    if fence:
        language = fmt.property(QTextFormat.Property.BlockCodeLanguage) or ""
        text = block.text().replace(LINE_SEPARATOR, "\n").replace(OBJECT_REPLACEMENT, "")
        return _Block("code", fence=str(fence), language=str(language), code_text=text,
                      spaced=True)
    text_list = block.textList()
    if text_list is not None:
        return _list_block(block, text_list, image_target)
    return _paragraph_block(block, image_target)


def _joined_code(blocks: List[_Block]) -> List[_Block]:
    """Qt keeps each line of a fenced block as a block of its own."""
    out: List[_Block] = []
    for block in blocks:
        previous = out[-1] if out else None
        if (block.kind == "code" and previous is not None and previous.kind == "code"
                and previous.fence == block.fence and previous.language == block.language):
            previous.code_text += "\n" + block.code_text
        else:
            out.append(block)
    return out


def document_blocks(doc, image_target: Optional[ImageTarget] = None) -> List[_Block]:
    """The document as the blocks both writers share. Exposed for tests."""
    return _blocks_of_frame(doc.rootFrame(), image_target)


# --------------------------------------------------------------------------- #
# Markdown                                                                     #
# --------------------------------------------------------------------------- #

def _escape_plain(text: str) -> str:
    out = []
    for i, ch in enumerate(text):
        if ch in _ALWAYS_ESCAPE:
            out.append("\\" + ch)
        elif ch == "_":
            before = text[i - 1] if i else " "
            after = text[i + 1] if i + 1 < len(text) else " "
            # Inside a word an underscore never starts emphasis.
            out.append("_" if before.isalnum() and after.isalnum() else "\\_")
        else:
            out.append(ch)
    return "".join(out)


def _escape_text(text: str) -> str:
    """Typed text, escaped where Markdown would read it as markup, with
    every web address and nostr: link left exactly as written."""
    out = []
    last = 0
    for match in _LINK_RE.finditer(text):
        out.append(_escape_plain(text[last:match.start()]))
        out.append(match.group(0))
        last = match.end()
    out.append(_escape_plain(text[last:]))
    return "".join(out)


def _code_span(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") or (
        text.startswith(" ") and text.endswith(" ") and text.strip()) else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def _wrap(text: str, marker: str) -> str:
    """``**text**`` with the spaces kept outside the markers, which is
    where Markdown needs them for the emphasis to count."""
    core = text.strip(" ")
    if not core:
        return text
    lead = text[:len(text) - len(text.lstrip(" "))]
    trail = text[len(text.rstrip(" ")):]
    return f"{lead}{marker}{core}{marker}{trail}"


def _inline_markdown(spans: List[_Span], hard_break: str = "  \n") -> str:
    out = []
    i = 0
    while i < len(spans):
        span = spans[i]
        if span.raw:
            out.append(span.text)
            i += 1
            continue
        if span.href:
            # One link around every neighbour that points at the same place.
            group = []
            while i < len(spans) and spans[i].href == span.href and not spans[i].raw:
                group.append(spans[i])
                i += 1
            inner = "".join(_styled(s) for s in group)
            label = inner.replace(LINE_SEPARATOR, " ")
            href = span.href.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
            out.append(f"[{label}]({href})")
            continue
        out.append(_styled(span))
        i += 1
    # A line break at the very end of a block breaks nothing, and Markdown
    # drops it; trailing spaces there mean nothing either.
    text = "".join(out).rstrip(LINE_SEPARATOR + " ")
    return text.replace(LINE_SEPARATOR, hard_break)


def _styled(span: _Span) -> str:
    if span.code:
        parts = span.text.split(LINE_SEPARATOR)
        return LINE_SEPARATOR.join(_code_span(p) for p in parts if p)
    pieces = []
    for part in span.text.split(LINE_SEPARATOR):
        text = _escape_text(part)
        if span.strike:
            text = _wrap(text, "~~")
        if span.italic:
            text = _wrap(text, "*")
        if span.bold:
            text = _wrap(text, "**")
        pieces.append(text)
    return LINE_SEPARATOR.join(pieces)


def _escape_line_start(line: str) -> str:
    if _LINE_START_ESCAPE.match(line):
        return "\\" + line
    return line


def _table_markdown(block: _Block) -> str:
    if not block.rows:
        return ""
    width = max(len(row) for row in block.rows)

    def cell(spans):
        text = _inline_markdown(spans, hard_break=" ")
        return text.replace("|", "\\|").replace("\n", " ").strip() or " "

    lines = []
    for index, row in enumerate(block.rows):
        cells = [cell(spans) for spans in row] + [" "] * (width - len(row))
        lines.append("| " + " | ".join(cells) + " |")
        if index == 0:
            lines.append("|" + "|".join([" --- "] * width) + "|")
    return "\n".join(lines)


def _markdown_lines(block: _Block) -> str:
    if block.kind == "heading":
        return "#" * min(6, block.level) + " " + _inline_markdown(block.spans, " ").strip()
    if block.kind == "rule":
        return "---"
    if block.kind == "code":
        fence = "```"
        while fence in block.code_text:
            fence += "`"
        return f"{fence}{block.language}\n{block.code_text}\n{fence}"
    if block.kind == "table":
        return _table_markdown(block)
    if block.kind == "quote":
        prefix = "> " * block.level
        body = _inline_markdown(block.spans)
        return "\n".join(prefix + line if line else prefix.rstrip()
                         for line in body.split("\n"))
    if block.kind == "item":
        marker = f"{block.number}." if block.ordered else "-"
        if block.task is not None:
            marker += " [x]" if block.task else " [ ]"
        pad = " " * (LIST_INDENT * (block.level - 1))
        body = _inline_markdown(block.spans)
        cont = " " * (len(pad) + len(marker) + 1)
        lines = body.split("\n")
        return "\n".join([f"{pad}{marker} {lines[0]}".rstrip()]
                         + [cont + line for line in lines[1:]])
    # paragraph
    body = _inline_markdown(block.spans)
    lines = body.split("\n")
    lines[0] = NBSP * len(block.indent) + lines[0] if block.indent else _escape_line_start(
        lines[0])
    return "\n".join(lines[:1] + [_escape_line_start(line) for line in lines[1:]])


def document_to_markdown(doc, image_target: Optional[ImageTarget] = None) -> str:
    """The document as Markdown, for a .md file, an article or a draft."""
    blocks = document_blocks(doc, image_target)
    out: List[str] = []
    previous: Optional[_Block] = None
    for block in blocks:
        if block.kind == "empty":
            previous = block
            continue
        text = _markdown_lines(block)
        if previous is not None and out:
            out.append(_separator(previous, block))
        out.append(text)
        previous = block
    return "".join(out).rstrip("\n") + ("\n" if out else "")


def _new_list(previous: _Block, block: _Block) -> bool:
    return previous.group != block.group and previous.level == block.level


def _separator(previous: _Block, block: _Block) -> str:
    """What goes between two written blocks: a line break, or a blank line."""
    if previous.kind == "empty":
        return "\n\n"
    if previous.kind == "item" and block.kind == "item":
        # A different list at the same depth is a list of its own.
        return "\n\n" if _new_list(previous, block) else "\n"
    if previous.kind == "quote" and block.kind == "quote":
        # Two paragraphs of one quote, or two quoted lines typed one under
        # the other: either way they stay in the quote.
        if previous.spaced:
            return "\n" + ("> " * min(previous.level, block.level)).rstrip() + "\n"
        return "  \n"
    if (previous.kind == "paragraph" and block.kind == "paragraph"
            and not previous.spaced and not block.spaced):
        # Typed one under the other: separate lines, one paragraph.
        return "  \n"
    return "\n\n"


# --------------------------------------------------------------------------- #
# Plain text for a short note                                                  #
# --------------------------------------------------------------------------- #

def _inline_note(spans: List[_Span]) -> str:
    out = []
    i = 0
    while i < len(spans):
        span = spans[i]
        if span.href:
            group = []
            while i < len(spans) and spans[i].href == span.href and not spans[i].raw:
                group.append(spans[i].text)
                i += 1
            label = "".join(group).replace(LINE_SEPARATOR, " ").strip()
            href = span.href
            if not label or label == href or label.rstrip("/") == href.rstrip("/"):
                out.append(href)
            else:
                out.append(f"{label} ({href})")
            continue
        out.append(span.text)
        i += 1
    return "".join(out).replace(LINE_SEPARATOR, "\n")


def _note_lines(block: _Block) -> str:
    if block.kind == "rule":
        return "---"
    if block.kind == "code":
        return block.code_text
    if block.kind == "table":
        return "\n".join(" | ".join(_inline_note(spans).strip() for spans in row)
                         for row in block.rows)
    if block.kind == "quote":
        return "\n".join("> " * block.level + line
                         for line in _inline_note(block.spans).split("\n"))
    if block.kind == "item":
        if block.bullet_text:
            # Typed bullets stay exactly as typed, the way they look.
            return block.bullet_text.split(BULLET_MARKER, 1)[0] + BULLET_MARKER + \
                _inline_note(block.spans)
        marker = f"{block.number}." if block.ordered else BULLET_MARKER.strip()
        if block.task is not None:
            marker = ("[x]" if block.task else "[ ]") if not block.ordered else marker
        pad = "  " * (block.level - 1)
        return f"{pad}{marker} {_inline_note(block.spans)}"
    if block.kind == "heading":
        return _inline_note(block.spans).strip()
    return block.indent + _inline_note(block.spans)


def document_to_note_text(doc, image_target: Optional[ImageTarget] = None) -> str:
    """The document as plain text for a short note: what it says, with list
    markers and link addresses, and no Markdown markup."""
    blocks = document_blocks(doc, image_target)
    out: List[str] = []
    previous: Optional[_Block] = None
    for block in blocks:
        if block.kind == "empty":
            if previous is not None and previous.kind != "empty":
                out.append("\n")
            previous = block
            continue
        if previous is not None and out:
            spaced = previous.spaced and block.spaced and previous.kind != "empty"
            separate_items = (previous.kind == "item" and block.kind == "item"
                              and not _new_list(previous, block))
            out.append("\n\n" if spaced and not separate_items else "\n")
        out.append(_note_lines(block))
        previous = block
    return "".join(out).strip("\n")


def has_local_only_formatting(doc) -> bool:
    """Whether the document holds what Markdown cannot carry: underline,
    text color or highlight. A link's own color and underline are how it
    looks, not formatting, so links do not count."""
    for block in iter_blocks(doc):
        for _text, fmt in iter_block_runs(block):
            if fmt.isImageFormat() or fmt.isAnchor():
                continue
            if (fmt.fontUnderline()
                    or fmt.hasProperty(QTextCharFormat.Property.ForegroundBrush)
                    or fmt.hasProperty(QTextCharFormat.Property.BackgroundBrush)):
                return True
    return False


def document_to(doc, flavor: str, image_target: Optional[ImageTarget] = None) -> str:
    """:func:`document_to_markdown` or :func:`document_to_note_text`."""
    if flavor == NOTE:
        return document_to_note_text(doc, image_target)
    return document_to_markdown(doc, image_target)


__all__: Tuple[str, ...] = (
    "MARKDOWN", "NOTE", "document_blocks", "document_to", "document_to_markdown",
    "document_to_note_text", "has_local_only_formatting",
)
