# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The inbox never stops MyEditor from starting.

The inbox is a convenience kept on this computer. A damaged file is set
aside (renamed, never deleted) and a new inbox started; a folder that
cannot be written to leaves imports unbound, and Nostr > Imports says
why instead of doing nothing. Without an account it says what to do.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from main_window import MainWindow
from nostr.imports.inbox_store import database_path
from nostr.imports_controller import SET_ASIDE
from nostr.ui.imports_window import ImportsWindow
from tests.outbox_fakes import settle
from tests.test_imports_controller import PK, Harness, profile


@pytest.fixture
def harness(tmp_path):
    h = Harness(tmp_path)
    yield h
    h.controller.account_changed(None)
    settle()


def test_a_damaged_inbox_is_set_aside_and_a_new_one_started(harness, tmp_path):
    path = database_path(tmp_path, PK)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"this is not a database at all" * 100)
    harness.controller.account_changed(profile())
    settle()
    controller = harness.controller
    assert controller.bound and not controller.problem
    assert controller.notice == SET_ASIDE
    aside = [p for p in path.parent.iterdir() if ".damaged-" in p.name]
    assert len(aside) == 1
    assert aside[0].read_bytes().startswith(b"this is not a database")
    # The window says so once, where the lists are.
    window = ImportsWindow(controller)
    assert window.notice.text() == SET_ASIDE
    assert not window.notice.isHidden()


@pytest.mark.skipif(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="folder permissions do not restrict this user")
def test_a_folder_that_cannot_be_written_leaves_imports_unbound(harness, tmp_path):
    folder = database_path(tmp_path, PK).parent
    folder.mkdir(parents=True)
    folder.chmod(0o500)
    try:
        changes = []
        harness.controller.bound_changed.connect(changes.append)
        harness.controller.account_changed(profile())   # does not raise
        settle()
        controller = harness.controller
        assert not controller.bound
        assert controller.problem.startswith("MyEditor can't keep imports on this computer")
        assert changes == [False]
        assert harness.checkers == []
        # Leaving the account forgets the problem with it.
        controller.account_changed(None)
        assert controller.problem == ""
    finally:
        folder.chmod(0o700)


def test_a_damaged_inbox_another_process_holds_is_left_alone(harness, tmp_path):
    from PySide6.QtCore import QLockFile
    path = database_path(tmp_path, PK)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"garbage" * 200)
    other = QLockFile(str(path.with_suffix(".lock")))
    assert other.tryLock(0)
    try:
        harness.controller.account_changed(profile())
        settle()
        assert not harness.controller.bound
        assert harness.controller.problem
        assert path.read_bytes().startswith(b"garbage")
    finally:
        other.unlock()


class TestTheCommand:
    """Nostr > Imports... says something in every state (M6)."""

    def run(self, imports, monkeypatch):
        import main_window
        told = []
        monkeypatch.setattr(main_window, "inform",
                            lambda parent, **kw: told.append(kw))
        host = SimpleNamespace(_imports=imports, is_dark_theme=False)
        MainWindow._open_imports_window(host)
        return told

    def test_bound_opens_the_window(self, monkeypatch):
        opened = []
        imports = SimpleNamespace(bound=True, problem="", open_window=lambda: opened.append(1))
        assert self.run(imports, monkeypatch) == []
        assert opened == [1]

    def test_without_an_account_it_says_what_to_do(self, monkeypatch):
        imports = SimpleNamespace(bound=False, problem="", open_window=None)
        (told,) = self.run(imports, monkeypatch)
        assert told["title"] == "Connect a signer first"
        assert "Connect Signer" in told["message"]

    def test_a_problem_is_said(self, monkeypatch):
        imports = SimpleNamespace(bound=False, problem="It cannot be kept.", open_window=None)
        (told,) = self.run(imports, monkeypatch)
        assert (told["title"], told["message"]) == ("Imports aren't available",
                                                    "It cannot be kept.")
