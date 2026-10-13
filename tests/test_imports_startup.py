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
from nostr.imports_controller import (
    CANNOT_OPEN,
    CANNOT_WRITE,
    DAMAGED_ELSEWHERE,
    DAMAGED_HELD,
    DISK_FULL,
    IN_USE,
    SET_ASIDE,
    plain_problem,
)
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


def test_a_damaged_inbox_is_closed_before_it_is_set_aside(tmp_path, monkeypatch):
    # Windows cannot move a file that is still open: the store that could
    # not read it lets go of it first.
    import sqlite3

    from nostr.imports import inbox_store
    opened = []
    connect = sqlite3.connect
    monkeypatch.setattr(inbox_store.sqlite3, "connect",
                        lambda *args, **kwargs: opened.append(connect(*args, **kwargs))
                        or opened[-1])
    path = tmp_path / "inbox.sqlite"
    path.write_bytes(b"this is not a database at all" * 100)
    with pytest.raises(sqlite3.DatabaseError):
        inbox_store.InboxStore(path)
    with pytest.raises(sqlite3.ProgrammingError):        # closed
        opened[0].execute("SELECT 1")


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
        assert controller.problem == CANNOT_WRITE.format(folder=str(folder))
        assert changes == [False]
        assert harness.checkers == []
        # Choosing Imports again tries again: once the folder can be
        # written to, the inbox opens.
        assert controller.retry() is False
        folder.chmod(0o700)
        assert controller.retry() is True
        assert controller.bound and controller.problem == ""
        assert changes == [False, False, True]
        # Leaving the account forgets a problem with it.
        controller.account_changed(None)
        folder.chmod(0o500)
        controller.account_changed(profile())
        assert controller.problem == CANNOT_WRITE.format(folder=str(folder))
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
        assert harness.controller.problem == DAMAGED_ELSEWHERE
        assert path.read_bytes().startswith(b"garbage")
    finally:
        other.unlock()
    # The other MyEditor has quit: choosing Imports again sets it aside.
    assert harness.controller.retry() is True
    assert harness.controller.notice == SET_ASIDE


def test_a_damaged_inbox_another_program_holds_is_said_so(harness, tmp_path, monkeypatch):
    """Windows refuses to rename a file another program has open (a
    backup or sync app): it is left as it is, and the message says why."""
    import pathlib
    path = database_path(tmp_path, PK)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"garbage" * 200)
    real_replace = pathlib.Path.replace

    def held(source, target):
        if pathlib.Path(source) == path:
            raise PermissionError(13, "The process cannot access the file because it is "
                                      "being used by another process", str(source))
        return real_replace(source, target)
    monkeypatch.setattr(pathlib.Path, "replace", held)
    harness.controller.account_changed(profile())
    settle()
    assert not harness.controller.bound
    assert harness.controller.problem == DAMAGED_HELD
    assert path.read_bytes().startswith(b"garbage")
    monkeypatch.setattr(pathlib.Path, "replace", real_replace)    # the program quit
    assert harness.controller.retry() is True
    assert harness.controller.notice == SET_ASIDE


class TestThePlainWords:
    """Each cause says what to do, never the error's own words (N1)."""

    def sqlite_error(self, name, text="x"):
        import sqlite3
        exc = sqlite3.OperationalError(text)
        exc.sqlite_errorname = name
        return exc

    def test_the_causes(self, tmp_path):
        import errno
        folder = tmp_path / "imports"
        writable = CANNOT_WRITE.format(folder=str(folder))
        assert plain_problem(PermissionError(errno.EACCES, "denied"), folder) == writable
        assert plain_problem(OSError(errno.EROFS, "read-only"), folder) == writable
        assert plain_problem(self.sqlite_error("SQLITE_READONLY"), folder) == writable
        assert plain_problem(self.sqlite_error("SQLITE_CANTOPEN"), folder) == writable
        assert plain_problem(OSError(errno.ENOSPC, "full"), folder) == DISK_FULL
        assert plain_problem(self.sqlite_error("SQLITE_FULL"), folder) == DISK_FULL
        assert plain_problem(self.sqlite_error("SQLITE_BUSY"), folder) == IN_USE
        assert plain_problem(self.sqlite_error("SQLITE_IOERR"), folder) == CANNOT_OPEN
        assert plain_problem(RuntimeError("?"), folder) == CANNOT_OPEN

    def test_none_says_the_error_or_sign_in(self):
        for text in (CANNOT_WRITE, DISK_FULL, IN_USE, DAMAGED_ELSEWHERE, DAMAGED_HELD,
                     CANNOT_OPEN):
            assert "Nostr > Imports" in text
            assert "sign in" not in text and "{reason}" not in text
            # CP3 review L6: one word per concept, and no promise about
            # relays a change still waiting here would break.
            assert "relay" not in text.lower()


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
                           ("Connect Signer App\u2026", "connect", alerts.NORMAL),
                           ("Cancel", None, alerts.CANCEL)]
        assert ran == ([answer] if answer else [])

    def test_a_problem_is_said(self, monkeypatch):
        tried = []
        imports = SimpleNamespace(bound=False, problem="It cannot be kept.", open_window=None,
                                  retry=lambda: tried.append(1) or False)
        (told,), asked, _ran = self.run(imports, monkeypatch)
        assert asked == [] and tried == [1]
        assert (told["title"], told["message"]) == ("Imports aren't available",
                                                    "It cannot be kept.")

    def test_after_a_problem_it_tries_again_first(self, monkeypatch):
        opened = []
        imports = SimpleNamespace(bound=False, problem="It cannot be kept.",
                                  open_window=lambda: opened.append(1))

        def retry():
            imports.bound, imports.problem = True, ""
            return True
        imports.retry = retry
        assert self.run(imports, monkeypatch) == ([], [], [])
        assert opened == [1]


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


def _damaged_pages(path) -> None:
    """A real inbox with posts and an import, its first page (the header)
    kept and every later page scribbled: SQLite's usual damage after a
    crash during a write, or a copy a sync tool took mid-write."""
    from nostr.imports.inbox_store import InboxStore, JobRow
    from tests.imports_fakes import make_item
    store = InboxStore(path)
    store.sync_sources([("k", "https://blog.example/feed", "Blog", True)])
    store.ingest("k", [make_item(f"Post {n}", guid=f"g{n}", content_html="<p>" + "x" * 2000
                                 + "</p>") for n in range(60)])
    store.create_job(label="An import", source_type="inbox",
                     rows=[JobRow(index=0, d_tag="rss-1", title="Post")])
    store.close()
    data = bytearray(path.read_bytes())
    assert len(data) > 3 * 4096
    for at in range(4096, len(data), 7):
        data[at] = 0xA5
    path.write_bytes(bytes(data))


class _WindowsRename:
    """Windows' rule on any system: a file still open cannot be renamed
    ([WinError 32] on the Windows runner, CP2 review N1)."""

    def __init__(self, monkeypatch):
        import pathlib
        import sqlite3
        from nostr.imports import inbox_store
        self.open = []
        tracker = self

        class Tracked(sqlite3.Connection):
            def __init__(self, database, *args, **kwargs):
                super().__init__(database, *args, **kwargs)
                self.database = os.path.realpath(database)
                tracker.open.append(self)

            def close(self):
                if self in tracker.open:
                    tracker.open.remove(self)
                super().close()

        real_connect = sqlite3.connect
        monkeypatch.setattr(inbox_store.sqlite3, "connect",
                            lambda database, **kw: real_connect(database, factory=Tracked, **kw))
        real_replace = pathlib.Path.replace

        def replace(path, target):
            if any(c.database == os.path.realpath(str(path)) for c in tracker.open):
                raise PermissionError(13, "The process cannot access the file because it is "
                                          "being used by another process", str(path))
            return real_replace(path, target)

        monkeypatch.setattr(pathlib.Path, "replace", replace)


def test_damaged_pages_are_set_aside_and_never_stop_the_start(harness, tmp_path, monkeypatch):
    """CP2 review N1: only the open was guarded; a file whose pages were
    damaged opened, then raised out of the next step, out of
    MainWindow.__init__ with it. On Windows the damaged file was renamed
    while still open, refused, and imports stayed off with wrong advice."""
    _WindowsRename(monkeypatch)
    path = database_path(tmp_path, PK)
    path.parent.mkdir(parents=True)
    _damaged_pages(path)
    harness.controller.account_changed(profile())       # does not raise
    settle()
    controller = harness.controller
    assert controller.bound and not controller.problem
    assert controller.notice == SET_ASIDE
    aside = [p.name for p in path.parent.iterdir() if ".damaged-" in p.name]
    assert len(aside) == 1
    assert controller.counts().inbox == 0           # a new inbox
    controller.subscriptions.add_feed("https://blog.example/feed", "Blog")
    assert [s.url for s in controller.sources()] == ["https://blog.example/feed"]


def test_a_file_that_is_not_a_database_is_set_aside_on_windows_too(harness, tmp_path,
                                                                    monkeypatch):
    _WindowsRename(monkeypatch)
    path = database_path(tmp_path, PK)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"this is not a database at all" * 100)
    harness.controller.account_changed(profile())
    settle()
    assert harness.controller.bound and harness.controller.notice == SET_ASIDE


def test_imports_never_stop_the_editor(monkeypatch, caplog):
    """The last guard: whatever goes wrong in imports, the account still
    changes and the editor still opens."""
    import logging

    class Imports:
        def account_changed(self, profile):
            raise RuntimeError("something nobody foresaw")

    host = SimpleNamespace(
        nostr_state=SimpleNamespace(refresh=lambda: None, active=True),
        _imports=Imports(),
        _profile_store=SimpleNamespace(default=lambda: None),
        _update_nostr_commands=lambda: None)
    with caplog.at_level(logging.ERROR):
        MainWindow._update_account_actions(host)        # does not raise
    assert "imports could not follow the account" in caplog.text
