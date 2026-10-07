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
from tests.widget_lifetime import delete_new_windows


@pytest.fixture(autouse=True)
def _windows_deleted():
    """Every window and panel a test makes is deleted after it: left to
    the cycle collector, one without a parent can crash it."""
    yield from delete_new_windows()


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

    def run(self, imports, monkeypatch, *, answer=None):
        import main_window
        told, asked, ran = [], [], []
        monkeypatch.setattr(main_window, "inform",
                            lambda parent, **kw: told.append(kw))

        def ask(parent, **kw):
            asked.append(kw)
            return answer
        monkeypatch.setattr(main_window, "ask", ask)
        host = SimpleNamespace(_imports=imports, is_dark_theme=False,
                               _on_create_account=lambda: ran.append("create"),
                               _on_nostr_connect=lambda: ran.append("connect"))
        host._offer_account = lambda **kw: MainWindow._offer_account(host, **kw)
        MainWindow._open_imports_window(host)
        return told, asked, ran

    def test_bound_opens_the_window(self, monkeypatch):
        opened = []
        imports = SimpleNamespace(bound=True, problem="", open_window=lambda: opened.append(1))
        assert self.run(imports, monkeypatch) == ([], [], [])
        assert opened == [1]

    @pytest.mark.parametrize("answer", ["create", "connect", None])
    def test_without_an_account_it_offers_the_ways_in(self, monkeypatch, answer):
        """CP2 live run: it said only "Connect a signer first", though an
        account can also be made, or its key kept, on this computer."""
        import alerts
        imports = SimpleNamespace(bound=False, problem="", open_window=None)
        told, (asked,), ran = self.run(imports, monkeypatch, answer=answer)
        assert told == []
        assert asked["title"] == "Imports need a Nostr account"
        assert asked["message"] == "Imported posts become drafts in your Nostr account."
        buttons = [(b.label, b.value, b.role) for b in asked["buttons"]]
        assert buttons == [("Create Account\u2026", "create", alerts.DEFAULT),
                           ("Connect Signer\u2026", "connect", alerts.NORMAL),
                           ("Cancel", None, alerts.CANCEL)]
        assert ran == ([answer] if answer else [])

    def test_a_problem_is_said(self, monkeypatch):
        imports = SimpleNamespace(bound=False, problem="It cannot be kept.", open_window=None)
        (told,), asked, _ran = self.run(imports, monkeypatch)
        assert asked == []
        assert (told["title"], told["message"]) == ("Imports aren't available",
                                                    "It cannot be kept.")


def test_the_other_nostr_commands_offer_an_account_the_same_way(monkeypatch):
    """Publishing, the media library and the profile ask the same way."""
    offered = []
    host = SimpleNamespace(_profile_store=SimpleNamespace(default=lambda: None),
                           is_dark_theme=False)
    host._offer_account = lambda **kw: offered.append(kw["title"])
    host._offer_publishing_account = lambda: MainWindow._offer_publishing_account(host)
    host._offer_media_account = lambda: MainWindow._offer_media_account(host)
    for command in (MainWindow._on_nostr_publish_note, MainWindow._on_nostr_publish_article,
                    MainWindow._on_nostr_media_library, MainWindow._on_edit_profile):
        command(host)
    assert offered == ["Publishing needs a Nostr account", "Publishing needs a Nostr account",
                       "The media library needs a Nostr account",
                       "Your profile needs a Nostr account"]


class TestTheLock:
    """CP2 live run: a quit without a closed window left the inbox's lock to
    its destructor, after the exit handlers, and it complained."""

    def test_a_normal_quit_leaves_no_lock(self, harness, tmp_path):
        controller = harness.controller
        controller.account_changed(profile())
        settle()
        lock = database_path(tmp_path, PK).with_suffix(".lock")
        assert lock.exists() and not controller.read_only
        controller.flush()
        controller.shutdown()
        assert not lock.exists()
        assert not controller.bound
        assert controller._exit_hook is None

    def test_a_lock_still_held_at_exit_is_given_back(self, harness, tmp_path):
        controller = harness.controller
        controller.account_changed(profile())
        settle()
        lock = database_path(tmp_path, PK).with_suffix(".lock")
        hook = controller._exit_hook
        assert hook is not None
        hook()                      # what the exit handler does
        assert not lock.exists()
        assert controller._exit_hook is None

    def test_a_lock_left_by_a_crash_is_taken_over(self, harness, tmp_path):
        """A lock nothing gave back (its process crashed) is taken over at
        the next start. Left here as such a process leaves it: Qt's own
        lock file, naming a process that does not exist (no process
        number can be 4194307: it is above Linux's and macOS's highest and
        not a multiple of four, as Windows' are), so no other process has
        to start or end for the test."""
        from PySide6.QtCore import QLockFile
        lock = database_path(tmp_path, PK).with_suffix(".lock")
        lock.parent.mkdir(parents=True)
        mine = QLockFile(str(lock))
        assert mine.tryLock(0)
        fields = lock.read_bytes().split(b"\n")
        mine.unlock()
        fields[0] = b"4194307"
        lock.write_bytes(b"\n".join(fields))
        harness.controller.account_changed(profile())
        settle()
        assert harness.controller.bound
        assert not harness.controller.read_only
        assert lock.read_text(encoding="utf-8").splitlines()[0] == str(os.getpid())
