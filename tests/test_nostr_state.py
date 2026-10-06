# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the one "Nostr in use" signal.

What must hold:

  Nostr is in use exactly while an account is active.

  ``changed`` fires once per real change and never for a refresh that
  changed nothing, so it can be refreshed at every account transition.

  The window refreshes it where every account change passes through, and
  the Keyboard Shortcuts window follows it.
"""

import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.state import NostrState  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def test_in_use_while_an_account_is_active():
    account = {"profile": None}
    state = NostrState(lambda: account["profile"])
    assert not state.active
    account["profile"] = object()
    state.refresh()
    assert state.active


def test_changes_are_announced_once_and_only_when_real():
    account = {"profile": None}
    state = NostrState(lambda: account["profile"])
    seen = []
    state.changed.connect(seen.append)
    state.refresh()
    assert seen == []                       # nothing changed
    account["profile"] = object()
    state.refresh()
    state.refresh()
    account["profile"] = object()           # switching accounts: still in use
    state.refresh()
    account["profile"] = None
    state.refresh()
    assert seen == [True, False]


def test_the_window_refreshes_it_at_every_account_change():
    from main_window import MainWindow
    source = inspect.getsource(MainWindow._update_account_actions)
    assert "nostr_state" in source and ".refresh()" in source
    # Every account transition refreshes the chip menu, which runs that.
    menu = inspect.getsource(MainWindow._refresh_profile_chip_menu)
    assert "_update_account_actions()" in menu


def test_the_shortcut_list_follows_it():
    from main_window import MainWindow
    source = inspect.getsource(MainWindow._show_shortcuts)
    assert "nostr=self.nostr_state.active" in source
