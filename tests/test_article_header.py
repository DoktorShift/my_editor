# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins an article's head on the page (nostr/ui/article_header.py).

What must hold: what is typed in the title and the summary is handed
back, trimmed, and showing the article again never overwrites a field
being typed in. Return and Down in the title go to the summary, Return
in the summary leaves for the text, Up on its first line goes back to
the title. Without a cover, Add Cover is offered (its room kept, so the
page does not jump); a cover typed as an address shows no picture and
says so; Remove and Change hand the choice to the window. The summary
grows with its lines, up to five. Every control has a name.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.ui.article_header import SUMMARY_MAX_LINES, ArticleHeader  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    yield QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def header():
    head = ArticleHeader()
    head.resize(600, 300)
    head.show()
    QApplication.processEvents()
    yield head
    head.close()


def test_what_is_typed_is_handed_back_trimmed(header):
    said = []
    header.edited.connect(lambda title, summary: said.append((title, summary)))
    header.title_edit.setText("Plain text ")
    header.summary_edit.setPlainText(" Why files outlive apps.")
    assert said[-1] == ("Plain text", "Why files outlive apps.")


def test_showing_the_article_again_never_overwrites_a_field_being_typed_in(header):
    said = []
    header.edited.connect(lambda *details: said.append(details))
    header.show_article("From the inspector", "Its summary", "")
    assert header.title_edit.text() == "From the inspector" and said == []
    header.title_edit.setFocus()
    header.title_edit.setText("Typed here")
    header.show_article("Typed here", "Its summary", "")
    header.show_article("Something older", "Its summary", "")
    assert header.title_edit.text() == "Typed here"


def test_return_and_the_arrows_move_between_the_fields_and_out_to_the_text(header):
    left = []
    header.leave_down.connect(lambda: left.append(True))
    header.title_edit.setFocus()
    QTest.keyClick(header.title_edit, Qt.Key_Return)
    assert header.summary_edit.hasFocus()
    QTest.keyClick(header.summary_edit, Qt.Key_Up)
    assert header.title_edit.hasFocus()
    QTest.keyClick(header.title_edit, Qt.Key_Down)
    assert header.summary_edit.hasFocus()
    QTest.keyClick(header.summary_edit, Qt.Key_Return)
    assert left == [True] and header.summary_edit.toPlainText() == ""


def test_coming_up_from_the_text_lands_at_the_end_of_the_summary(header):
    header.show_article("Title", "A summary", "")
    header.enter_from_below()
    assert header.summary_edit.hasFocus()
    assert header.summary_edit.textCursor().atEnd()


def test_the_cover_is_offered_shown_and_handed_to_the_window(header):
    asked, removed = [], []
    header.cover_requested.connect(lambda: asked.append(True))
    header.cover_removed.connect(lambda: removed.append(True))
    assert header.add_cover.isVisible() and not header.banner.isVisible()
    header.add_cover.click()
    header.show_article("", "", "https://cdn.example/cover.jpg")
    assert header.banner.isVisible() and not header.add_cover.isVisible()
    assert header.banner._picture.isNull() and header.banner._words == "Cover from the web"
    header.change_cover.click()
    header.remove_cover.click()
    assert asked == [True, True] and removed == [True]


def test_add_cover_keeps_its_room_so_the_page_does_not_jump(header):
    header.title_edit.setFocus()
    QApplication.processEvents()
    near = header.height_for(600)
    header.setFocus()
    header.clearFocus()
    QApplication.processEvents()
    assert header.height_for(600) == near


def test_the_summary_grows_with_its_lines_up_to_five(header):
    header.set_text_font(QFont("Helvetica", 14))
    one = header.summary_edit.sizeHint().height()
    header.summary_edit.setPlainText("A line. " * 200)
    QApplication.processEvents()
    many = header.summary_edit.sizeHint().height()
    line = header.summary_edit.fontMetrics().lineSpacing()
    assert many == SUMMARY_MAX_LINES * line + 2 and one == line + 2


def test_every_control_has_a_name(header):
    header.show_article("", "", "https://cdn.example/cover.jpg")
    assert unnamed_controls(header) == []
