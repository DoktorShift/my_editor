# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins quotes and dividers in the editor.

What must hold:

  Quote quotes the paragraphs under the cursor, or takes the quote away
  when all of them are quoted, as one step on the undo stack; a quote
  typed here looks like one read from Markdown and is written as "> ".
  Return on an empty quoted line and Backspace at a quoted paragraph's
  start take one level of quote away.

  A divider is "---". It never holds text: typing on it goes below it,
  Backspace just below it or on it takes it away.

  The bar beside a quote is painted only: the document is not changed.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import rich_text  # noqa: E402
from editor import HtmlEditor  # noqa: E402
from markdown_writer import READ_FEATURES, document_to_markdown  # noqa: E402
from tests.rich_text_helpers import (  # noqa: E402
    assert_round_trip, block_named, from_markdown, press, type_text,
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def editor(markdown: str = "") -> HtmlEditor:
    ed = HtmlEditor()
    if markdown:
        ed.document().setMarkdown(markdown, READ_FEATURES)
    return ed


def caret_in(ed, text, at_end=False):
    cursor = QTextCursor(block_named(ed.document(), text))
    if at_end:
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    ed.setTextCursor(cursor)


# -- quotes ---------------------------------------------------------------------------

def test_quote_and_unquote_paragraphs():
    ed = editor("before\n\none\n\ntwo\n")
    cursor = QTextCursor(block_named(ed.document(), "one"))
    cursor.setPosition(block_named(ed.document(), "two").position() + 1,
                       QTextCursor.MoveMode.KeepAnchor)
    ed.setTextCursor(cursor)
    ed.toggle_quote()
    assert assert_round_trip(ed.document()) == "before\n\n> one\n>\n> two\n"
    ed.toggle_quote()
    assert document_to_markdown(ed.document()) == "before\n\none\n\ntwo\n"


def test_a_typed_quote_looks_like_one_read_from_markdown():
    typed = from_markdown("words\n")
    rich_text.toggle_quote(QTextCursor(typed))
    read = from_markdown("> words\n")
    assert typed.begin().blockFormat().leftMargin() == read.begin().blockFormat().leftMargin()
    assert rich_text.quote_depth(typed.begin()) == rich_text.quote_depth(read.begin()) == 1


def test_quoting_is_one_undo_step():
    ed = editor("one\n")
    ed.toggle_quote()
    ed.document().undo()
    assert document_to_markdown(ed.document()) == "one\n"


def test_return_continues_a_quote_and_twice_leaves_it():
    ed = editor("> said\n")
    caret_in(ed, "said", at_end=True)
    type_text(ed, "\nmore\n\nafter")
    assert assert_round_trip(ed.document()) == "> said\n>\n> more\n\nafter\n"


def test_backspace_at_a_quoted_paragraph_start_unquotes_it():
    ed = editor("> said\n")
    caret_in(ed, "said")
    press(ed, Qt.Key.Key_Backspace)
    assert document_to_markdown(ed.document()) == "said\n"


def test_painting_the_quote_bar_changes_nothing():
    ed = editor("> quoted\n")
    ed.resize(400, 200)
    ed.document().setModified(False)
    before = document_to_markdown(ed.document())
    ed.grab()
    assert document_to_markdown(ed.document()) == before
    assert not ed.document().isModified()


# -- dividers -------------------------------------------------------------------------

def test_a_divider_goes_after_the_paragraph_and_typing_below_it():
    ed = editor("above\n")
    caret_in(ed, "above", at_end=True)
    ed.insert_divider()
    type_text(ed, "below")
    assert assert_round_trip(ed.document()) == "above\n\n---\n\nbelow\n"


def test_a_divider_takes_the_place_of_an_empty_paragraph():
    ed = editor()
    ed.insert_divider()
    type_text(ed, "text")
    assert document_to_markdown(ed.document()) == "---\n\ntext\n"


def test_typing_on_a_divider_goes_below_it():
    ed = editor("above\n\n---\n")
    ed.setTextCursor(QTextCursor(ed.document().lastBlock()))
    assert rich_text.is_divider(ed.textCursor().block())
    type_text(ed, "x")
    assert document_to_markdown(ed.document()) == "above\n\n---\n\nx\n"


def test_backspace_below_a_divider_takes_it_away():
    ed = editor("above\n\n---\n\nbelow\n")
    caret_in(ed, "below")
    press(ed, Qt.Key.Key_Backspace)
    assert document_to_markdown(ed.document()) == "above\n\nbelow\n"


def test_inserting_a_divider_is_one_undo_step():
    ed = editor("above\n")
    caret_in(ed, "above", at_end=True)
    ed.insert_divider()
    ed.document().undo()
    assert document_to_markdown(ed.document()) == "above\n"
