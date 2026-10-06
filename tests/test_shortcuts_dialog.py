# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins how the Keyboard Shortcuts window writes keys.

What must hold:

  On a Mac, keys are Apple's symbols in Apple's order (⌃⌥⇧⌘), side by
  side as in menus; elsewhere they are joined by "+" and named as the
  reader's keyboard names them.

  The editing keys that are not commands (Tab and Shift+Tab in lists,
  Return, Shift+Return, Command-click on a link) are listed.

  Filtering finds a shortcut by its name and by its keys, either way of
  writing them.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

import shortcuts_dialog  # noqa: E402
from shortcuts_dialog import OTHER_KEYS, ShortcutGroup, Shortcut, _key_names  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("keys, mac, shown", [
    ("Ctrl+Alt+Shift+V", True, ["⌥", "⇧", "⌘", "V"]),
    ("Meta+Ctrl+F", True, ["⌃", "⌘", "F"]),
    ("Shift+Tab", True, ["⇧", "⇥"]),
    ("Ctrl++", True, ["⌘", "+"]),
    ("Ctrl+Click", True, ["⌘", "Click"]),
    ("Ctrl+Alt+Shift+V", False, ["Ctrl", "Alt", "Shift", "V"]),
    ("Esc", False, ["Esc"]),
])
def test_keys_are_written_the_platform_way(keys, mac, shown):
    assert _key_names(keys, mac=mac) == shown


@pytest.mark.parametrize("mac", [True, False])
def test_a_mac_writes_keys_side_by_side(monkeypatch, mac):
    monkeypatch.setattr(shortcuts_dialog, "IS_MAC", mac)
    row = shortcuts_dialog._make_keycap_row("Ctrl+B")
    texts = [label.text() for label in row.findChildren(QLabel)]
    assert ("+" in texts) is (not mac)


def test_the_list_and_link_keys_are_listed():
    editing = dict(OTHER_KEYS)["Editing"]
    keys = [k for k, _words in editing]
    assert {"Tab", "Shift+Tab", "Enter", "Shift+Enter", "Ctrl+Click"} <= set(keys)


def test_filtering_finds_keys_written_either_way(monkeypatch):
    monkeypatch.setattr(shortcuts_dialog, "IS_MAC", True)
    group = ShortcutGroup(title="File", items=(Shortcut("Ctrl+S", "Save"),
                                               Shortcut("Ctrl+O", "Open file")))
    card = shortcuts_dialog._ShortcutCard(group)
    assert card.apply_filter("⌘S") == 1
    assert card.apply_filter("ctrl+s") == 1
    assert card.apply_filter("open") == 1
    assert card.apply_filter("nothing like it") == 0
