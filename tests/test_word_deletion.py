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


# What a Mac's own text view (NSTextView) leaves of a text after deleting a
# word or to the line's edge from a caret: (text, caret, key, text after,
# caret after). Carets count UTF-16 units, as Qt's and AppKit's do.
# Option-Delete is "wb", Fn-Option-Delete "wf", Command-Delete "lb" and
# Fn-Command-Delete "lf".
NATIVE = [
    ("hello world", 11, 'wb', "hello ", 6),
    ("hello world", 0, 'wf', " world", 0),
    ("hello world", 5, 'wf', "hello", 5),
    ("hello world", 6, 'wb', "world", 0),
    ("foo, bar", 3, 'wf', "foo", 3),
    ("foo, bar", 5, 'wb', "bar", 0),
    ("foo, bar", 4, 'wb', " bar", 0),
    ("foo, bar.", 9, 'wb', "foo, ", 5),
    ("say 😀😀 now", 8, 'wb', "say  now", 4),
    ("say 😀😀 now", 4, 'wf', "say 😀 now", 4),
    ("say 👍🏽 ok", 8, 'wb', "say  ok", 4),
    ("say 😀😀 now", 4, 'wf', "say 😀 now", 4),
    ("café au", 5, 'wb', " au", 0),
    ("café au", 0, 'wf', " au", 0),
    ("日本語のテキスト", 8, 'wb', "日本語の", 4),
    ("日本語のテキスト", 0, 'wf', "のテキスト", 0),
    ("中文 测试", 5, 'wb', "中文 ", 3),
    ("it's done", 9, 'wb', "it's ", 5),
    ("it's done", 4, 'wb', " done", 0),
    ("a.b.c", 5, 'wb', "", 0),
    ("x = y + z", 9, 'wb', "x = y + ", 8),
    ("https://example.com/path", 24, 'wb', "https://example.com/", 20),
    ("e-mail me", 6, 'wb', "e- me", 2),
    ("e-mail me", 0, 'wf', "-mail me", 0),
    ("one   two", 6, 'wb', "two", 0),
    ("one   two", 3, 'wf', "one", 3),
    ("end.  ", 6, 'wb', "", 0),
    ("hello world", 8, 'lb', "rld", 0),
    ("hello world", 3, 'lf', "hel", 3),
    ("@alice hi", 6, 'wb', "@ hi", 1),
    ("word nbsp", 9, 'wb', "word ", 5),
]

KEYS = {"wb": (BACK, WORD), "wf": (FORWARD, WORD), "lb": (BACK, LINE), "lf": (FORWARD, LINE)}


@pytest.mark.parametrize("text, at, op, after, caret_after", NATIVE)
def test_a_mac_deletes_as_its_own_text_view_does(text, at, op, after, caret_after):
    """Review M2: punctuation, Japanese without spaces and emoji, as AppKit
    finds words, not as Qt does."""
    if not MAC:
        pytest.skip("a Mac's own word boundaries")
    ed = editor(structure=False)
    ed.setPlainText(text)
    caret(ed, at)
    QTest.keyClick(ed, *KEYS[op])
    # (Qt's plain text writes a no-break space as a space.)
    assert (ed.toPlainText(), ed.textCursor().position()) == (after.replace("\xa0", " "),
                                                              caret_after)
