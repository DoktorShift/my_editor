# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the indicator shown while dictating on this computer
(dictation/indicator.py).

What must hold, as a Mac shows its dictation: a small panel at the
insertion point, inside the text's view, that follows the insertion point
as phrases are written; a meter that follows the voice; "Getting ready…"
until the model listens, then "Listening"; Done, Escape in the text, or
hiding it, all stop dictating. It never takes the keyboard focus, and a
screen reader hears what it does.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from dictation.indicator import DictationIndicator  # noqa: E402
from editor import HtmlEditor  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    yield QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def shown():
    editor = HtmlEditor()
    editor.resize(500, 300)
    editor.setPlainText("First line.\nSecond line.")
    editor.show()
    editor.setFocus()
    indicator = DictationIndicator(editor)
    stops = []
    indicator.done.connect(lambda: stops.append(True))
    indicator.open()
    QApplication.processEvents()
    yield editor, indicator, stops
    editor.close()


def test_it_shows_at_the_insertion_point_inside_the_view(shown):
    editor, indicator, _stops = shown
    caret = editor.cursorRect()
    assert indicator.isVisible()
    assert editor.viewport().rect().contains(indicator.geometry())
    assert abs(indicator.geometry().center().y() - caret.center().y()) < 80
    before = indicator.pos()
    cursor = editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    cursor.insertText(" A phrase written. " * 3)
    QApplication.processEvents()
    assert indicator.pos() != before                       # it follows the insertion point


def test_it_says_when_it_listens_and_follows_the_voice(shown):
    _editor, indicator, _stops = shown
    assert indicator.state_text() == "Getting ready…"
    indicator.set_listening(True)
    assert indicator.state_text() == "Listening"
    indicator.set_level(0.0)
    quiet = indicator.lit_bars()
    indicator.set_level(0.6)
    assert indicator.lit_bars() > quiet


def test_done_or_escape_stops_and_it_never_takes_the_focus(shown):
    editor, indicator, stops = shown
    for widget in [indicator, *indicator.findChildren(QWidget)]:
        assert widget.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert unnamed_controls(indicator) == []
    indicator.done_button.click()
    assert stops == [True]
    QTest.keyClick(editor, Qt.Key.Key_Escape)
    assert stops == [True, True]
    indicator.close_quietly()
    QTest.keyClick(editor, Qt.Key.Key_Escape)              # gone: Escape is the editor's again
    assert stops == [True, True] and not indicator.isVisible()
