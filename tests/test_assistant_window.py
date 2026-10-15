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

  A page's pull-down button (a menu in place of a handler) opens its menu,
  sits where the page puts it, and never takes Return from the action.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtGui import QAccessible  # noqa: E402
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


@pytest.mark.parametrize("mac, chosen", [(True, "done"), (False, "cancel")])
def test_return_on_a_focused_button_follows_the_system(monkeypatch, mac, chosen):
    # The sheets and windows built on this one (Draft Relays, Source
    # Settings, Edit Profile, the account windows) answer Return as the
    # alerts and dialogs do: on a Mac the default, whichever button has the
    # focus; on Windows and Linux the focused button. From a field, Return
    # chooses the default everywhere.
    import alerts
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLineEdit
    from nostr.ui.assistant import NORMAL
    monkeypatch.setattr(alerts, "IS_MAC", mac)
    pressed = []
    window = AssistantWindow("Test", is_dark=False)
    form, column = page()
    field = QLineEdit()
    column.addWidget(field)
    window.add_page("form", form)
    window.show_page("form", [("cancel", "Cancel", NORMAL, lambda: pressed.append("cancel")),
                              ("done", "Done", DEFAULT, lambda: pressed.append("done"))])
    window.show()
    settle()
    try:
        field.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(field, Qt.Key.Key_Return)
        assert pressed == ["done"]
        pressed.clear()
        cancel = window.buttons["cancel"]
        cancel.setFocus(Qt.FocusReason.TabFocusReason)
        settle()
        QTest.keyClick(cancel, Qt.Key.Key_Return)
        assert pressed == [chosen]
    finally:
        window.close()


def test_a_menu_in_place_of_a_handler_makes_a_pull_down_button():
    # Manage on the membership window's member page: a button that opens
    # a menu of commands, at the place given, never the default (Return
    # stays with the page's action), and without Qt's own menu indicator
    # (a dot on a Mac): the Disclosure arrow is drawn at its end, as on
    # the format bar's pop-up.
    from PySide6.QtWidgets import QMenu
    from format_toolbar import Disclosure
    from nostr.ui.assistant import LEADING, PULL_DOWN_ARROW_END
    window = AssistantWindow("Test", is_dark=False)
    body, column = page()
    column.addWidget(text_label("A page.", ""))
    window.add_page("page", body)
    menu = QMenu(window)
    menu.addAction("Copy")
    window.show_page("page", [("manage", "Manage", LEADING, menu),
                              ("done", "Done", DEFAULT, window.accept)])
    manage = window.buttons["manage"]
    assert manage.menu() is menu
    assert not manage.isDefault() and window.buttons["done"].isDefault()
    assert manage.findChildren(Disclosure)
    # A screen reader announces it as a button with a menu, by its title.
    spoken = QAccessible.queryAccessibleInterface(manage)
    assert spoken.role() == QAccessible.Role.ButtonMenu
    assert spoken.text(QAccessible.Text.Name) == "Manage"
    # As wide as its title, its padding and its arrow need: Qt keeps room
    # for its own indicator too, which is not drawn.
    manage.ensurePolished()
    words = manage.fontMetrics().horizontalAdvance("Manage")
    room = 14 + PULL_DOWN_ARROW_END + 2 * Disclosure.WIDTH + 2      # padding and border
    assert manage.sizeHint().width() <= words + room + 2
    row = window._button_row
    widgets = [row.itemAt(i).widget() for i in range(row.count()) if row.itemAt(i).widget()]
    assert widgets[0] is manage and widgets[-1] is window.buttons["done"]
    # The page shown next brings its own buttons; the menu stays the page's.
    window.show_page("page", [("done", "Done", DEFAULT, window.accept)])
    assert "manage" not in window.buttons
    assert menu.actions()[0].text() == "Copy"


def test_a_window_opens_as_tall_as_its_page_needs():
    # Every assistant the app opens has the main window as its parent, and
    # such a window is laid out before it is shown at a width it is never
    # shown at (its least width, or a child's first size): its wrapped
    # words were measured narrower than they are shown, and it opened
    # taller than its page, with an empty band above its buttons.
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtWidgets import QWidget

    class Row(QWidget):
        """A row that would like more room than the window's least width
        gives it (a row of controls), and makes do with less."""

        def sizeHint(self):
            return QSize(520, 10)

        def minimumSizeHint(self):
            return QSize(100, 10)

    parent = QWidget()
    window = AssistantWindow("Test", is_dark=False, parent=parent)
    window.setWindowFlag(Qt.WindowType.Window, True)
    body, column = page()
    column.addWidget(Row())
    window.add_page("page", body)
    window.show_page("page", [("done", "Done", DEFAULT, window.accept)])
    margins = window.layout().contentsMargins()
    sides = margins.left() + margins.right()
    least = window.minimumWidth() - sides
    opens_at = window.sizeHint().width() - sides       # its size hint's, as a window opens
    # Lines that wrap at the window's least width and fit on one line at
    # the width it opens at (both differ from one system's fonts to the
    # next), measured with the page's own labels and style.
    lines = [text_label("", "") for _ in range(6)]
    for line in lines:
        column.addWidget(line)
    first = lines[0]
    first.ensurePolished()
    pattern = "word " * 40
    while first.heightForWidth(least) <= first.heightForWidth(opens_at):
        assert len(first.text()) < len(pattern), "no line wraps only at the least width"
        first.setText(pattern[:len(first.text()) + 1])
    for line in lines:
        line.setText(first.text())
    settle()
    # Measured at the width it opens at.
    assert window.sizeHint().width() - sides == opens_at
    assert window._stack.minimumHeight() == body.layout().totalHeightForWidth(opens_at)
    window.show()
    settle()
    try:
        assert window.height() == window.minimumHeight()
        assert window._scroll.height() == window._stack.minimumHeight()
    finally:
        window.close()
