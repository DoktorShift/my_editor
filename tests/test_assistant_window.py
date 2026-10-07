# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins how a page of an assistant window (nostr/ui/assistant.py) is given room.

What must hold:

  A page is laid out for its own height. The stack of pages used to answer
  the scroll area with the height of its tallest page, so a short page got
  a scroll bar, and a waiting page (Edit Profile's "Reading your profile…",
  the membership window's wait) showed an empty window: its words were
  centered in the tallest page's height, below what the window shows.

  Whatever page is shown, everything on it is on screen without scrolling,
  as long as the screen has room.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from nostr.ui.assistant import DEFAULT, AssistantWindow, page, text_label  # noqa: E402
from nostr.ui.profile_window import ProfileWindow  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def settle():
    for _ in range(4):
        QApplication.processEvents()


def window_with_a_short_and_a_tall_page():
    window = AssistantWindow("Test", is_dark=False)
    short, column = page()
    column.addWidget(text_label("A short page.", "title"))
    column.addStretch(1)
    window.add_page("short", short)
    tall, column = page()
    for number in range(12):
        column.addWidget(text_label(f"Line {number} of a page with much more to say.", ""))
    window.add_page("tall", tall)
    return window


def shows_all_of(window, widget) -> bool:
    """Whether ``widget`` is inside the part of the page the window shows."""
    viewport = window._scroll.viewport()
    top_left = widget.mapTo(viewport, QPoint(0, 0))
    return QRect(QPoint(0, 0), viewport.size()).contains(QRect(top_left, widget.size()))


def test_a_short_page_does_not_scroll_because_another_page_is_taller():
    window = window_with_a_short_and_a_tall_page()
    window.show_page("short", [("done", "Done", DEFAULT, window.accept)])
    window.show()
    settle()
    try:
        assert not window._scroll.verticalScrollBar().isVisible()
        window.show_page("tall", [("done", "Done", DEFAULT, window.accept)])
        settle()
        assert not window._scroll.verticalScrollBar().isVisible()
        tall = window._pages["tall"]
        assert all(shows_all_of(window, label) for label in tall.findChildren(QLabel))
    finally:
        window.close()


def test_a_waiting_page_shows_what_it_waits_for():
    window = ProfileWindow(read=lambda _done: None, save=lambda _changes, _done: None,
                           is_dark=False)
    window.show()
    settle()
    try:
        assert window.page == "reading"
        assert not window._scroll.verticalScrollBar().isVisible()
        assert shows_all_of(window, window._reading_label)
    finally:
        window.close()
