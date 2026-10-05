# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the window side of the update restart (workspace_restore.py).

What must hold:

  A tab is written down with what brings it back: its file, its cursor and
  selection, its Nostr draft link, and, for unsaved or untitled content,
  the crash-recovery backup written at that moment.

  Content that is in no file and no backup is reported, never silently
  left out.

  After a launch, the person hears which version they are on now, or that
  the update did not install. A launch on the same version says nothing.

The whole restart, with real windows, is pinned in test_update_restart.py.
"""

import os
import sys
from dataclasses import dataclass

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import recovery  # noqa: E402
import workspace_restore  # noqa: E402
from editor import HtmlEditor  # noqa: E402
from workspace import DOCUMENT, Workspace  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(autouse=True)
def backup_dir(tmp_path, monkeypatch):
    """Never touch the real ~/.cache while testing."""
    target = tmp_path / "backups"
    monkeypatch.setattr(recovery, "BACKUP_DIR", str(target))
    return target


def editor(text="", path=None, modified=False):
    ed = HtmlEditor()
    ed.setPlainText(text)
    ed._file_path = path
    ed._backup = recovery.EditorBackup(ed, path)
    ed.document().setModified(modified)
    return ed


@dataclass
class Binding:
    identifier: str
    title: str = ""


# -- writing a tab down ---------------------------------------------------------

def test_an_unsaved_untitled_tab_points_at_a_backup_written_now():
    ed = editor("draft body", modified=True)
    cursor = ed.textCursor()
    cursor.setPosition(2)
    cursor.setPosition(6, cursor.MoveMode.KeepAnchor)
    ed.setTextCursor(cursor)

    tab = workspace_restore.capture_editor_tab(ed)

    assert tab.kind == DOCUMENT and tab.path is None
    assert tab.backup_file == os.path.abspath(ed._backup.path)
    assert os.path.exists(tab.backup_file)
    assert tab.modified is True
    assert (tab.anchor, tab.cursor) == (2, 6)


def test_a_saved_file_needs_no_backup(tmp_path):
    path = str(tmp_path / "notes.txt")
    tab = workspace_restore.capture_editor_tab(editor("saved", path=path))
    assert (tab.path, tab.backup_file, tab.modified) == (path, None, False)


def test_unsaved_content_with_nowhere_to_go_is_reported(monkeypatch):
    monkeypatch.setattr(recovery, "MAX_BACKUP_BYTES", 10)   # the backup can't be written
    ed = editor("a document far too large for its backup", modified=True)
    assert workspace_restore.capture_editor_tab(ed) is None


def test_an_empty_untitled_tab_is_kept_without_a_backup():
    tab = workspace_restore.capture_editor_tab(editor())
    assert tab is not None and tab.backup_file is None


def test_the_draft_link_is_written_down():
    ed = editor("body", modified=True)
    ed._draft_binding = Binding(identifier="abc", title="My Draft")
    tab = workspace_restore.capture_editor_tab(ed)
    assert tab.draft == {"identifier": "abc", "title": "My Draft"}


def test_a_draft_link_is_rebuilt_from_the_fields_it_knows():
    rebuild = workspace_restore._dataclass_from
    assert rebuild(Binding, {"identifier": "abc", "unknown": 1}) == Binding("abc")
    assert rebuild(Binding, {"title": "no identifier"}) is None


# -- what a launch says ------------------------------------------------------------

PAGE = "https://github.com/rinbal/my_editor/releases/tag/v3.4"


def restart(to_version, notes="- New", url="https://example.org/notes"):
    return Workspace(tabs=(), from_version="3.3", to_version=to_version,
                     release_notes=notes, release_url=url)


def test_a_restart_on_the_version_it_meant_to_reach_is_an_update():
    news = workspace_restore.version_news(restart("3.4"), "3.3", "3.4", release_page=PAGE)
    assert news == workspace_restore.VersionNews(
        updated=True, version="3.4", notes="- New", release_url="https://example.org/notes")


def test_a_restart_on_an_older_version_did_not_install():
    news = workspace_restore.version_news(restart("3.5"), "3.4", "3.4", release_page=PAGE)
    assert news == workspace_restore.VersionNews(updated=False, version="3.5")


def test_a_launch_on_a_newer_version_than_last_time_was_updated_by_hand():
    news = workspace_restore.version_news(None, "3.3", "3.4", release_page=PAGE)
    assert news == workspace_restore.VersionNews(updated=True, version="3.4", release_url=PAGE)


@pytest.mark.parametrize("last_run", ["3.4", "3.5", None, 34, "not a version"])
def test_any_other_launch_says_nothing(last_run):
    assert workspace_restore.version_news(None, last_run, "3.4", release_page=PAGE) is None
