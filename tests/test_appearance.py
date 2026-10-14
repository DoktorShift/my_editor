# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins View > Appearance: Automatic, Light, Dark.

What must hold:

  MyEditor follows the system's light or dark appearance until Light or
  Dark is chosen, and Automatic goes back to following it. The old
  View > Toggle Dark/Light Theme stopped following the system the first
  time it was used, and nothing in the app could undo that (the Human
  Interface Guidelines ask that an app which offers its own appearance
  setting still respond to the system's).

  The choice is remembered across launches: Light or Dark is written to
  the settings, Automatic removes it.

  The menu shows the choice as one checked item of three.
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QAction, QActionGroup  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import settings  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def stored(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "_SETTINGS_FILE", str(path))
    return lambda: json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def window(*, follows=True, dark=False, system_dark=False):
    """Just the parts of MainWindow its appearance code uses."""
    group = QActionGroup(None)
    actions = {}
    for mode in ("system", "light", "dark"):
        action = QAction(mode, group)
        action.setCheckable(True)
        actions[mode] = action
    applied = []
    win = SimpleNamespace(_follow_os_theme=follows, is_dark_theme=dark, act_appearance=actions,
                          applied=applied, _group=group,
                          _detect_os_dark_theme=lambda: system_dark)

    def set_theme(is_dark):
        win.is_dark_theme = is_dark
        applied.append(is_dark)

    win._set_theme = set_theme
    return win


def choose(win, mode):
    from main_window import MainWindow
    MainWindow.set_appearance(win, mode)


def appearance(win):
    from main_window import MainWindow
    return MainWindow.appearance.fget(win)


def test_dark_is_remembered_and_stops_following_the_system(stored):
    win = window(follows=True, dark=False)
    choose(win, "dark")
    assert stored() == {"theme": "dark"}
    assert win.applied == [True] and win._follow_os_theme is False
    assert appearance(win) == "dark" and win.act_appearance["dark"].isChecked()


def test_automatic_forgets_the_choice_and_follows_the_system_again(stored):
    settings.save_settings({"theme": "light", "other": 1})
    win = window(follows=False, dark=False, system_dark=True)
    choose(win, "system")
    assert stored() == {"other": 1}                  # the choice is gone, nothing else
    assert win.applied == [True]                    # the system is dark right now
    assert win._follow_os_theme is True and appearance(win) == "system"
    assert win.act_appearance["system"].isChecked()


def test_the_system_is_followed_only_while_automatic(stored):
    from main_window import MainWindow
    win = window(follows=True, dark=False)
    MainWindow._on_os_color_scheme_changed(win, Qt.ColorScheme.Dark)
    assert win.applied == [True]
    choose(win, "light")
    MainWindow._on_os_color_scheme_changed(win, Qt.ColorScheme.Dark)
    assert win.applied == [True, False]              # Light stays light


def test_the_same_choice_twice_changes_nothing_on_screen(stored):
    win = window(follows=False, dark=True)
    choose(win, "dark")
    assert win.applied == []
