# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Markdown typed as it is written, turned into what it means (opt-in).

With Edit > Substitutions > Markdown Shortcuts on (it is off until the
person turns it on), typing the Markdown for a structure makes it, the
way Bear, Notion and standup do:

- at the start of a Body paragraph, followed by a space: "#", "##" or
  "###" for Heading 1 to 3, "-", "*" or "+" for a bulleted list, "1."
  or "1)" for a numbered list (starting at the number typed, unless the
  item continues a list right above), ">" for a quote;
- a paragraph that holds only "---", "***" or "___", followed by
  Return: a divider;
- a closing mark: "**bold**", "*italic*" or "_italic_" (the underscore
  only at the start of a word, so snake_case stays as typed),
  "~~struck~~", "`code`".

The typed characters are their own steps on the undo stack and the
conversion another, so Undo right after it brings back exactly what was
typed. Nothing is converted in a code block, in inline code, across a
link or a picture, or in a plain-text or code file (the editor asks
before it calls in).

Each function takes a QTextCursor at the caret, changes nothing unless a
rule applies, and makes each conversion one edit block, moving the
cursor to where the caret belongs afterwards. rich_text.py makes the
structures, so a heading made here is the heading the menu makes.
"""

from __future__ import annotations

import re
from typing import Optional

from PySide6.QtGui import QTextCursor

import rich_text

# The start of a paragraph, up to the space just typed.
_BLOCK_START = re.compile(r"(#{1,3}|[-*+]|(\d{1,9})[.)]|>) ")
# A checklist box typed at the start of a paragraph or of a list item.
_TASK_START = re.compile(r"\[([ xX])\] ")
# A paragraph that is a divider, before Return.
_DIVIDER = re.compile(r"-{3,}|\*{3,}|_{3,}")
# Closing marks, checked in this order against the paragraph's text up to
# the caret. Bold before italic: "**bold*" is not yet italic, and
# "**bold**" is bold. A mark after a backslash is a literal one.
_INLINE = (
    (rich_text.BOLD, re.compile(r"(?<![*\\])\*\*(?=[^\s*])(.+?)(?<=[^\s*])\*\*$")),
    (rich_text.STRIKE, re.compile(r"(?<![~\\])~~(?=\S)(.+?)(?<=\S)~~$")),
    (rich_text.ITALIC, re.compile(r"(?<![*\w\\])\*(?=[^\s*])([^*]+?)(?<=[^\s*])\*$")),
    (rich_text.ITALIC, re.compile(r"(?<![\w\\])_(?=[^\s_])([^_]+?)(?<=[^\s_])_$")),
    (rich_text.CODE, re.compile(r"(?<![`\\])`([^`]+)`$")),
)
# The characters that can close an inline mark.
CLOSING_MARKS = "*_~`"


def _is_body(block) -> bool:
    """Whether a paragraph is plain Body text: no heading, list, quote,
    code, divider or table cell."""
    return (not block.blockFormat().headingLevel() and block.textList() is None
            and not rich_text.quote_depth(block) and not rich_text.is_code_block(block)
            and not rich_text.is_divider(block) and QTextCursor(block).currentTable() is None)


def after_space(cursor: QTextCursor) -> str:
    """A space was just typed at the caret. When the paragraph's start asks
    for a structure, make it and return its name ("heading 1", "bullet",
    "number", "quote"); else return ""."""
    if cursor.hasSelection():
        return ""
    block = cursor.block()
    typed = block.text()[:cursor.positionInBlock()]
    task = _TASK_START.fullmatch(typed)
    if task is not None and (_is_body(block) or block.textList() is not None):
        # In an item that has a box already, the box typed sets its tick.
        return _make_task(cursor, block, typed, ticked=task.group(1) != " ")
    found = _BLOCK_START.fullmatch(typed)
    if found is None or not _is_body(block):
        return ""
    marker = found.group(1)
    doc = cursor.document()
    edit = QTextCursor(doc)
    edit.beginEditBlock()
    marks = QTextCursor(block)
    marks.setPosition(block.position() + len(typed), QTextCursor.MoveMode.KeepAnchor)
    marks.removeSelectedText()
    at = QTextCursor(block)
    if marker.startswith("#"):
        rich_text.set_heading(at, len(marker))
        made = f"heading {len(marker)}"
    elif marker == ">":
        rich_text.toggle_quote(at)
        made = "quote"
    elif found.group(2):
        rich_text.toggle_list(at, rich_text.NUMBER)
        _number_from(block, int(found.group(2)))
        made = "number"
    else:
        rich_text.toggle_list(at, rich_text.BULLET)
        made = "bullet"
    edit.endEditBlock()
    cursor.setPosition(block.position())
    return made


def _make_task(cursor: QTextCursor, block, typed: str, *, ticked: bool) -> str:
    """"[ ] " or "[x] " typed at the start of a paragraph or a list item:
    a checklist item, ticked for "[x] "."""
    edit = QTextCursor(cursor.document())
    edit.beginEditBlock()
    marks = QTextCursor(block)
    marks.setPosition(block.position() + len(typed), QTextCursor.MoveMode.KeepAnchor)
    marks.removeSelectedText()
    if block.textList() is None:
        rich_text.toggle_checklist(QTextCursor(block))
    rich_text.set_tick(block, rich_text.TICKED if ticked else rich_text.UNTICKED)
    edit.endEditBlock()
    cursor.setPosition(block.position())
    return "checklist"


def _number_from(block, number: int) -> None:
    """A numbered list made by typing "3. " starts at 3, unless the item
    continues a list right above it (then it takes the next number)."""
    text_list = block.textList()
    if text_list is None or number == 1 or text_list.itemNumber(block) != 0:
        return
    fmt = text_list.format()
    fmt.setStart(number)
    text_list.setFormat(fmt)


def on_return(cursor: QTextCursor) -> bool:
    """Return was pressed at the caret. A Body paragraph holding only
    "---", "***" or "___" becomes a divider, and the caret goes to the
    paragraph after it. True when it did (Return is then handled)."""
    if cursor.hasSelection() or not cursor.atBlockEnd():
        return False
    block = cursor.block()
    if not _DIVIDER.fullmatch(block.text()) or not _is_body(block):
        return False
    doc = cursor.document()
    edit = QTextCursor(doc)
    edit.beginEditBlock()
    marks = QTextCursor(block)
    marks.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
    marks.removeSelectedText()
    at = QTextCursor(block)
    rich_text.insert_divider(at)
    edit.endEditBlock()
    cursor.setPosition(at.position())
    return True


def _plain_span(doc, start: int, end: int) -> bool:
    """Whether the text from ``start`` to ``end`` is ordinary words: no
    link, no inline code and no picture in it."""
    probe = QTextCursor(doc)
    probe.setPosition(start)
    probe.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    return not rich_text.holds_link_or_code(probe)


def after_mark(cursor: QTextCursor) -> Optional[str]:
    """A closing mark (CLOSING_MARKS) was just typed at the caret. When it
    closes an inline style, style the words between the marks, take the
    marks away and return the style (rich_text.BOLD, ...); else None."""
    if cursor.hasSelection():
        return None
    block = cursor.block()
    if rich_text.is_code_block(block):
        return None
    typed = block.text()[:cursor.positionInBlock()]
    doc = cursor.document()
    for style, pattern in _INLINE:
        found = pattern.search(typed)
        if found is None:
            continue
        start = block.position() + found.start()
        end = block.position() + len(typed)
        if not _plain_span(doc, start, end):
            return None
        opening = found.start(1) - found.start()
        closing = found.end() - found.end(1)
        words = len(found.group(1))
        edit = QTextCursor(doc)
        edit.beginEditBlock()
        marks = QTextCursor(doc)
        marks.setPosition(end - closing)
        marks.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        marks.removeSelectedText()
        marks.setPosition(start)
        marks.setPosition(start + opening, QTextCursor.MoveMode.KeepAnchor)
        marks.removeSelectedText()
        span = QTextCursor(doc)
        span.setPosition(start)
        span.setPosition(start + words, QTextCursor.MoveMode.KeepAnchor)
        span.mergeCharFormat(rich_text.style_format(style, True))
        edit.endEditBlock()
        cursor.setPosition(start + words)
        return style
    return None
