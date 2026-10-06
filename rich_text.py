# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The editor's rich text: every structure it can make, as plain functions.

The document is a QTextDocument, and Markdown is how it leaves the editor
(markdown_writer.py). So every structure made here is one that writer
already writes, and one Qt's own Markdown reader reads back the same way:
a heading is a block with a heading level, a list is a QTextList, a quote
is a block with a quote level. What the editor shows, Markdown can say,
and what was written comes back unchanged when the file is opened again.

The functions take a QTextCursor (or the document) and change nothing but
the text they are given, each change as one step on the undo stack. They
hold no state and know no widget, so the editor, the menus, the toolbar,
the slash menu and the Markdown shortcuts all call the same code, and a
test can call it without a window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List, Tuple

from PySide6.QtGui import (
    QFont, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument, QTextDocumentFragment,
    QTextFormat, QTextListFormat,
)

from doc_walk import iter_blocks

# Inline styles, by name. Underline has no Markdown; it stays in local
# files only (markdown_writer.has_local_only_formatting).
BOLD = "bold"
ITALIC = "italic"
UNDERLINE = "underline"
STRIKE = "strike"
CODE = "code"
INLINE = (BOLD, ITALIC, UNDERLINE, STRIKE, CODE)

# The font inline code is shown in, as Qt's Markdown reader writes it.
CODE_FAMILIES = ["monospace"]


def normalize_after_markdown_load(doc) -> None:
    """Tidy what Qt's Markdown reader leaves behind.

    After a checklist the reader puts a checklist mark on every block that
    follows it (paragraphs, quotes, code), outside any list. The mark is
    invisible there, but it is in the block's format, so starting a list
    on such a paragraph would make a checked item out of nothing. Every
    block outside a list loses it; list items keep theirs.

    Headings get the room above and below them that the editor gives
    the ones typed here.

    Call after every ``setMarkdown`` whose result the person edits.
    """
    marker = QTextFormat.Property.BlockMarker
    stray = [block for block in iter_blocks(doc)
             if block.textList() is None and block.blockFormat().hasProperty(marker)]
    headings = [block for block in iter_blocks(doc) if block.blockFormat().headingLevel()]
    if not stray and not headings:
        return
    cursor = QTextCursor(doc)
    cursor.beginEditBlock()
    for block in stray:
        fmt = block.blockFormat()
        fmt.clearProperty(marker)
        QTextCursor(block).setBlockFormat(fmt)
    # Headings get the room the editor gives the ones typed here.
    for block in headings:
        fmt = block.blockFormat()
        QTextCursor(block).setBlockFormat(heading_block_format(fmt, fmt.headingLevel()))
    cursor.endEditBlock()


# --------------------------------------------------------------------------- #
# Inline styles                                                                #
# --------------------------------------------------------------------------- #

def has_style(fmt: QTextCharFormat, style: str) -> bool:
    """Whether a piece of text carries ``style``."""
    if style == BOLD:
        return fmt.fontWeight() >= QFont.Weight.DemiBold
    if style == ITALIC:
        return fmt.fontItalic()
    if style == UNDERLINE:
        return fmt.fontUnderline()
    if style == STRIKE:
        return fmt.fontStrikeOut()
    if style == CODE:
        return fmt.fontFixedPitch()
    raise ValueError(f"unknown style {style!r}")


def style_format(style: str, on: bool) -> QTextCharFormat:
    """The change that turns ``style`` on or off, to merge into text."""
    fmt = QTextCharFormat()
    if style == BOLD:
        fmt.setFontWeight(QFont.Weight.Bold if on else QFont.Weight.Normal)
    elif style == ITALIC:
        fmt.setFontItalic(on)
    elif style == UNDERLINE:
        fmt.setFontUnderline(on)
    elif style == STRIKE:
        fmt.setFontStrikeOut(on)
    elif style == CODE:
        fmt.setFontFixedPitch(on)
        # Off, the families are cleared by clear_style below: merging can
        # set a property but never take one away.
        if on:
            fmt.setFontFamilies(CODE_FAMILIES)
    else:
        raise ValueError(f"unknown style {style!r}")
    return fmt


def _iter_text_runs(cursor: QTextCursor) -> Iterator[Tuple[int, int, QTextCharFormat]]:
    """``(start, end, format)`` of each piece of text in the selection
    (images left out), cut to the selection, one after the other."""
    doc = cursor.document()
    start, end = cursor.selectionStart(), cursor.selectionEnd()
    block = doc.findBlock(start)
    while block.isValid() and block.position() < end:
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid():
                first = fragment.position()
                last = first + fragment.length()
                fmt = fragment.charFormat()
                if last > start and first < end and not fmt.isImageFormat():
                    yield max(first, start), min(last, end), fmt
            it += 1
        block = block.next()


def _text_runs(cursor: QTextCursor) -> List[Tuple[int, int, QTextCharFormat]]:
    """Every piece of text in the selection (see _iter_text_runs)."""
    return list(_iter_text_runs(cursor))


def selection_has(cursor: QTextCursor, style: str) -> bool:
    """Whether all of the selected text carries ``style``: a selection that
    is only partly bold counts as not bold, so the command makes it all
    bold (the way Pages and Google Docs do it)."""
    found = False
    for _start, _end, fmt in _iter_text_runs(cursor):
        if not has_style(fmt, style):
            return False
        found = True
    return found


# What the menus and the toolbar show about a selection is read from at
# most this many pieces of text, or paragraphs: far more than any
# selection someone reads at once, and a bound on the time one update
# takes while a long selection is being extended. Past it, a style that
# could not be checked all the way counts as not applied throughout (its
# button shows off); the commands themselves always act on all of it.
STATE_BUDGET = 1500


def selection_state(cursor: QTextCursor, styles=INLINE, *,
                    budget: int = STATE_BUDGET) -> dict:
    """``{style: bool}``: for each style, whether all of the selected text
    carries it, read in one pass that stops as soon as every answer is
    known, or after ``budget`` pieces of text."""
    still = set(styles)
    seen = 0
    for _start, _end, fmt in _iter_text_runs(cursor):
        seen += 1
        if seen > budget:
            still.clear()
            break
        for style in list(still):
            if not has_style(fmt, style):
                still.discard(style)
        if not still:
            break
    return {style: seen > 0 and style in still for style in styles}


def toggle_style(cursor: QTextCursor, style: str) -> bool:
    """Turn ``style`` on over the whole selection, or off when all of it
    has it already. Returns the new state. One step on the undo stack."""
    on = not selection_has(cursor, style)
    cursor.beginEditBlock()
    cursor.mergeCharFormat(style_format(style, on))
    if not on and style == CODE:
        _clear_properties(cursor, (QTextFormat.Property.FontFamilies,
                                   QTextFormat.Property.FontFamily))
    cursor.endEditBlock()
    return on


def _clear_properties(cursor: QTextCursor, properties, *, keep=None) -> None:
    """Take ``properties`` off every piece of the selection; ``keep(fmt,
    property)`` may spare one."""
    doc = cursor.document()
    for start, end, fmt in _text_runs(cursor):
        changed = QTextCharFormat(fmt)
        for prop in properties:
            if keep is None or not keep(fmt, prop):
                changed.clearProperty(prop)
        if changed != fmt:
            piece = QTextCursor(doc)
            piece.setPosition(start)
            piece.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            piece.setCharFormat(changed)


# What Clear Formatting takes away: every inline style and color. A link
# stays a link and keeps its own look; a heading keeps its weight.
_FORMATTING = (
    QTextFormat.Property.FontWeight, QTextFormat.Property.FontItalic,
    QTextFormat.Property.FontUnderline, QTextFormat.Property.TextUnderlineStyle,
    QTextFormat.Property.FontStrikeOut, QTextFormat.Property.FontFixedPitch,
    QTextFormat.Property.FontFamilies, QTextFormat.Property.FontFamily,
    QTextFormat.Property.ForegroundBrush, QTextFormat.Property.BackgroundBrush,
)
_LINK_LOOK = (QTextFormat.Property.ForegroundBrush, QTextFormat.Property.FontUnderline,
              QTextFormat.Property.TextUnderlineStyle)


def clear_formatting(cursor: QTextCursor) -> None:
    """Clear Formatting over the selection, as one step on the undo stack."""
    doc = cursor.document()
    headings = set()
    block = doc.findBlock(cursor.selectionStart())
    while block.isValid() and block.position() <= cursor.selectionEnd():
        if block.blockFormat().headingLevel():
            headings.add(block.position())
        block = block.next()

    def keep(fmt, prop):
        if fmt.isAnchor() and prop in _LINK_LOOK:
            return True
        return False

    cursor.beginEditBlock()
    _clear_properties(cursor, _FORMATTING, keep=keep)
    # A heading is bold by its own format: that is not emphasis to clear.
    for position in headings:
        heading = QTextCursor(doc.findBlock(position))
        heading.movePosition(QTextCursor.MoveOperation.EndOfBlock,
                             QTextCursor.MoveMode.KeepAnchor)
        within = QTextCursor(doc)
        within.setPosition(max(heading.selectionStart(), cursor.selectionStart()))
        within.setPosition(min(heading.selectionEnd(), cursor.selectionEnd()),
                           QTextCursor.MoveMode.KeepAnchor)
        within.mergeCharFormat(style_format(BOLD, True))
    cursor.endEditBlock()


def typing_format_without(fmt: QTextCharFormat) -> QTextCharFormat:
    """The format to type on with after Clear Formatting with nothing
    selected: the one at the caret, with every style and color taken off
    (a link stays a link)."""
    clean = QTextCharFormat(fmt)
    for prop in _FORMATTING:
        if not (fmt.isAnchor() and prop in _LINK_LOOK):
            clean.clearProperty(prop)
    return clean


# --------------------------------------------------------------------------- #
# Paragraph styles: Body and Heading 1 to 3                                    #
# --------------------------------------------------------------------------- #

BODY = 0
# How much larger a heading's text is, the way Qt's Markdown reader sizes
# it (QTextCharFormat.FontSizeAdjustment): a document typed here and one
# opened from a .md file look the same.
HEADING_SIZE = {1: 3, 2: 2, 3: 1, 4: 0, 5: -1, 6: -1}


def _iter_blocks_of(cursor: QTextCursor):
    """The blocks the cursor's selection touches (the caret's block
    without one), in order, one after the other. None for a null cursor
    (an editor's cursor is one while its whole document is replaced)."""
    if cursor.isNull():
        return
    doc = cursor.document()
    block = doc.findBlock(cursor.selectionStart())
    last = doc.findBlock(cursor.selectionEnd())
    while block.isValid():
        yield block
        if block == last:
            break
        block = block.next()


def _blocks_of(cursor: QTextCursor):
    """The blocks the cursor's selection touches (see _iter_blocks_of)."""
    return list(_iter_blocks_of(cursor))


def heading_level(cursor: QTextCursor) -> int:
    """The paragraph style under the cursor: 0 for Body, 1 to 6 for a
    heading, -1 when the selection spans different ones."""
    level = None
    for block in _iter_blocks_of(cursor):
        this = block.blockFormat().headingLevel()
        if level is not None and this != level:
            return -1
        level = this
    return -1 if level is None else level


def heading_char_format(level: int) -> QTextCharFormat:
    """The text format of a heading of ``level`` (merged into its text)."""
    fmt = QTextCharFormat()
    fmt.setProperty(QTextFormat.Property.FontSizeAdjustment, HEADING_SIZE.get(level, 0))
    fmt.setFontWeight(QFont.Weight.Bold)
    return fmt


# The weight words had of their own before their paragraph became a
# heading, which sets every word in its bold: given back when the
# paragraph is Body again, so bold words stay bold (0: none of their own).
OWN_WEIGHT = QTextFormat.Property.UserProperty + 0x3D1


def body_char_format(fmt: QTextCharFormat, *, own_weight: bool = True) -> QTextCharFormat:
    """``fmt`` as Body text: no heading size, no heading weight, and the
    weight the words had of their own (unless ``own_weight`` is False:
    what is typed next starts in the plain weight)."""
    body = QTextCharFormat(fmt)
    body.clearProperty(QTextFormat.Property.FontSizeAdjustment)
    own = body.property(OWN_WEIGHT) if own_weight else None
    if isinstance(own, int) and not isinstance(own, bool) and own > 0:
        body.setFontWeight(own)
    else:
        body.clearProperty(QTextFormat.Property.FontWeight)
    body.clearProperty(OWN_WEIGHT)
    return body


# Room above and below a heading. Margins are how the editor shows it;
# they are not Markdown, so nothing of them is written.
HEADING_MARGINS = (12, 4)


def heading_block_format(fmt, level: int):
    fmt.setHeadingLevel(level)
    top, bottom = HEADING_MARGINS if level else (0, 0)
    fmt.setTopMargin(top)
    fmt.setBottomMargin(bottom)
    return fmt


def set_heading(cursor: QTextCursor, level: int) -> None:
    """Make the paragraphs under the cursor Body (0) or a heading of
    ``level``, as one step on the undo stack. Choosing the style a
    paragraph already has turns it back into Body, the way the toolbar's
    heading buttons work in standup and Google Docs.

    A heading is a paragraph of its own: a list item or a quoted line
    made a heading leaves its list or quote (Markdown readers drop the
    quote of a quoted heading, and a heading inside a list is no heading
    to them)."""
    if level and heading_level(cursor) == level:
        level = BODY
    doc = cursor.document()
    edit = QTextCursor(doc)
    edit.beginEditBlock()
    for block in _blocks_of(cursor):
        if level:
            if block.textList() is not None:
                leave_list(block)
            if quote_depth(block):
                set_quote_depth(block, 0)
        fmt = heading_block_format(block.blockFormat(), level)
        whole = QTextCursor(block)
        whole.setBlockFormat(fmt)
        whole.movePosition(QTextCursor.MoveOperation.EndOfBlock,
                           QTextCursor.MoveMode.KeepAnchor)
        for start, end, char in (_text_runs(whole) if whole.hasSelection() else []):
            piece = QTextCursor(doc)
            piece.setPosition(start)
            piece.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            piece.setCharFormat(restyled(char, level))
        # What is typed into the paragraph next (an empty one, or at its end).
        whole.setBlockCharFormat(restyled(block.charFormat(), level))
    edit.endEditBlock()


def restyled(fmt: QTextCharFormat, level: int) -> QTextCharFormat:
    """``fmt`` in the paragraph style ``level`` (0 for Body). Words that
    become heading words keep note of their own weight (OWN_WEIGHT)."""
    if not level:
        return body_char_format(fmt)
    heading = QTextCharFormat(fmt)
    if not heading.hasProperty(OWN_WEIGHT):
        own = fmt.fontWeight() if fmt.hasProperty(QTextFormat.Property.FontWeight) else 0
        heading.setProperty(OWN_WEIGHT, int(own))
    heading.clearProperty(QTextFormat.Property.FontSizeAdjustment)
    heading.clearProperty(QTextFormat.Property.FontWeight)
    heading.merge(heading_char_format(level))
    return heading


# --------------------------------------------------------------------------- #
# Lists                                                                        #
# --------------------------------------------------------------------------- #

BULLET = "bullet"
NUMBER = "number"
# Deepest nesting a command makes. Markdown has no limit; readers have.
MAX_LIST_DEPTH = 6

_BULLET_STYLES = (QTextListFormat.Style.ListDisc, QTextListFormat.Style.ListCircle,
                  QTextListFormat.Style.ListSquare)
_ORDERED = {QTextListFormat.Style.ListDecimal, QTextListFormat.Style.ListLowerAlpha,
            QTextListFormat.Style.ListUpperAlpha, QTextListFormat.Style.ListLowerRoman,
            QTextListFormat.Style.ListUpperRoman}


def list_kind_of(text_list) -> str:
    """BULLET or NUMBER for a QTextList (Markdown knows only these two)."""
    return NUMBER if text_list.format().style() in _ORDERED else BULLET


def list_format(kind: str, depth: int) -> QTextListFormat:
    """A list of ``kind`` nested ``depth`` deep. Bullets change shape with
    depth (disc, circle, square), as in Pages and every browser."""
    fmt = QTextListFormat()
    if kind == NUMBER:
        fmt.setStyle(QTextListFormat.Style.ListDecimal)
    else:
        fmt.setStyle(_BULLET_STYLES[(depth - 1) % len(_BULLET_STYLES)])
    fmt.setIndent(depth)
    return fmt


def list_kind(cursor: QTextCursor) -> str:
    """The list under the cursor: BULLET, NUMBER, "" for none, "mixed"
    when the selection holds more than one kind (or list and not)."""
    kinds = {list_kind_of(b.textList()) if b.textList() is not None else ""
             for b in _blocks_of(cursor)}
    if not kinds:
        return ""
    return kinds.pop() if len(kinds) == 1 else "mixed"


def leave_list(block) -> None:
    """Take a paragraph out of its list; it stays where it is, as Body text
    at the left margin (Qt would keep the list's indent as its own)."""
    text_list = block.textList()
    if text_list is not None:
        text_list.remove(block)
    fmt = block.blockFormat()
    fmt.setIndent(0)
    fmt.clearProperty(QTextFormat.Property.BlockMarker)
    QTextCursor(block).setBlockFormat(fmt)


def _list_to_join(block, depth: int, kind: str):
    """The list a paragraph moved to ``depth`` belongs to: the one an item
    before it already has at that depth (only deeper items between)."""
    previous = block.previous()
    while previous.isValid() and previous.textList() is not None \
            and previous.textList().format().indent() > depth:
        previous = previous.previous()
    if (previous.isValid() and previous.textList() is not None
            and previous.textList().format().indent() == depth
            and list_kind_of(previous.textList()) == kind):
        return previous.textList()
    return None


def _move_to_depth(block, depth: int, kind: str) -> None:
    target = _list_to_join(block, depth, kind)
    if target is not None:
        if block.textList() is not target:
            target.add(block)
    else:
        QTextCursor(block).createList(list_format(kind, depth))
    _no_own_indent(block)


def _no_own_indent(block) -> None:
    fmt = block.blockFormat()
    if fmt.indent():
        fmt.setIndent(0)
        QTextCursor(block).setBlockFormat(fmt)


def _item_run(block):
    """The first and last block of the run of list items ``block`` is in."""
    first = last = block
    while first.previous().isValid() and first.previous().textList() is not None:
        first = first.previous()
    while last.next().isValid() and last.next().textList() is not None:
        last = last.next()
    return first, last


def _tidy_lists(doc, around) -> None:
    """Items next to each other at the same depth and of the same kind are
    one list, as Markdown reads them: a list a command made right after
    another, or the children left behind when an item moved, join it.
    Only the runs of items ``around`` touches are looked at."""
    runs = []
    for block in around:
        if block.isValid() and block.textList() is not None:
            run = _item_run(block)
            if run not in runs:
                runs.append(run)
    for first, last in runs:
        open_lists = {}
        block = first
        while block.isValid():
            text_list = block.textList()
            depth = text_list.format().indent()
            for deeper in [d for d in open_lists if d > depth]:
                del open_lists[deeper]
            current = open_lists.get(depth)
            if (current is not None and current is not text_list
                    and list_kind_of(current) == list_kind_of(text_list)):
                current.add(block)
                text_list = current
            open_lists[depth] = text_list
            if block == last:
                break
            block = block.next()


def toggle_list(cursor: QTextCursor, kind: str) -> None:
    """Bulleted List or Numbered List over the paragraphs under the
    cursor, as one step on the undo stack:

    - all of them in lists of this kind: they leave the list;
    - all of them in lists, some of another kind: those lists change
      kind in place, nesting kept;
    - otherwise they become one list of this kind (a heading becomes
      Body first: a list item is not a heading)."""
    blocks = _blocks_of(cursor)
    if not blocks:
        return
    doc = cursor.document()
    edit = QTextCursor(doc)
    edit.beginEditBlock()
    current = list_kind(cursor)
    all_items = all(b.textList() is not None for b in blocks)
    if current == kind:
        for block in blocks:
            leave_list(block)
    elif all_items:
        changed = []
        for block in blocks:
            text_list = block.textList()
            if any(text_list is seen for seen in changed):
                continue
            changed.append(text_list)
            text_list.setFormat(list_format(kind, text_list.format().indent()))
    else:
        for block in blocks:
            if block.blockFormat().headingLevel():
                set_heading(QTextCursor(block), BODY)
            if block.textList() is not None:
                leave_list(block)
        new_list = QTextCursor(blocks[0]).createList(list_format(kind, 1))
        for block in blocks:
            if block.textList() is not new_list:
                new_list.add(block)
            _no_own_indent(block)
    _tidy_lists(doc, [blocks[0].previous(), *blocks, blocks[-1].next()])
    edit.endEditBlock()


def change_indent(cursor: QTextCursor, delta: int) -> bool:
    """Increase (+1) or decrease (-1) the nesting of the list items under
    the cursor, as one step on the undo stack. An item at the top level
    that is decreased leaves the list. False when there is no list item
    under the cursor (nothing done)."""
    items = [b for b in _blocks_of(cursor) if b.textList() is not None]
    if not items:
        return False
    doc = cursor.document()
    edit = QTextCursor(doc)
    edit.beginEditBlock()
    for block in items:
        text_list = block.textList()
        kind = list_kind_of(text_list)
        depth = text_list.format().indent() + delta
        if depth < 1:
            leave_list(block)
        else:
            _move_to_depth(block, min(depth, MAX_LIST_DEPTH), kind)
    _tidy_lists(doc, [items[0].previous(), *items, items[-1].next()])
    edit.endEditBlock()
    return True


# --------------------------------------------------------------------------- #
# Quotes and dividers                                                          #
# --------------------------------------------------------------------------- #

# How far one level of quote moves a paragraph in, as Qt's Markdown reader
# places it: a quote typed here and one read from a .md file look alike.
QUOTE_INDENT = 40


def quote_depth(block) -> int:
    level = block.blockFormat().property(QTextFormat.Property.BlockQuoteLevel)
    return level if isinstance(level, int) and level > 0 else 0


def set_quote_depth(block, depth: int) -> None:
    fmt = block.blockFormat()
    if depth > 0:
        fmt.setProperty(QTextFormat.Property.BlockQuoteLevel, depth)
        fmt.setLeftMargin(QUOTE_INDENT * depth)
    else:
        fmt.clearProperty(QTextFormat.Property.BlockQuoteLevel)
        fmt.setLeftMargin(0)
    QTextCursor(block).setBlockFormat(fmt)


def in_quote(cursor: QTextCursor) -> bool:
    """Whether every paragraph under the cursor is quoted."""
    found = False
    for block in _iter_blocks_of(cursor):
        if not quote_depth(block):
            return False
        found = True
    return found


@dataclass(frozen=True)
class ParagraphState:
    """What the menus and the toolbar show about the paragraphs under the
    cursor: their style (-1 for mixed), their list (BULLET, NUMBER, "" or
    "mixed") and whether all of them are quoted."""

    heading: int = -1
    list_kind: str = ""
    quoted: bool = False


def paragraph_state(cursor: QTextCursor, *, budget: int = STATE_BUDGET) -> ParagraphState:
    """The paragraphs' state, read in one pass that stops once every
    answer is known, or after ``budget`` paragraphs (STATE_BUDGET: past it,
    the state reads as mixed)."""
    level = None
    kind = None
    quoted = True
    seen = 0
    for block in _iter_blocks_of(cursor):
        seen += 1
        if seen > budget:
            return ParagraphState(-1, "mixed", False)
        this_level = block.blockFormat().headingLevel()
        this_kind = list_kind_of(block.textList()) if block.textList() is not None else ""
        level = this_level if level is None or level == this_level else -1
        kind = this_kind if kind is None or kind == this_kind else "mixed"
        quoted = quoted and bool(quote_depth(block))
        if level == -1 and kind == "mixed" and not quoted:
            break
    if not seen:
        return ParagraphState()
    return ParagraphState(level, kind, quoted)


def toggle_quote(cursor: QTextCursor) -> None:
    """Quote the paragraphs under the cursor, or, when all of them are
    quoted already, take the quote away. One step on the undo stack. A
    heading quoted becomes Body first: Markdown readers drop the quote of
    a quoted heading."""
    blocks = _blocks_of(cursor)
    if not blocks:
        return
    unquote = in_quote(cursor)
    edit = QTextCursor(cursor.document())
    edit.beginEditBlock()
    for block in blocks:
        if unquote:
            set_quote_depth(block, 0)
        elif not quote_depth(block) and not is_divider(block):
            if block.blockFormat().headingLevel():
                set_heading(QTextCursor(block), BODY)
            set_quote_depth(block, 1)
    edit.endEditBlock()


def is_divider(block) -> bool:
    return block.blockFormat().hasProperty(
        QTextFormat.Property.BlockTrailingHorizontalRulerWidth)


def _divider_format() -> QTextBlockFormat:
    fmt = QTextBlockFormat()
    # What Qt's Markdown reader gives "---": a rule across the page.
    fmt.setProperty(QTextFormat.Property.BlockTrailingHorizontalRulerWidth, 1)
    return fmt


def insert_divider(cursor: QTextCursor) -> None:
    """A divider (a horizontal rule, ``---``) after the paragraph at the
    cursor, or in its place when it is empty, and an empty paragraph after
    it where the cursor goes. One step on the undo stack."""
    cursor.beginEditBlock()
    block = cursor.block()
    if block.text() or is_divider(block):
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        cursor.insertBlock(_divider_format(), QTextCharFormat())
    else:
        cursor.setBlockFormat(_divider_format())
        cursor.setBlockCharFormat(QTextCharFormat())
    after = cursor.block().next()
    if after.isValid() and not after.text() and not is_divider(after) \
            and after.textList() is None:
        cursor.setPosition(after.position())
    else:
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
    cursor.endEditBlock()


def remove_divider(block) -> None:
    """The divider becomes an empty paragraph again."""
    QTextCursor(block).setBlockFormat(QTextBlockFormat())


# --------------------------------------------------------------------------- #
# Links                                                                        #
# --------------------------------------------------------------------------- #

_LINK_PROPERTIES = (QTextFormat.Property.IsAnchor, QTextFormat.Property.AnchorHref,
                    QTextFormat.Property.AnchorName)


def link_range(cursor: QTextCursor):
    """``(start, end, href)`` of the link at the caret (touching it from
    either side) or holding the whole selection; None when there is none.
    Pieces of one link in different styles count as one link."""
    if cursor.isNull():
        return None
    doc = cursor.document()
    block = doc.findBlock(cursor.selectionStart())
    pieces = []
    it = block.begin()
    while not it.atEnd():
        fragment = it.fragment()
        if fragment.isValid():
            fmt = fragment.charFormat()
            pieces.append((fragment.position(), fragment.position() + fragment.length(),
                           fmt.anchorHref() if fmt.isAnchor() else ""))
        it += 1
    start, end = cursor.selectionStart(), cursor.selectionEnd()
    for index, (first, last, href) in enumerate(pieces):
        if not href:
            continue
        touches = (first <= start <= last and first <= end <= last) if start != end else \
            first <= start <= last
        if not touches:
            continue
        low, high = index, index
        while low > 0 and pieces[low - 1][2] == href and pieces[low - 1][1] == pieces[low][0]:
            low -= 1
        while high + 1 < len(pieces) and pieces[high + 1][2] == href \
                and pieces[high + 1][0] == pieces[high][1]:
            high += 1
        return pieces[low][0], pieces[high][1], href
    return None


def set_link(cursor: QTextCursor, text: str, href: str) -> None:
    """Make the selection a link to ``href``, as one step on the undo
    stack. With ``text`` that differs from the selection (or with nothing
    selected), the words are replaced by ``text``, in the style of the
    first selected character; otherwise their bold or italic stays."""
    cursor.beginEditBlock()
    anchor = QTextCharFormat()
    anchor.setAnchor(True)
    anchor.setAnchorHref(href)
    selected = cursor.selectedText()
    if text and text != selected:
        style = QTextCharFormat(cursor.charFormat()) if not cursor.hasSelection() else \
            _first_char_format(cursor)
        for prop in _LINK_PROPERTIES:
            style.clearProperty(prop)
        style.merge(anchor)
        cursor.insertText(text, style)
        cursor.setPosition(cursor.position() - len(text), QTextCursor.MoveMode.KeepAnchor)
    elif cursor.hasSelection():
        cursor.mergeCharFormat(anchor)
    cursor.endEditBlock()


def _first_char_format(cursor: QTextCursor) -> QTextCharFormat:
    probe = QTextCursor(cursor.document())
    probe.setPosition(cursor.selectionStart() + 1)
    return QTextCharFormat(probe.charFormat())


def remove_link(cursor: QTextCursor) -> bool:
    """Take the link at the cursor away; the words stay, plain. One step
    on the undo stack. False when there is no link there."""
    found = link_range(cursor)
    if found is None:
        return False
    start, end, _href = found
    whole = QTextCursor(cursor.document())
    whole.setPosition(start)
    whole.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    whole.beginEditBlock()
    # A link read from Markdown also carries the link color: that goes
    # with it, or the words would keep a color of their own.
    _clear_properties(whole, (*_LINK_PROPERTIES, QTextFormat.Property.ForegroundBrush,
                              QTextFormat.Property.FontUnderline,
                              QTextFormat.Property.TextUnderlineStyle),
                      keep=lambda fmt, prop: not fmt.isAnchor())
    whole.endEditBlock()
    return True


def without_link(fmt: QTextCharFormat) -> QTextCharFormat:
    """``fmt`` with no link: what is typed right after a link is not part
    of it."""
    plain = QTextCharFormat(fmt)
    for prop in (*_LINK_PROPERTIES, QTextFormat.Property.ForegroundBrush,
                 QTextFormat.Property.FontUnderline, QTextFormat.Property.TextUnderlineStyle):
        plain.clearProperty(prop)
    return plain


# --------------------------------------------------------------------------- #
# Code blocks and pasted documents                                             #
# --------------------------------------------------------------------------- #

def is_code_block(block) -> bool:
    """Whether the paragraph is a line of a code block (fenced or indented,
    as Qt's Markdown reader marks them)."""
    fmt = block.blockFormat()
    return bool(fmt.property(QTextFormat.Property.BlockCodeFence)) or fmt.hasProperty(
        QTextFormat.Property.BlockCodeLanguage)


def _has_own_style(block) -> bool:
    """Whether a paragraph is more than Body text: a heading, a list item,
    a quote, a code line or a divider."""
    return bool(block.blockFormat().headingLevel() or block.textList() is not None
                or quote_depth(block) or is_code_block(block) or is_divider(block))


def _stands_alone(block) -> bool:
    """Whether a paragraph cannot share its line with other words: a line
    of code, a divider."""
    return is_code_block(block) or is_divider(block)


def _restyle(doc, start: int, end: int, level: int) -> None:
    """The text from ``start`` to ``end`` in the paragraph style ``level``."""
    if end <= start:
        return
    span = QTextCursor(doc)
    span.setPosition(start)
    span.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    for first, last, fmt in _text_runs(span):
        piece = QTextCursor(doc)
        piece.setPosition(first)
        piece.setPosition(last, QTextCursor.MoveMode.KeepAnchor)
        piece.setCharFormat(restyled(fmt, level))


def _take_style(target, first) -> None:
    """The empty paragraph ``target`` became ``first``, the first pasted
    paragraph: it takes its style, list included."""
    fmt = QTextBlockFormat(first.blockFormat())
    fmt.clearProperty(QTextFormat.Property.ObjectIndex)    # the list is set below
    QTextCursor(target).setBlockFormat(fmt)
    pasted_list = first.textList()
    if pasted_list is None:
        return
    following = first.next()
    joined = None
    if (following.isValid() and following.textList() is not None
            and following.textList().objectIndex() == pasted_list.objectIndex()):
        # The next pasted item came from the same list: join its copy.
        joined = target.next().textList()
    if joined is not None:
        joined.add(target)
    else:
        QTextCursor(target).createList(pasted_list.format())


def _one_line(source):
    """``source`` as one line of text, its paragraphs joined by spaces:
    what a table cell can hold."""
    line = QTextDocument()
    out = QTextCursor(line)
    for block in iter_blocks(source):
        if not block.text().strip():
            continue
        if not out.atStart():
            out.insertText(" ", QTextCharFormat())
        level = block.blockFormat().headingLevel()
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            it += 1
            if fragment.isValid():
                fmt = fragment.charFormat()
                out.insertText(fragment.text(), restyled(fmt, 0) if level else fmt)
    return line


def insert_document(cursor: QTextCursor, source) -> None:
    """Insert the document ``source`` at the cursor in place of the
    selection, as one step on the undo stack, the way typing it would:

    - its first paragraph goes on from the words before the caret and its
      last runs into the words after it, each in the style of the
      paragraph it joins (a heading pasted into a sentence becomes words
      of that sentence);
    - into an empty paragraph, the first pasted paragraph comes with its
      own style, so a heading stays a heading and an item an item;
    - code and dividers never run into words: pasted inside a paragraph,
      they split it and stand on lines of their own;
    - pasted list items join a list of the same kind they land next to;
    - into a table cell it comes as one line, which is what a cell holds;
    - pasted on a divider, it goes below it.

    The cursor ends after what was pasted."""
    doc = cursor.document()
    cursor.beginEditBlock()
    if cursor.hasSelection():
        cursor.removeSelectedText()
    if is_divider(cursor.block()):
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
    if cursor.currentTable() is not None:
        source = _one_line(source)
    first = source.begin()
    if cursor.block().text():
        # A code line or a divider at either end of what is pasted gets a
        # line of its own: split the paragraph there.
        if _stands_alone(source.lastBlock()) and not cursor.atBlockEnd():
            cursor.insertBlock()
            cursor.movePosition(QTextCursor.MoveOperation.PreviousBlock)
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        if _stands_alone(first) and not cursor.atBlockStart():
            cursor.insertBlock()
    target = cursor.block()
    empty = not target.text()
    level = target.blockFormat().headingLevel()
    start = cursor.position()
    cursor.insertFragment(QTextDocumentFragment(source))
    end = cursor.position()
    landed = doc.findBlock(start)
    last = doc.findBlock(end)
    if empty and _has_own_style(first):
        _take_style(landed, first)
    elif first.blockFormat().headingLevel() != level:
        # The first pasted words joined a paragraph of another style.
        _restyle(doc, start, min(end, landed.position() + landed.length() - 1), level)
    if last != landed:
        # The words after the caret went on in the last pasted paragraph.
        tail_level = last.blockFormat().headingLevel()
        if tail_level != level:
            _restyle(doc, end, last.position() + last.length() - 1, tail_level)
    around = [landed.previous()]
    block = landed
    while block.isValid():
        around.append(block)
        if block == last:
            break
        block = block.next()
    around.append(last.next())
    _tidy_lists(doc, around)
    cursor.setPosition(end)
    cursor.endEditBlock()
