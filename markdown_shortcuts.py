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
  Return: a divider; one that holds a code fence ("```" or "~~~", with
  a language: "```python"), followed by Return: a code block;
- "[ ] " or "[x] " at the start of a paragraph or a list item: a
  checklist item, ticked for "[x]";
- a closing mark: "**bold**", "*italic*", "~~struck~~", "`code`", and
  "_italic_" once the character after it is typed and is no letter or
  digit (so "snake_case" and "_private_name" stay as typed, as Markdown
  reads them). The words between the marks never hold the mark itself
  (an earlier "**a**" kept as typed is not reached back to), and a mark
  right after a backslash is a literal one.

The typed characters are their own steps on the undo stack and the
conversion another, so Undo right after it brings back exactly what was
typed. After a conversion, typing goes on in the style the opening mark
was typed in. Nothing is converted in a code block, in inline code or
in code still being typed (after an opening backtick), across a link
or a picture, on marks that are themselves code or a link, or in a
plain-text or code file (the editor asks before it calls in).

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
from doc_walk import doc_length, doc_offset, text_index

# The start of a paragraph, up to the space just typed.
_BLOCK_START = re.compile(r"(#{1,3}|[-*+]|(\d{1,9})[.)]|>) ")
# A checklist box typed at the start of a paragraph or of a list item.
_TASK_START = re.compile(r"\[([ xX])\] ")
# A paragraph that is a divider, before Return.
_DIVIDER = re.compile(r"-{3,}|\*{3,}|_{3,}")
# A code fence, with the language it names, before Return.
_FENCE = re.compile(r"(?:`{3,}|~{3,})\s*([\w+#.-]*)\s*")
# Closing marks, checked in this order against the paragraph's text up to
# the caret. Bold before italic: "**bold*" is not yet italic, and
# "**bold**" is bold. The words never hold the mark itself, and neither
# opening nor closing mark comes right after a backslash.
_INLINE = (
    (rich_text.BOLD, re.compile(
        r"(?<![*\\])\*\*(?=[^\s*])((?:(?!\*\*).)+?)(?<=[^\s*\\])\*\*$")),
    (rich_text.STRIKE, re.compile(r"(?<![~\\])~~(?=\S)((?:(?!~~).)+?)(?<=[^\s\\])~~$")),
    (rich_text.ITALIC, re.compile(r"(?<![*\w\\])\*(?=[^\s*])([^*]+?)(?<=[^\s*\\])\*$")),
    (rich_text.CODE, re.compile(r"(?<![`\\])`([^`]+)`$")),
)
# "_italic_", checked once the character after the closing "_" is typed.
_UNDERSCORE = re.compile(r"(?<![\w\\])_(?=[^\s_])([^_]+?)(?<=[^\s_\\])_$")
# The characters that close an inline mark as they are typed.
CLOSING_MARKS = "*~`"


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
    text = block.text()
    typed = text[:text_index(text, cursor.positionInBlock())]
    if not _plain_span(cursor.document(), block.position(),
                       block.position() + doc_length(typed)):
        return ""                     # "`#`": a code word, no heading mark
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
    marks.setPosition(block.position() + doc_length(typed), QTextCursor.MoveMode.KeepAnchor)
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
    marks.setPosition(block.position() + doc_length(typed), QTextCursor.MoveMode.KeepAnchor)
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
    if not _plain_span(cursor.document(), block.position(), block.position() + block.length() - 1):
        return False                  # "`---`": code words, no divider
    fence = _FENCE.fullmatch(block.text())
    if fence is not None and _is_body(block):
        return _make_code_block(cursor, block, fence.group(1))
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


def _make_code_block(cursor: QTextCursor, block, language: str) -> bool:
    """"```python" and Return: the paragraph becomes the first, empty line
    of a code block in that language."""
    edit = QTextCursor(cursor.document())
    edit.beginEditBlock()
    marks = QTextCursor(block)
    marks.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
    marks.removeSelectedText()
    rich_text.make_code_block(QTextCursor(block), language)
    edit.endEditBlock()
    cursor.setPosition(block.position())
    return True


def _plain_span(doc, start: int, end: int) -> bool:
    """Whether the text from ``start`` to ``end`` is ordinary words: no
    link, no inline code and no picture in it."""
    probe = QTextCursor(doc)
    probe.setPosition(start)
    probe.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    return not rich_text.holds_link_or_code(probe)


def _in_open_code(text: str) -> bool:
    """Whether ``text`` ends inside code still being typed: after an odd
    number of backticks that no backslash escapes."""
    count = 0
    escaped = False
    for ch in text:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "`":
            count += 1
    return count % 2 == 1


def after_mark(cursor: QTextCursor) -> Optional[str]:
    """A character was just typed at the caret. When it closes an inline
    style (a closing mark of CLOSING_MARKS, or the character after a
    closing "_" that is no letter or digit), style the words between the
    marks, take the marks away and return the style (rich_text.BOLD,
    ...); else None. The cursor ends after the words (and the character
    typed after an "_"), carrying the format the opening mark was typed
    in, for what is typed next."""
    if cursor.hasSelection():
        return None
    block = cursor.block()
    if rich_text.is_code_block(block):
        return None
    text = block.text()
    typed = text[:text_index(text, cursor.positionInBlock())]
    if not typed:
        return None
    if typed[-1] in CLOSING_MARKS:
        rules, after = _INLINE, 0
    elif len(typed) > 1 and typed[-2] == "_" and not typed[-1].isalnum() and typed[-1] != "_":
        # (What is typed after the "_" may take two places: an emoji.)
        rules, after = ((rich_text.ITALIC, _UNDERSCORE),), doc_length(typed[-1])
        typed = typed[:-1]
    else:
        return None
    doc = cursor.document()
    for style, pattern in rules:
        found = pattern.search(typed)
        if found is None:
            continue
        if style != rich_text.CODE and _in_open_code(typed[:found.start()]):
            continue                  # "`**x**": code being typed, not bold
        # Places in the text, as places in the document.
        start = block.position() + doc_offset(typed, found.start())
        end = block.position() + doc_length(typed)
        if not _plain_span(doc, start, end):
            return None
        opening = found.start(1) - found.start()
        closing = found.end() - found.end(1)
        words = doc_length(found.group(1))
        probe = QTextCursor(doc)
        probe.setPosition(start + 1)
        before = probe.charFormat()   # what the opening mark was typed in
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
        cursor.setPosition(start + words + after)
        cursor.setCharFormat(before)
        return style
    return None
