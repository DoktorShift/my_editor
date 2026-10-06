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

from typing import List, Tuple

from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor, QTextFormat

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

    Call after every ``setMarkdown`` whose result the person edits.
    """
    marker = QTextFormat.Property.BlockMarker
    stray = [block for block in iter_blocks(doc)
             if block.textList() is None and block.blockFormat().hasProperty(marker)]
    if not stray:
        return
    cursor = QTextCursor(doc)
    cursor.beginEditBlock()
    for block in stray:
        fmt = block.blockFormat()
        fmt.clearProperty(marker)
        QTextCursor(block).setBlockFormat(fmt)
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


def _text_runs(cursor: QTextCursor) -> List[Tuple[int, int, QTextCharFormat]]:
    """``(start, end, format)`` of every piece of text in the selection
    (images left out), cut to the selection."""
    doc = cursor.document()
    start, end = cursor.selectionStart(), cursor.selectionEnd()
    runs = []
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
                    runs.append((max(first, start), min(last, end), fmt))
            it += 1
        block = block.next()
    return runs


def selection_has(cursor: QTextCursor, style: str) -> bool:
    """Whether all of the selected text carries ``style``: a selection that
    is only partly bold counts as not bold, so the command makes it all
    bold (the way Pages and Google Docs do it)."""
    runs = _text_runs(cursor)
    return bool(runs) and all(has_style(fmt, style) for _s, _e, fmt in runs)


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


def _blocks_of(cursor: QTextCursor):
    """The blocks the cursor's selection touches (the caret's block
    without one), in order. None for a null cursor (an editor's cursor is
    one while its whole document is being replaced)."""
    if cursor.isNull():
        return []
    doc = cursor.document()
    block = doc.findBlock(cursor.selectionStart())
    last = doc.findBlock(cursor.selectionEnd())
    blocks = []
    while block.isValid():
        blocks.append(block)
        if block == last:
            break
        block = block.next()
    return blocks


def heading_level(cursor: QTextCursor) -> int:
    """The paragraph style under the cursor: 0 for Body, 1 to 6 for a
    heading, -1 when the selection spans different ones."""
    levels = {block.blockFormat().headingLevel() for block in _blocks_of(cursor)}
    return levels.pop() if len(levels) == 1 else -1


def heading_char_format(level: int) -> QTextCharFormat:
    """The text format of a heading of ``level`` (merged into its text)."""
    fmt = QTextCharFormat()
    fmt.setProperty(QTextFormat.Property.FontSizeAdjustment, HEADING_SIZE.get(level, 0))
    fmt.setFontWeight(QFont.Weight.Bold)
    return fmt


def body_char_format(fmt: QTextCharFormat) -> QTextCharFormat:
    """``fmt`` as Body text: no heading size, no heading weight."""
    body = QTextCharFormat(fmt)
    body.clearProperty(QTextFormat.Property.FontSizeAdjustment)
    body.clearProperty(QTextFormat.Property.FontWeight)
    return body


def set_heading(cursor: QTextCursor, level: int) -> None:
    """Make the paragraphs under the cursor Body (0) or a heading of
    ``level``, as one step on the undo stack. Choosing the style a
    paragraph already has turns it back into Body, the way the toolbar's
    heading buttons work in standup and Google Docs."""
    if level and heading_level(cursor) == level:
        level = BODY
    doc = cursor.document()
    edit = QTextCursor(doc)
    edit.beginEditBlock()
    for block in _blocks_of(cursor):
        fmt = block.blockFormat()
        fmt.setHeadingLevel(level)
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
    """``fmt`` in the paragraph style ``level`` (0 for Body)."""
    body = body_char_format(fmt)
    if level:
        body.merge(heading_char_format(level))
    return body
