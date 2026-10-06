# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every control in these windows has a name a screen reader can announce.

Built on Qt's own accessibility interfaces (tests/accessibility.py), which
are what VoiceOver, NVDA and Orca read. Windows that are still being built
in other workstreams add the same check to their own tests.
"""

import pytest

from alerts import CANCEL, DEFAULT, Button, Alert
from nostr.key_vault import KeyVault
from nostr.profiles import ProfileStore
from nostr.ui.account_windows import (
    BackupAccountWindow, CreateAccountWindow, MoveToSignerWindow, RestoreAccountWindow,
)
from nostr.ui.connect_dialog import ConnectDialog
from nostr.ui.profile_window import ProfileWindow
from shortcuts_dialog import ShortcutsDialog, groups_from
from tests.accessibility import unnamed_controls

SECRET = bytes.fromhex("1f" * 32)


def membership(tmp_path):
    from tests.test_membership_window import window
    return window()


def profile(tmp_path):
    return ProfileWindow(read=lambda _done: None, save=lambda _changes, _done: None,
                         is_dark=False)


def backup(tmp_path):
    return BackupAccountWindow(secret=SECRET, is_dark=False)


def create(tmp_path):
    return CreateAccountWindow(store=ProfileStore(tmp_path / "profiles.json"),
                               vault=KeyVault(tmp_path / "keys.json"),
                               connect_signer=lambda *a, **k: None, is_dark=False)


def restore(tmp_path):
    return RestoreAccountWindow(store=ProfileStore(tmp_path / "profiles.json"),
                                vault=KeyVault(tmp_path / "keys.json"), is_dark=False)


def move_to_signer(tmp_path):
    return MoveToSignerWindow(secret=SECRET, name="Alice",
                              connect_signer=lambda *a, **k: None, is_dark=False)


def connect(tmp_path):
    from tests.test_connect_dialog import FakePool
    return ConnectDialog(FakePool(), ProfileStore(tmp_path / "profiles.json"), persist=False,
                         is_dark=False)


def update(tmp_path):
    from tests.test_update_dialog import FakeInstaller, automatic_dialog
    return automatic_dialog(FakeInstaller())


def alert(tmp_path):
    return Alert(None, title="Delete “Notes”?", message="It cannot be undone.",
                 buttons=(Button("Cancel", "cancel", CANCEL), Button("Delete", "delete", DEFAULT)),
                 checkbox="Don’t ask again", is_dark=False)


def shortcuts(tmp_path):
    return ShortcutsDialog(groups_from([("Editing", [("Ctrl+B", "Bold")])]), is_dark=False)


WINDOWS = [membership, profile, backup, create, restore, move_to_signer, connect, update,
           alert, shortcuts]


@pytest.mark.parametrize("make", WINDOWS, ids=[w.__name__ for w in WINDOWS])
def test_every_control_has_a_name(make, tmp_path):
    window = make(tmp_path)
    try:
        assert unnamed_controls(window) == []
    finally:
        window.close()
        window.deleteLater()
