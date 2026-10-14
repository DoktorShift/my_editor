# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the header above an editor's text (editor_header.py).

What must hold: the header sits in the text column, above the text, and
is not in the document (no step of Undo, not modified). Scrolling down
takes it away first at the speed of the text, then the text scrolls;
reaching the top brings it back. Up on the first line goes into the
header; the header hands the caret back to the start of the text. A
header that grows or shrinks gets its room; detached, the text has its
room back.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QTextCursor, QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout  # noqa: E402

from editor import HtmlEditor  # noqa: E402
from editor_header import EditorHeader, HeaderWidget  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    yield QApplication.instance() or QApplication(sys.argv)


class Head(HeaderWidget):
    def __init__(self, height=120):
        super().__init__()
        self.field = QLineEdit(self)
        layout = QVBoxLayout(self)
        layout.addWidget(self.field)
        self.setFixedHeight(height)

    def enter_from_below(self):
        self.field.setFocus()


def editor_with_header(height=120, lines=200):
    ed = HtmlEditor()
    ed.resize(700, 400)
    ed.setPlainText("\n".join(f"Line {i}" for i in range(lines)))
    ed.document().setModified(False)
    ed.show()
    QApplication.processEvents()
    head = Head(height)
    slot = EditorHeader(ed, head)
    QApplication.processEvents()
    return ed, head, slot


def scroll_by(ed, pixels):
    bar = ed.verticalScrollBar()
    bar.setValue(bar.value() + pixels)
    QApplication.processEvents()


def test_the_header_sits_above_the_text_in_its_column_and_is_not_in_the_document():
    ed, head, slot = editor_with_header()
    assert slot.shown() == 120 and ed.top_inset() == 120
    viewport = ed.viewport().geometry()
    clip = head.parentWidget().geometry()
    margin = int(ed.document().documentMargin())
    assert clip.left() == viewport.left() + margin
    assert clip.width() == viewport.width() - 2 * margin
    assert clip.bottom() < viewport.top() + 1
    assert not ed.document().isModified() and not ed.document().isUndoAvailable()


def test_scrolling_down_takes_the_header_away_first_then_the_text_scrolls():
    ed, head, slot = editor_with_header()
    scroll_by(ed, 50)
    assert slot.shown() == 70 and ed.verticalScrollBar().value() == 0
    scroll_by(ed, 100)
    assert slot.shown() == 0 and ed.verticalScrollBar().value() == 30


def test_reaching_the_top_brings_it_back():
    ed, head, slot = editor_with_header()
    scroll_by(ed, 400)
    assert slot.shown() == 0
    ed.verticalScrollBar().setValue(0)
    QApplication.processEvents()
    assert slot.shown() == 120


def test_the_wheel_at_the_top_brings_it_back_bit_by_bit():
    ed, head, slot = editor_with_header()
    scroll_by(ed, 60)
    assert slot.shown() == 60
    wheel = QWheelEvent(QPointF(10, 10), QPointF(ed.viewport().mapToGlobal(QPoint(10, 10))),
                        QPoint(0, 40), QPoint(0, 0), Qt.NoButton, Qt.NoModifier,
                        Qt.ScrollUpdate, False)
    QApplication.sendEvent(ed.viewport(), wheel)
    assert slot.shown() == 100


def test_up_on_the_first_line_goes_into_the_header_and_it_hands_the_caret_back():
    ed, head, slot = editor_with_header()
    ed.setFocus()
    cursor = ed.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    cursor.movePosition(QTextCursor.MoveOperation.Right, n=3)
    ed.setTextCursor(cursor)
    QTest.keyClick(ed, Qt.Key_Up)
    assert head.field.hasFocus()
    head.leave_down.emit()
    assert ed.hasFocus() and ed.textCursor().position() == 0


def test_a_header_that_grows_gets_its_room_and_detached_gives_it_back():
    ed, head, slot = editor_with_header()
    head.setFixedHeight(180)
    QApplication.processEvents()
    assert slot.shown() == 180 and ed.top_inset() == 180
    slot.detach()
    QApplication.processEvents()
    assert ed.top_inset() == 0


def test_paper_mode_keeps_the_room_above_the_text():
    ed, head, slot = editor_with_header()
    ed.set_paper_mode(True)
    QApplication.processEvents()
    assert ed.viewportMargins().top() == 120
