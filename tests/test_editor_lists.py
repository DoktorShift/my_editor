# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins real lists in the editor: the commands and the keys.

What must hold:

  Bulleted and Numbered List make, convert and take away real lists (a
  list made right after another of its kind continues it), as one step
  on the undo stack.

  Tab nests an item, Shift+Tab un-nests it, and from the top level takes
  it out of the list. Return on an empty item goes one level up, and out
  of the list from the top. Backspace at an item's start does the same:
  two items are never run together.

  Every case writes the Markdown readers expect, and reads back the
  same. Where a document cannot hold lists (a .txt file), Tab keeps
  typing bullets as text.
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
from tests.rich_text_helpers import assert_round_trip, block_named, press, type_text  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def editor(markdown: str = "") -> HtmlEditor:
    ed = HtmlEditor()
    if markdown:
        ed.document().setMarkdown(markdown, READ_FEATURES)
        rich_text.normalize_after_markdown_load(ed.document())
    return ed


def caret_in(ed: HtmlEditor, text: str, at_end: bool = False) -> None:
    block = block_named(ed.document(), text)
    cursor = QTextCursor(block)
    if at_end:
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    ed.setTextCursor(cursor)


def markdown(ed: HtmlEditor) -> str:
    return assert_round_trip(ed.document())


# -- the commands ---------------------------------------------------------------------

def test_paragraphs_become_a_bulleted_list_and_back():
    ed = editor("one\n\ntwo\n\nthree\n")
    ed.selectAll()
    ed.toggle_list(rich_text.BULLET)
    assert markdown(ed) == "- one\n- two\n- three\n"
    ed.toggle_list(rich_text.BULLET)
    assert markdown(ed) == "one\n\ntwo\n\nthree\n"


def test_a_bulleted_list_becomes_numbered_with_its_nesting():
    ed = editor("- one\n    - inner\n- two\n")
    ed.selectAll()
    ed.toggle_list(rich_text.NUMBER)
    assert markdown(ed) == "1. one\n    1. inner\n2. two\n"


def test_a_list_made_right_after_one_continues_it():
    ed = editor("1. one\n2. two\n\nthree\n")
    caret_in(ed, "three")
    ed.toggle_list(rich_text.NUMBER)
    assert markdown(ed) == "1. one\n2. two\n3. three\n"


def test_a_heading_made_a_list_item_is_body_text():
    ed = editor("## Title\n")
    ed.toggle_list(rich_text.BULLET)
    assert markdown(ed) == "- Title\n"


def test_making_a_list_is_one_undo_step():
    ed = editor("one\n\ntwo\n")
    ed.selectAll()
    ed.toggle_list(rich_text.BULLET)
    ed.document().undo()
    assert document_to_markdown(ed.document()) == "one\n\ntwo\n"


def test_increase_and_decrease_indent_nest_an_item():
    ed = editor("- one\n- two\n")
    caret_in(ed, "two")
    ed.change_indent(+1)
    assert markdown(ed) == "- one\n    - two\n"
    ed.change_indent(-1)
    assert markdown(ed) == "- one\n- two\n"
    ed.change_indent(-1)
    assert markdown(ed) == "- one\n\ntwo\n"


# -- the keys -------------------------------------------------------------------------

def test_tab_nests_and_shift_tab_unnests():
    ed = editor("- one\n- two\n- three\n")
    caret_in(ed, "two")
    press(ed, Qt.Key.Key_Tab)
    assert markdown(ed) == "- one\n    - two\n- three\n"
    press(ed, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
    assert markdown(ed) == "- one\n- two\n- three\n"


def test_tab_on_a_selection_nests_every_item():
    ed = editor("- one\n- two\n- three\n")
    cursor = QTextCursor(block_named(ed.document(), "two"))
    cursor.setPosition(block_named(ed.document(), "three").position() + 2,
                       QTextCursor.MoveMode.KeepAnchor)
    ed.setTextCursor(cursor)
    press(ed, Qt.Key.Key_Tab)
    assert markdown(ed) == "- one\n    - two\n    - three\n"


def test_children_left_behind_join_the_item_that_moved_in():
    ed = editor("- one\n- two\n    - child\n")
    caret_in(ed, "two")
    press(ed, Qt.Key.Key_Tab)
    assert markdown(ed) == "- one\n    - two\n    - child\n"


def test_return_continues_the_list_and_twice_leaves_it():
    ed = editor("- one\n")
    caret_in(ed, "one", at_end=True)
    type_text(ed, "\ntwo\n\nafter")
    assert markdown(ed) == "- one\n- two\n\nafter\n"


def test_return_on_an_empty_nested_item_goes_one_level_up():
    ed = editor("- one\n    - two\n")
    caret_in(ed, "two", at_end=True)
    type_text(ed, "\n\nthree")
    assert markdown(ed) == "- one\n    - two\n- three\n"


def test_backspace_at_an_item_start_never_runs_two_items_together():
    ed = editor("- one\n- two\n")
    caret_in(ed, "two")
    press(ed, Qt.Key.Key_Backspace)
    assert ed.document().blockCount() == 2
    assert markdown(ed) == "- one\n\ntwo\n"


def test_tab_at_the_start_of_a_paragraph_starts_a_list():
    ed = editor()
    type_text(ed, "first")
    ed.moveCursor(QTextCursor.MoveOperation.StartOfBlock)
    press(ed, Qt.Key.Key_Tab)
    ed.moveCursor(QTextCursor.MoveOperation.End)
    type_text(ed, "\nsecond")
    assert markdown(ed) == "- first\n- second\n"


def test_a_document_without_lists_keeps_typed_bullets():
    ed = editor()
    ed.set_structure_check(lambda: False)
    type_text(ed, "first")
    ed.moveCursor(QTextCursor.MoveOperation.StartOfBlock)
    press(ed, Qt.Key.Key_Tab)
    assert ed.toPlainText() == "    • first"
    assert ed.document().begin().textList() is None
