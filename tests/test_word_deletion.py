# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins deleting by word and by line with the keyboard, as every text field
of the platform does.

What must hold:

  On a Mac, Option-Delete deletes the word before the caret and
  Fn-Option-Delete the word after it; Command-Delete deletes to the start
  of the line and Fn-Command-Delete to its end. On Windows and Linux,
  Ctrl+Backspace and Ctrl+Delete delete the word before and after.

  They do so in every kind of document and inside lists, checklists,
  quotes, code blocks and tables, as a step of the run of deleting like
  Backspace's. At the edge of a paragraph, or with words selected, they do
  what Backspace or Delete does there (out of a list, a divider taken
  away). A mention goes as a whole, never half of it.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import editor as editor_module  # noqa: E402
import rich_text  # noqa: E402
from editor import HtmlEditor  # noqa: E402

MAC = sys.platform == "darwin"
WORD = Qt.KeyboardModifier.AltModifier if MAC else Qt.KeyboardModifier.ControlModifier
LINE = Qt.KeyboardModifier.ControlModifier          # Command, on a Mac
BACK, FORWARD = Qt.Key.Key_Backspace, Qt.Key.Key_Delete


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(editor_module, "time", Clock())


def editor(text="", structure=True):
    ed = HtmlEditor()
    ed.show()
    ed.setFocus()
    ed.set_structure_check(lambda: structure)
    if text:
        QTest.keyClicks(ed, text)
    return ed


def caret(ed, position):
    cursor = ed.textCursor()
    cursor.setPosition(position)
    ed.setTextCursor(cursor)


def line(ed):
    return ed.textCursor().block().text()


@pytest.mark.parametrize("structure", [True, False])
def test_a_word_back_and_forth_in_the_run_of_deleting(structure):
    ed = editor("hello big world", structure)
    QTest.keyClick(ed, BACK, WORD)
    assert ed.toPlainText() == "hello big "
    QTest.keyClick(ed, BACK, WORD)
    assert ed.toPlainText() == "hello "
    assert ed.undo_name() == "Typing"
    ed.undo()                                   # the two words: one run
    assert ed.toPlainText() == "hello big world"
    caret(ed, 0)
    QTest.keyClick(ed, FORWARD, WORD)
    assert ed.toPlainText() == (" big world" if MAC else "big world")


def test_command_delete_deletes_to_the_start_or_end_of_the_line():
    if not MAC:
        pytest.skip("a Mac's keys")
    ed = editor("hello big world")
    caret(ed, 6)
    QTest.keyClick(ed, BACK, LINE)
    assert ed.toPlainText() == "big world" and ed.undo_name() == "Typing"
    QTest.keyClick(ed, FORWARD, LINE)
    assert ed.toPlainText() == ""


def styled(make):
    ed = editor("first second")
    make(ed)
    return ed


@pytest.mark.parametrize("make", [
    lambda ed: ed.toggle_list(rich_text.BULLET),
    lambda ed: ed.toggle_checklist(),
    lambda ed: ed.toggle_quote(),
    lambda ed: ed.make_code_block("python"),
], ids=["list", "checklist", "quote", "code"])
def test_inside_structure_a_word_goes_and_the_structure_stays(make):
    ed = styled(make)
    block = ed.textCursor().block()
    before, in_list = block.blockFormat(), block.textList() is not None
    QTest.keyClick(ed, BACK, WORD)
    block = ed.textCursor().block()
    assert line(ed) == "first "
    assert block.blockFormat() == before and (block.textList() is not None) == in_list


def test_in_a_table_cell():
    ed = editor()
    ed.insert_table(2, 2)
    QTest.keyClicks(ed, "one two")
    QTest.keyClick(ed, BACK, WORD)
    assert line(ed) == "one " and ed.textCursor().currentTable() is not None


def test_at_the_start_of_a_list_item_it_is_backspace():
    ed = editor("item")
    ed.toggle_list(rich_text.BULLET)
    cursor = ed.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
    ed.setTextCursor(cursor)
    QTest.keyClick(ed, BACK, WORD)
    assert ed.textCursor().block().textList() is None and line(ed) == "item"


def test_below_a_divider_it_takes_the_divider_away_as_backspace_does():
    ed = editor("a")
    ed.insert_divider()
    QTest.keyClicks(ed, "below")
    caret(ed, ed.textCursor().block().position())
    QTest.keyClick(ed, BACK, WORD)
    assert not any(rich_text.is_divider(ed.document().findBlockByNumber(i))
                   for i in range(ed.document().blockCount()))
    assert "below" in ed.toPlainText()


def test_with_words_selected_only_they_go():
    ed = editor("hello big world")
    cursor = ed.textCursor()
    cursor.setPosition(6)
    cursor.setPosition(9, QTextCursor.MoveMode.KeepAnchor)
    ed.setTextCursor(cursor)
    QTest.keyClick(ed, BACK, WORD)
    assert ed.toPlainText() == "hello  world"


def test_a_mention_goes_whole():
    ed = editor("Hi ")
    ed.set_nostr_check(lambda: True)
    at = ed.textCursor().position()
    ed.insert_mention(at, "nostr:npub1" + "q" * 58, "Ann")
    caret(ed, at + 1)
    start, end = rich_text.mention_at(ed.textCursor())
    caret(ed, end)
    QTest.keyClick(ed, BACK, WORD)
    assert ed.toPlainText().rstrip() == "Hi"
    ed.undo()
    caret(ed, start)
    QTest.keyClick(ed, FORWARD, WORD)
    assert "Ann" not in ed.toPlainText() and "@" not in ed.toPlainText()


def test_in_a_typed_bullet_it_deletes_words_not_the_indent():
    ed = editor(structure=False)
    ed.textCursor().insertText("    • item two")
    QTest.keyClick(ed, BACK, WORD)
    assert ed.toPlainText() == "    • item "
