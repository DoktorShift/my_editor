# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Helpers for the tests of the editor's rich text.

``assert_round_trip(doc)`` is the test every structure has to pass: the
document written as Markdown, read back the way a .md file or a draft is
opened, and written again, gives the same Markdown.
"""

from PySide6.QtGui import QTextDocument

import rich_text
from markdown_writer import READ_FEATURES, document_to_markdown


def from_markdown(text: str) -> QTextDocument:
    """A document opened from Markdown, the way the editor opens it."""
    doc = QTextDocument()
    doc.setMarkdown(text, READ_FEATURES)
    rich_text.normalize_after_markdown_load(doc)
    return doc


def assert_round_trip(doc: QTextDocument) -> str:
    """The Markdown of ``doc``, after checking it survives a round trip."""
    first = document_to_markdown(doc)
    second = document_to_markdown(from_markdown(first))
    assert second == first, f"round trip changed\n{first!r}\ninto\n{second!r}"
    return first


def block_named(doc: QTextDocument, text: str):
    """The first block whose text is ``text``."""
    block = doc.begin()
    while block.isValid():
        if block.text() == text:
            return block
        block = block.next()
    raise AssertionError(f"no block {text!r}")


def select(doc: QTextDocument, text: str, occurrence: int = 0):
    """A cursor selecting the ``occurrence``-th ``text`` in ``doc``."""
    from PySide6.QtGui import QTextCursor
    plain = doc.toPlainText()
    start = -1
    for _i in range(occurrence + 1):
        start = plain.index(text, start + 1)
    cursor = QTextCursor(doc)
    cursor.setPosition(start)
    cursor.setPosition(start + len(text), QTextCursor.MoveMode.KeepAnchor)
    return cursor
