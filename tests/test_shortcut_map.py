# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins MyEditor's keyboard map: MyEditor 3.3's keys, on every system.

What must hold:

  Every key MyEditor 3.3 had still does what it did, or what took its
  command's place (Reset Format is Clear Formatting, Insert image… is
  Insert Image from Media Library…), on a Mac, on Windows and on Linux.
  The one exception is logged in OPS map and the user guide: Shift-Command-P
  is Page Setup on a Mac (Apple's guidelines), so Publish as Note is
  Option-Command-P there.

  The PDF reader and the Media Library keep their 3.3 keys too.
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import commands  # noqa: E402

PLATFORMS = ("darwin", "win32", "linux")
STD = QKeySequence.StandardKey

# MyEditor 3.3's keys (main_window.py, _build_actions and _build_menu), by
# the command that has them now.
V33 = {
    "file.new": STD.New, "file.open": STD.Open, "file.save": STD.Save,
    "file.save_as": "Ctrl+Shift+S", "file.print": STD.Print,
    "file.knit_html": "Ctrl+Shift+K", "file.close_tab": "Ctrl+W", "file.quit": "Ctrl+Q",
    "format.bold": "Ctrl+B", "format.italic": "Ctrl+I", "format.underline": "Ctrl+U",
    "format.reset": "Ctrl+D",                          # was Reset Format
    "search.find": "Ctrl+F", "search.next": "F3", "search.previous": "Shift+F3",
    "view.appearance.switch": "Ctrl+Shift+T",          # was Toggle Dark/Light Theme
    "view.line_numbers": "Ctrl+Shift+L", "view.syntax_highlighting": "Ctrl+Shift+H",
    "view.fullscreen": STD.FullScreen,
    "nostr.publish_note": "Ctrl+Shift+P", "nostr.publish_article": "Ctrl+Shift+A",
    "nostr.media_library": "Ctrl+Shift+M",
    "nostr.insert_image": "Ctrl+Shift+I",              # was Insert image…
    "nostr.drafts": "Ctrl+Shift+D",
}
# Where a system's own convention has the key, and what the command took.
MOVED = {("darwin", "nostr.publish_note"): "Ctrl+Alt+P"}


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def window_on(platform, monkeypatch):
    from main_window import MainWindow
    from tests.test_commands import StandIn
    monkeypatch.setattr(commands, "sys", types.SimpleNamespace(platform=platform))
    win = StandIn()
    MainWindow._build_actions(win)          # refuses two commands on one key
    return win


def bound(action):
    return {k.toString(QKeySequence.SequenceFormat.PortableText) for k in action.shortcuts()}


@pytest.mark.parametrize("platform", PLATFORMS)
def test_every_key_of_3_3_still_does_its_job(monkeypatch, platform):
    win = window_on(platform, monkeypatch)
    for command_id, keys in V33.items():
        keys = MOVED.get((platform, command_id), keys)
        expected = set(commands.all_key_texts(keys))
        assert expected <= bound(win.commands.action(command_id)), (platform, command_id)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_the_key_3_3_gave_up_on_a_mac_is_page_setup_there(monkeypatch, platform):
    win = window_on(platform, monkeypatch)
    page_setup = bound(win.commands.action("file.page_setup"))
    assert ("Ctrl+Shift+P" in page_setup) is (platform == "darwin")


def test_the_pdf_reader_keeps_its_3_3_keys():
    from pdf_viewer import reader_keys
    keys = {k for _s, _w, _a, tab, view in reader_keys() for k in tab + view}
    assert {"Ctrl+=", "Ctrl+0", "F12", "Space", "Shift+Space", "Home", "End", "J", "K",
            "N", "P", "+", "-", "G", "Esc"} <= keys
    assert set(commands.all_key_texts(STD.ZoomIn)) <= keys
    assert set(commands.all_key_texts(STD.ZoomOut)) <= keys
    assert set(commands.all_key_texts(STD.Copy)) <= keys


def test_the_media_library_keeps_its_3_3_keys():
    from nostr.ui.media_grid import MediaGrid
    grid = MediaGrid(is_dark=False)
    heard = []
    for name in ("copy_requested", "save_requested", "paste_requested", "delete_requested"):
        getattr(grid, name).connect(lambda name=name: heard.append(name))

    def press(keys):
        combo = QKeySequence(keys)[0]
        grid.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, combo.key(),
                                     combo.keyboardModifiers()))

    for keys in (QKeySequence(STD.Copy), QKeySequence(STD.Save), QKeySequence(STD.Paste),
                 QKeySequence(Qt.Key.Key_Delete), QKeySequence(Qt.Key.Key_Backspace)):
        press(keys)
    assert heard == ["copy_requested", "save_requested", "paste_requested",
                     "delete_requested", "delete_requested"]
    grid.deleteLater()
