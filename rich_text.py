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

from PySide6.QtGui import QTextCursor, QTextFormat

from doc_walk import iter_blocks


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
