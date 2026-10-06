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
- Headings, block quotes (with the lists and headings inside them),
  fenced and indented code, horizontal rules, tables and task lists that
  a .md file brought in are written back as such.
- Lists: Qt's own lists (from a .md file) and the editor's typed bullets
  ("    • item", see doc_walk) both become "- item", nested by depth.
- Lines: a paragraph that came from Markdown (it carries paragraph
  spacing) ends with a blank line, and so does every line typed one
  under the other: a blank line is the break every Nostr reader shows
  alike (NIP-23 asks for no hard line breaks inside a paragraph). Only
  Shift+Enter, an explicit line break, is written as one.
- Typed text is Markdown: what is typed is written as typed, so
  "## Heading", "[label](url)", a footnote mark ("[^1]", and its
  "[^1]: note") or a code fence typed by hand reach Nostr as such (the
  preview shows what they become). A web address
  that holds a character Markdown would read as emphasis is written as
  ``<address>``, which every reader keeps intact.
- A link that shows its own address (a pasted web address, an email) is
  written bare too, where readers find it by themselves: a picture or a
  video whose address stands alone on its line plays in the apps that
  look for one.

Images are written by the caller's ``image_target(QTextImageFormat)``,
which returns the text to put in the image's place (``![alt](url)`` or a
bare URL) or None to leave it out. The caller decides where an image
points (a Blossom URL, a sidecar file); this module never does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, List, Optional, Tuple

from PySide6.QtGui import (
    QFont, QTextBlockFormat, QTextCharFormat, QTextDocument, QTextFormat, QTextListFormat,
    QTextTable,
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
from rich_text import normalize_after_markdown_load

ImageTarget = Callable[[object], Optional[str]]

# How Markdown is read back into the editor (.md files, article drafts):
# GitHub's dialect without its underline extension, which reads
# ``_word_`` as underline (a local-only format this writer leaves out)
# where every Nostr reader shows italics.
_MD_FLAG_UNDERLINE = 0x4000
READ_FEATURES = QTextDocument.MarkdownFeature(
    QTextDocument.MarkdownFeature.MarkdownDialectGitHub.value & ~_MD_FLAG_UNDERLINE)

MARKDOWN = "markdown"
NOTE = "note"

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

# A bare web address, and the characters that make a Markdown reader see
# emphasis or code inside one.
_WEB_ADDRESS = re.compile(r"(?<![<(\w])(?:https?|wss?)://[^\s<>]+", re.IGNORECASE)
_MARKUP_IN_ADDRESS = set("_*~`")


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
    quote: int = 0             # how deep in block quotes it sits


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
    written = _block_kind(block, image_target)
    quote = block.blockFormat().property(QTextFormat.Property.BlockQuoteLevel)
    if isinstance(quote, int) and quote > 0 and written.kind != "quote":
        # A list, heading or code block inside a quote stays in it.
        written.quote = quote
    return written


def _block_kind(block, image_target) -> _Block:
    fmt = block.blockFormat()
    if fmt.hasProperty(QTextFormat.Property.BlockTrailingHorizontalRulerWidth):
        return _Block("rule", spaced=True)
    fence = fmt.property(QTextFormat.Property.BlockCodeFence)
    # Indented code carries a language property (empty) and no fence.
    if fence or fmt.hasProperty(QTextFormat.Property.BlockCodeLanguage):
        language = fmt.property(QTextFormat.Property.BlockCodeLanguage) or ""
        text = block.text().replace(LINE_SEPARATOR, "\n").replace(OBJECT_REPLACEMENT, "")
        return _Block("code", fence=str(fence or "`"), language=str(language),
                      code_text=text, spaced=True)
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
                and previous.fence == block.fence and previous.language == block.language
                and previous.quote == block.quote):
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

def _protect_addresses(text: str) -> str:
    """Typed text as typed, except a web address holding a character a
    Markdown reader would take for emphasis or code: that one is written
    as ``<address>``, which keeps it intact everywhere."""
    def protect(match):
        address = match.group(0)
        if any(ch in _MARKUP_IN_ADDRESS for ch in address):
            return f"<{address}>"
        return address
    return _WEB_ADDRESS.sub(protect, text)


def _destination(href: str) -> str:
    """A link's address as Markdown accepts it: in ``<…>`` when it holds
    a space or a parenthesis, so it is never cut short or changed."""
    if any(ch in href for ch in " ()"):
        return "<" + href.replace("<", "%3C").replace(">", "%3E") + ">"
    return href


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


def _own_address(words: str, href: str) -> str:
    """The address a link shows when its words are its own address (a web
    address, ``www.…`` or an email), as the words spell it; else ""."""
    if words == href:
        return words
    if href == "http://" + words and words.lower().startswith("www."):
        return words
    if href == "mailto:" + words and "@" in words:
        return words
    return ""


@lru_cache(maxsize=4096)
def _reads_back_whole(address: str, href: str) -> bool:
    """Whether Qt's reader (the editor's, the preview's, every reopen)
    reads ``address``, written bare, back as one link to ``href`` over all
    of it. It takes only some characters in a bare address: one with a
    percent sign, a port, an @, a + or an = is not linked at all, one with
    a comma or ! is cut there."""
    doc = QTextDocument()
    doc.setMarkdown(address, READ_FEATURES)
    block = doc.begin()
    runs = list(iter_block_runs(block))
    return (doc.blockCount() == 1 and block.text() == address and bool(runs)
            and all(fmt.isAnchor() and fmt.anchorHref() == href for _text, fmt in runs))


def _autolink(address: str, href: str, before: str, following: Optional[_Span]) -> str:
    """A link that shows its own address, written so every reader links it.

    Bare, the way it was typed, where a reader finds it by itself: a web
    address or an email between spaces, with no character a reader would
    take for emphasis, and only one that Qt's reader takes back whole.
    Readers that look for a bare address (a picture or a video alone on
    its line becomes a player) find it there. Anywhere else it is written
    ``<address>``, which every reader keeps whole.
    """
    is_email = "@" in address and "://" not in address
    findable = (address.lower().startswith(("https://", "http://", "www.")) or is_email)
    if (findable and not any(ch in _MARKUP_IN_ADDRESS for ch in address)
            and (not before or before[-1].isspace() or before[-1] == "(")
            and _ends_a_bare_address(address, following)
            and _reads_back_whole(address, href)):
        return address
    return f"<{address}>" if is_email else f"<{href}>"


def _ends_a_bare_address(address: str, following: Optional[_Span]) -> bool:
    """Whether the text after a bare address leaves it whole: nothing, a
    space, or punctuation that readers leave out of an address."""
    if following is None:
        return True
    if following.raw or following.href or following.bold or following.italic \
            or following.strike or following.code:
        return False
    text = following.text
    if text[0].isspace():
        return True
    rest = text.lstrip(".,:;!?" + ("" if "(" in address or ")" in address else ")"))
    return len(rest) < len(text) and (not rest or rest[0].isspace())


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
            words = "".join(s.text for s in group).replace(LINE_SEPARATOR, " ")
            address = _own_address(words.strip(), span.href)
            if address and not any(s.bold or s.italic or s.strike for s in group):
                following = spans[i] if i < len(spans) else None
                out.append(_autolink(address, span.href, "".join(out), following))
                continue
            inner = "".join(_styled(s) for s in group)
            label = inner.replace(LINE_SEPARATOR, " ").replace("]", "\\]")
            out.append(f"[{label}]({_destination(span.href)})")
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
        text = _protect_addresses(part)
        if span.strike:
            text = _wrap(text, "~~")
        if span.italic:
            text = _wrap(text, "*")
        if span.bold:
            text = _wrap(text, "**")
        pieces.append(text)
    return LINE_SEPARATOR.join(pieces)


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
    text = _block_markdown(block)
    if not block.quote:
        return text
    prefix = "> " * block.quote
    return "\n".join(prefix + line if line else prefix.rstrip() for line in text.split("\n"))


def _block_markdown(block: _Block) -> str:
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
    # paragraph, as typed
    return block.indent + _inline_markdown(block.spans)


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


def _quote_depth(block: _Block) -> int:
    return block.level if block.kind == "quote" else block.quote


def _separator(previous: _Block, block: _Block) -> str:
    """What goes between two written blocks: a line break, or a blank line."""
    if previous.kind == "empty":
        return "\n\n"
    if previous.kind == "item" and block.kind == "item":
        # A different list at the same depth is a list of its own.
        return "\n\n" if _new_list(previous, block) else "\n"
    depth = min(_quote_depth(previous), _quote_depth(block))
    if depth:
        # Two parts of one quote stay in it.
        return "\n" + ("> " * depth).rstrip() + "\n"
    # Lines typed one under the other become paragraphs of their own: a
    # blank line is the one break every Nostr reader shows the same way
    # (NIP-23 asks for no hard line breaks inside a paragraph).
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
    markers and link addresses, and no Markdown markup. Empty lines stay
    exactly as typed; paragraphs that came from Markdown or HTML keep the
    blank line between them."""
    lines: List[str] = []
    previous: Optional[_Block] = None
    for block in document_blocks(doc, image_target):
        if block.kind == "empty":
            lines.append("")
        else:
            if (previous is not None and previous.kind != "empty" and previous.spaced
                    and block.spaced
                    and not (previous.kind == "item" and block.kind == "item"
                             and not _new_list(previous, block))):
                lines.append("")
            lines.append(_note_lines(block))
        previous = block
    return "\n".join(lines).strip("\n")


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


# Markdown the editor's document cannot hold, so reading it in would lose
# something: footnotes, raw HTML, an image without alt text (Qt drops it),
# a link title, a link around an image, a heading inside a quote,
# a paragraph continued inside a list item, and lines that wrap inside a
# paragraph (a draft saved as plain text by an earlier version).
_LOSSY = [
    re.compile(r"\[\^[^\]]+\]"),                         # footnote
    re.compile(r"<(?:[A-Za-z][\w-]*[\s/>]|/[A-Za-z]|!--)"),  # raw HTML
    re.compile(r"!\[\]\("),                                # image without alt text
    re.compile(r"\]\((?!<)[^)\s]+\s+[\"'(]"),              # link title
    re.compile(r"\[!\["),                                  # image inside a link
    re.compile(r"^\s*>\s*#", re.MULTILINE),               # heading in a quote
]
_LIST_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")
_BLOCK_START = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|>|#|\||```|~~~|    |\t)")


def _soft_wrapped(markdown: str) -> bool:
    """Lines following each other inside one paragraph, or a list item
    continued after a blank line: Qt joins the first and moves the second
    out of the list."""
    lines = markdown.split("\n")
    fence = False
    for index in range(1, len(lines)):
        line, before = lines[index], lines[index - 1]
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        if fence:
            continue
        if (line.strip() and before.strip() and not _BLOCK_START.match(line)
                and not _BLOCK_START.match(before) and not before.endswith("  ")
                and not set(line.strip()) <= set("=-")):
            return True
        if (line.startswith(("  ", "\t")) and line.strip() and not before.strip()
                and index >= 2 and _LIST_LINE.match(lines[index - 2])):
            return True
    return False


def _fingerprint(doc) -> list:
    """What a document shows: each block's kind and text, and each piece's
    formatting, so two renderings can be compared."""
    prints = []
    for block in iter_blocks(doc):
        fmt = block.blockFormat()
        text_list = block.textList()
        prints.append((
            fmt.headingLevel(),
            (text_list.format().style() in _ORDERED_STYLES) if text_list else None,
            text_list.format().indent() if text_list else 0,
            fmt.property(QTextFormat.Property.BlockQuoteLevel),
            bool(fmt.property(QTextFormat.Property.BlockCodeFence)
                 or fmt.hasProperty(QTextFormat.Property.BlockCodeLanguage)),
            tuple((text, _is_bold(f), f.fontItalic(), f.fontStrikeOut(), _is_code(f),
                   f.anchorHref() if f.isAnchor() else "",
                   f.toImageFormat().name() if f.isImageFormat() else "")
                  for text, f in iter_block_runs(block)),
        ))
    return prints


# A footnote definition as typed ("[^1]: Ibid.") at a line's start, maybe
# inside a quote, and the fence lines around code, where it is code.
_FOOTNOTE_DEFINITION = re.compile(r"^((?: {0,3}>)* {0,3})\[(?=\^[^\]\s]+\]:)")
_FENCE_LINE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def literal_footnotes(markdown: str) -> str:
    """``markdown`` with every footnote definition outside code written
    ``\\[^1]:``, so a reader takes it for the text it is.

    Qt's reader takes a definition whose text is one word or a web address
    for a link reference definition: the line disappears and the mark
    that refers to it becomes a link. Footnotes are kept as typed text in
    the editor (D5), so they must read back as text."""
    out = []
    fence = ""
    for line in markdown.split("\n"):
        found = _FENCE_LINE.match(line)
        if fence:
            if (found and found.group(1)[0] == fence[0] and len(found.group(1)) >= len(fence)
                    and not found.group(2).strip()):
                fence = ""
        elif found:
            fence = found.group(1)
        else:
            line = _FOOTNOTE_DEFINITION.sub(r"\1\\[", line)
        out.append(line)
    return "\n".join(out)


def read_markdown(doc: QTextDocument, markdown: str, features=READ_FEATURES) -> None:
    """Read Markdown into ``doc`` the way the editor holds it: the one way
    the app reads Markdown it wrote or accepted (an opened file or draft,
    the publish preview, a paste), so what was typed comes back as typed.
    Replaces what ``doc`` held."""
    doc.setMarkdown(literal_footnotes(markdown), features)
    normalize_after_markdown_load(doc)


def holds_faithfully(markdown: str) -> bool:
    """Whether the editor can hold this Markdown and write it back without
    losing anything. When it cannot, open it as its Markdown text."""
    if any(pattern.search(markdown) for pattern in _LOSSY) or _soft_wrapped(markdown):
        return False
    first = QTextDocument()
    first.setMarkdown(markdown, READ_FEATURES)
    second = QTextDocument()
    second.setMarkdown(document_to_markdown(first), READ_FEATURES)
    return _fingerprint(first) == _fingerprint(second)


def image_markdown(alt: str, url: str) -> str:
    """``![alt](url)``, with a bracket in the alt text escaped and an
    address that holds a space or a parenthesis written as ``<url>``."""
    return f"![{(alt or 'image').replace(']', chr(92) + ']')}]({_destination(url)})"


def document_to(doc, flavor: str, image_target: Optional[ImageTarget] = None) -> str:
    """:func:`document_to_markdown` or :func:`document_to_note_text`."""
    if flavor == NOTE:
        return document_to_note_text(doc, image_target)
    return document_to_markdown(doc, image_target)


__all__: Tuple[str, ...] = (
    "MARKDOWN", "NOTE", "document_blocks", "document_to", "document_to_markdown",
    "document_to_note_text", "has_local_only_formatting", "holds_faithfully",
    "image_markdown", "literal_footnotes", "read_markdown", "READ_FEATURES",
)
