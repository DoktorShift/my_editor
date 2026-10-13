# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins Undo by runs of typing (editor.HtmlEditor._typing, undo_step) and
what Edit > Undo and Redo are called (commands.undo_words, redo_words).

What must hold, as in TextEdit, Pages and Word:

  One Undo takes back a whole run of typing: the characters typed one
  after another where the caret is, Return among them. A run ends when the
  person pauses (editor.TYPING_PAUSE_S), moves the caret or the selection,
  formats, pastes, deletes, uses any command or Undo, or saves. A run of
  Backspace, or of Delete, is one step of its own; typing after it, or
  deleting the other way, starts another.

  Edit > Undo and Redo name what they take back on macOS ("Undo Typing",
  "Undo Paste", "Undo Bold", "Undo Skip" in Imports), as its own apps do;
  on Windows and Linux they say "Undo" and "Redo", as Notepad and GNOME's
  Text Editor do.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, Qt  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import commands  # noqa: E402
import editor as editor_module  # noqa: E402
from editor import HtmlEditor  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


class Clock:
    """editor.time, standing still until moved on."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(editor_module, "time", fake)
    return fake


def typed(text=""):
    ed = HtmlEditor()
    ed.show()
    ed.setFocus()
    if text:
        QTest.keyClicks(ed, text)
    return ed


def key(ed, which, times=1):
    for _ in range(times):
        QTest.keyClick(ed, which)


def test_one_undo_takes_back_a_whole_run_of_typing(clock):
    ed = typed("Hello world")
    key(ed, Qt.Key.Key_Return)
    QTest.keyClicks(ed, "Second line")
    assert ed.undo_name() == "Typing"
    ed.undo()
    assert ed.toPlainText() == ""
    assert ed.redo_name() == "Typing"
    ed.redo()
    assert ed.toPlainText() == "Hello world\nSecond line"


def test_a_pause_ends_a_run(clock):
    ed = typed("First")
    clock.now += editor_module.TYPING_PAUSE_S + 0.1
    QTest.keyClicks(ed, " second")
    ed.undo()
    assert ed.toPlainText() == "First"


def test_moving_the_caret_or_the_selection_ends_a_run(clock):
    ed = typed("ab")
    key(ed, Qt.Key.Key_Left)
    key(ed, Qt.Key.Key_Right)                  # back where it was: still a new run
    QTest.keyClicks(ed, "cd")
    ed.undo()
    assert ed.toPlainText() == "ab"
    QTest.keyClick(ed, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClick(ed, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClicks(ed, "ef")
    ed.undo()
    assert ed.toPlainText() == "ab"


def test_formatting_pasting_and_saving_end_a_run(clock):
    ed = typed("ab")
    ed.toggle_bold()                           # for what is typed next
    QTest.keyClicks(ed, "cd")
    ed.undo()
    assert ed.toPlainText() == "ab"

    ed = typed("ab")
    data = QMimeData()
    data.setText("XY")
    ed.insertFromMimeData(data)
    assert ed.undo_name() == "Paste"
    QTest.keyClicks(ed, "cd")
    ed.undo()
    assert ed.toPlainText() == "abXY"
    ed.undo()
    assert ed.toPlainText() == "ab"

    ed = typed("ab")
    ed.document().setModified(False)           # saved
    QTest.keyClicks(ed, "cd")
    ed.undo()
    assert ed.toPlainText() == "ab"


def test_backspace_and_delete_runs_are_steps_of_their_own(clock):
    ed = typed("abcdef")
    key(ed, Qt.Key.Key_Backspace, 2)
    assert ed.toPlainText() == "abcd"
    ed.undo()                                  # the run of Backspace, whole
    assert ed.toPlainText() == "abcdef"
    ed.undo()                                  # the typing before it
    assert ed.toPlainText() == ""

    ed = typed("abcdef")
    key(ed, Qt.Key.Key_Backspace, 2)
    QTest.keyClicks(ed, "XY")                  # typing again: a run of its own
    ed.undo()
    assert ed.toPlainText() == "abcd"

    ed = typed("abcdef")
    cursor = ed.textCursor()
    cursor.setPosition(3)
    ed.setTextCursor(cursor)
    key(ed, Qt.Key.Key_Backspace)
    key(ed, Qt.Key.Key_Delete, 2)              # the other way: a run of its own
    assert ed.toPlainText() == "abf"
    ed.undo()                                  # the run of Delete, whole
    assert ed.toPlainText() == "abdef"


def test_a_command_is_a_step_named_by_what_it_does(clock):
    ed = typed("word")
    cursor = ed.textCursor()
    cursor.select(QTextCursor.SelectionType.WordUnderCursor)
    ed.setTextCursor(cursor)
    ed.toggle_bold()
    assert ed.undo_name() == "Bold"
    ed.cut()
    assert ed.undo_name() == "Cut"
    ed.undo()
    assert ed.redo_name() == "Cut" and ed.undo_name() == "Bold"


@pytest.mark.parametrize("platform, words", [
    ("darwin", ("Undo Typing", "Redo Paste", "Undo Skip", "Undo")),
    ("win32", ("Undo", "Redo", "Undo", "Undo")),
    ("linux", ("Undo", "Redo", "Undo", "Undo")),
])
def test_undo_and_redo_are_named_where_the_platform_names_them(monkeypatch, platform, words):
    monkeypatch.setattr(commands.sys, "platform", platform)
    assert (commands.undo_words("Typing"), commands.redo_words("Paste"),
            commands.undo_words("Skip"), commands.undo_words("")) == words


MENU_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QMimeData
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
w = main_window.MainWindow()
w.show()
ed = w.new_tab()
ed.setFocus()
QTest.keyClicks(ed, "Some words")
r = {"typed": (w.act_undo.text(), w.act_redo.text())}
data = QMimeData()
data.setText(" more")
ed.insertFromMimeData(data)
r["pasted"] = (w.act_undo.text(), w.act_redo.text())
w.act_undo.trigger()
r["undone"] = (w.act_undo.text(), w.act_redo.text())
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_edit_menu_names_undo_and_redo(tmp_path):
    from tests.app_process import run_window_script
    r = run_window_script(MENU_SCRIPT, tmp_path)
    undo, redo = commands.undo_words, commands.redo_words
    assert r["typed"] == [undo("Typing"), redo()]
    assert r["pasted"] == [undo("Paste"), redo()]
    assert r["undone"] == [undo("Typing"), redo("Paste")]


def commit(ed, text, preedit=""):
    """What an input method (a dead key, a CJK input) sends."""
    from PySide6.QtGui import QInputMethodEvent
    event = QInputMethodEvent(preedit, [])
    event.setCommitString(text)
    QApplication.sendEvent(ed, event)


def chord(ed, text, modifiers):
    """A character typed with Option (a Mac) or AltGr (Windows, Linux)."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        QApplication.sendEvent(ed, QKeyEvent(kind, Qt.Key.Key_unknown, modifiers, text))


def test_composed_and_option_characters_stay_in_the_run(clock):
    ed = typed("caf")
    commit(ed, "", preedit="´")                # a dead key, shown while composing
    commit(ed, "é")
    QTest.keyClicks(ed, " au lait")
    assert ed.toPlainText() == "café au lait" and ed.undo_name() == "Typing"
    ed.undo()
    assert ed.toPlainText() == ""

    ed = typed("")
    commit(ed, "日本")
    commit(ed, "語")
    ed.undo()
    assert ed.toPlainText() == ""

    ed = typed("mail")
    chord(ed, "@", Qt.KeyboardModifier.AltModifier)                     # Option on a Mac
    QTest.keyClicks(ed, "b.de ")
    chord(ed, "€", Qt.KeyboardModifier.ControlModifier
          | Qt.KeyboardModifier.AltModifier)                           # AltGr
    assert ed.toPlainText() == "mail@b.de €" and ed.undo_name() == "Typing"
    ed.undo()
    assert ed.toPlainText() == ""


def test_a_key_that_changes_nothing_does_not_rename_the_step_before(clock):
    ed = typed("word")
    cursor = ed.textCursor()
    cursor.select(QTextCursor.SelectionType.WordUnderCursor)
    ed.setTextCursor(cursor)
    ed.toggle_bold()
    cursor = ed.textCursor()
    cursor.setPosition(0)
    ed.setTextCursor(cursor)
    key(ed, Qt.Key.Key_Backspace)              # at the start: nothing to delete
    assert ed.undo_name() == "Bold"
