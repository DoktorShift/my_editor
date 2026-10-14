# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the File menu's two lists.

What must hold:

  Clear Recent Files is an ordinary menu item: reached by the keyboard like
  every other, in the menu's own color (clearing the list deletes no file),
  and dimmed while there is nothing to clear, as a Mac's Open Recent menu
  is. It used to be a red label painted into the menu, which arrow keys and
  Return could not reach, under an "(empty)" item.

  R Markdown, which few people write, is one submenu of the File menu
  instead of three items in it; its commands and Knit's key are unchanged.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QWidgetAction  # noqa: E402

from test_commands import StandIn  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def window():
    from main_window import MainWindow
    win = StandIn()
    MainWindow._build_actions(win)
    MainWindow._build_menu(win)
    return win


def file_menu(win):
    return next(m for m in win.menuBar().findChildren(QMenu)
                if m.parent() is win.menuBar() and "File" in m.title())


def recent_items(win, monkeypatch, paths):
    import main_window
    monkeypatch.setattr(main_window, "load_recent", lambda: list(paths))
    monkeypatch.setattr(main_window.os.path, "exists", lambda p: True)
    main_window.MainWindow._populate_recent_menu(win)
    return [a for a in win.m_recent.actions() if not a.isSeparator()]


def test_an_empty_list_offers_only_a_dimmed_clear(monkeypatch):
    win = window()
    items = recent_items(win, monkeypatch, [])
    assert [a.text() for a in items] == ["Clear Recent Files"]
    assert not items[0].isEnabled()
    assert not any(isinstance(a, QWidgetAction) for a in win.m_recent.actions())


def test_clear_follows_the_files_as_an_ordinary_item(monkeypatch):
    win = window()
    items = recent_items(win, monkeypatch, ["/notes/a.md", "/notes/b.md"])
    assert [a.text() for a in items] == ["a.md", "b.md", "Clear Recent Files"]
    clear = items[-1]
    assert clear.isEnabled() and not isinstance(clear, QWidgetAction)
    assert win.m_recent.actions()[-2].isSeparator()


def test_r_markdown_is_one_submenu_of_the_file_menu():
    win = window()
    top = [a.objectName() for a in file_menu(win).actions()]
    assert not {"file.knit_html", "file.knit_pdf", "file.rmd_toolchain"} & set(top)
    assert [a.objectName() for a in win.m_rmarkdown.actions() if not a.isSeparator()] == [
        "file.knit_html", "file.knit_pdf", "file.rmd_toolchain"]
    knit = win.commands.action("file.knit_html")
    assert knit.shortcut() == QKeySequence("Ctrl+Shift+K")


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_a_mac_has_a_window_menu_and_the_others_do_not(monkeypatch, platform):
    """Every Mac app has Window > Minimize (Command-M), Zoom and Bring All
    to Front, and Qt makes none, so Command-M did nothing in MyEditor.
    Between them, as in Safari, Show Previous Tab and Show Next Tab.
    Windows and Linux keep these in the title bar."""
    import main_window
    monkeypatch.setattr(main_window.sys, "platform", platform)
    win = window()
    titles = [m.title() for m in win.menuBar().findChildren(QMenu)
              if m.parent() is win.menuBar()]
    if platform != "darwin":
        assert "Window" not in titles and "window.minimize" not in win.commands
        return
    assert titles.index("Window") == titles.index("&Help") - 1     # just before Help
    entries = [a.objectName() for a in win.m_window.actions() if not a.isSeparator()]
    assert entries == ["window.minimize", "window.zoom", "nav.previous_tab", "nav.next_tab",
                       "window.bring_all"]
    assert win.commands.action("window.minimize").shortcut() == QKeySequence("Ctrl+M")


def test_refilling_the_list_leaves_no_old_items_behind(monkeypatch):
    from PySide6.QtGui import QAction
    win = window()
    recent_items(win, monkeypatch, ["/notes/a.md"])
    before = len(win.findChildren(QAction))
    for _ in range(3):
        recent_items(win, monkeypatch, ["/notes/a.md"])
    QApplication.processEvents()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert len(win.findChildren(QAction)) == before
