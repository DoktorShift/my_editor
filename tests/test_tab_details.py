# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The article details a tab holds are read and written through one place,
tab_details, which reads a file's details when something first asks for
them. Read anywhere else, they could be read before that, stale: a restored
tab saved without the details changed before the crash, and Save as Draft
offered the file's name for a title (review round 10, B1 and B2)."""

import os
import pathlib
import re
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import front_matter  # noqa: E402
import recovery  # noqa: E402
import tab_details  # noqa: E402
import workspace_restore  # noqa: E402
from nostr.article_details import ArticleDetails, from_front_matter  # noqa: E402
from tests.app_process import run_window_script  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = "---\ntitle: FM Title\nsummary: FM summary\nslug: fm-slug\ntags: [a, b]\n---\n\nBody.\n"
CAPS = SOURCE.replace("tags: [a, b]", "tags: [Python, Rust]")
CASES = ("restored", "restored copy", "resumed")


def test_no_module_but_tab_details_touches_a_tabs_details():
    """The attribute they live in, and the names it had, appear nowhere
    else in the app."""
    raw = re.compile(r"_held_details|_details_unread|\b_article_details\b(?!_)")
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT)
        if relative.parts[0] in ("tests", ".venv", "build", "dist") or path.name == "tab_details.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if raw.search(line):
                found.append(f"{relative}:{number}: {line.strip()}")
    assert found == []


RESTORE_SCRIPT = r"""
import dataclasses, json, os, shutil, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window, workspace_restore
from recovery import read_backup

path, copy = sys.argv[2], sys.argv[3]
w = main_window.MainWindow()
w.show()
CHANGES = {"details": {"title": "Changed Title", "hashtags": ("a", "b", "c")},
           "title": {"title": "Changed Title"}, "read": None}
r = {}
for change, case in [(change, case) for change in sys.argv[5:]
                     for case in ("restored", "restored copy", "resumed")]:
    open(path, "w", encoding="utf-8").write(sys.argv[4])
    ed = w.open_path(path)
    details = w._article_details_of(ed)     # Article Details shown, a preview, a draft
    if CHANGES[change]:
        w._set_article_details(ed, details.with_changes(**CHANGES[change]))
    c = ed.textCursor()
    c.movePosition(QTextCursor.MoveOperation.End)
    c.insertText("Z")
    if case == "resumed":                   # the record of a restart, its backup kept
        tab = workspace_restore.capture_editor_tab(ed)
        shutil.copy(tab.backup_file, tab.backup_file + ".kept")
        tab = dataclasses.replace(tab, backup_file=tab.backup_file + ".kept")
    else:                                   # a crash
        ed._backup.write_now()
        record = read_backup(ed._backup.path)
    ed.document().setModified(False)
    w.close_tab(w.tabs.currentIndex())
    app.processEvents()
    if case == "resumed":
        restored = workspace_restore.resume_tab(w, tab, draft_type=main_window.DraftBinding)
    else:
        restored = w._restore_one_backup(record)
    w.tabs.setCurrentIndex(w.tabs.count() - 1)
    target = copy if case == "restored copy" else path
    w._save_to(target)                      # nothing asked for the details first
    r[change + " " + case] = open(target, encoding="utf-8").read()
    restored.document().setModified(False)
    w.close_tab(w.tabs.currentIndex())
    app.processEvents()
w.close()
print("RESULT " + json.dumps(r))
"""


def test_a_restored_tab_saves_the_details_changed_before_the_crash(tmp_path):
    """B1: the file kept its old title, the save said Saved, and the tab
    went on publishing the new one. The same after a restart."""
    r = run_window_script(RESTORE_SCRIPT, tmp_path, str(tmp_path / "post.md"),
                          str(tmp_path / "copy.md"), SOURCE, "details")
    for case in CASES:
        saved = r["details " + case]
        assert "title: Changed Title\n" in saved and "tags: [a, b, c]\n" in saved, case
        assert "summary: FM summary\n" in saved and "slug: fm-slug\n" in saved, case
        assert saved.endswith("\nBody.Z\n"), case


def test_a_restored_tab_writes_only_the_details_changed_in_it(tmp_path):
    """Round 11, H2: back from a crash or a restart, a tab whose details
    were only read wrote its file's hashtags in lower case and in quotes;
    one whose title was changed, the title and those hashtags."""
    r = run_window_script(RESTORE_SCRIPT, tmp_path, str(tmp_path / "post.md"),
                          str(tmp_path / "copy.md"), CAPS, "read", "title")
    typed = CAPS.replace("Body.", "Body.Z")
    for case in CASES:
        assert r["read " + case] == typed, case
        assert r["title " + case] == typed.replace("FM Title", "Changed Title"), case


RESUME_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window, workspace_restore

path, before, after = sys.argv[2:5]
w = main_window.MainWindow()
w.show()
r = {}
for case in ("unread", "read"):
    open(path, "w", encoding="utf-8").write(before)
    ed = w.open_path(path)
    if case == "read":
        w._article_details_of(ed)
    tab = workspace_restore.capture_editor_tab(ed)     # unmodified: no backup
    w.close_tab(w.tabs.currentIndex())
    app.processEvents()
    open(path, "w", encoding="utf-8").write(after)     # another app, MyEditor closed
    ed = workspace_restore.resume_tab(w, tab, draft_type=main_window.DraftBinding)
    r[case + " title"] = w._article_details_of(ed).title
    c = ed.textCursor()
    c.movePosition(QTextCursor.MoveOperation.End)
    c.insertText(" typed")
    w._save_to(path)
    r[case] = open(path, encoding="utf-8").read()
    ed.document().setModified(False)
    w.close_tab(w.tabs.currentIndex())
    app.processEvents()
w.close()
print("RESULT " + json.dumps(r))
"""


def test_a_tab_unmodified_at_a_restart_keeps_its_file_as_another_app_left_it(tmp_path):
    """Round 11, H3: it came back with the front matter and details it had
    at the restart, so its next save wrote back the title, hashtags and
    summary another app had changed meanwhile, and it published the old
    title. Only a tab with unsaved changes keeps what it had (its backup)."""
    after = "---\ntitle: New Title\ntags: [a, b]\nsummary: Added outside\n---\n\nNew body.\n"
    r = run_window_script(RESUME_SCRIPT, tmp_path, str(tmp_path / "post.md"),
                          "---\ntitle: Old Title\ntags: [a]\n---\n\nOld body.\n", after)
    for case in ("unread", "read"):
        assert r[case + " title"] == "New Title", case
        assert r[case] == after.replace("New body.", "New body. typed"), case


def test_a_record_keeps_the_details_set_in_the_tab_and_none_only_read():
    ed = SimpleNamespace(_front_matter=front_matter.split(CAPS)[0])
    tab_details.hold(ed, None)                         # a file opened
    tab_details.details_of(ed)                         # its details read
    assert "details" not in recovery.tab_identity(ed)
    tab_details.hold(ed, tab_details.details_of(ed).with_changes(title="Set"))
    tab_details.details_of(ed)
    tab_details.file_changed(ed)                       # saved, still set in the tab
    assert ArticleDetails.from_tags(recovery.tab_identity(ed)["details"]).title == "Set"


def test_restored_details_the_file_holds_too_stay_as_the_file_writes_them():
    """A record keeps hashtags in lower case (ArticleDetails.tags): those
    the file holds come back in its case, and details all the file's are
    the file's, read when asked for, as on opening it."""
    front = front_matter.split(CAPS)[0]
    read = from_front_matter(front)
    for details in (read, read.with_changes(title="Set")):
        ed = SimpleNamespace()
        workspace_restore.restore_identity(ed, None, None, "", details.tags(),
                                           front_matter.join(front, ""), draft_type=dict)
        assert tab_details.untouched(ed) == (details == read)
        assert tab_details.details_of(ed) == details
        assert tab_details.details_of(ed).hashtags == ("Python", "Rust")


STASH_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window

w = main_window.MainWindow()
w.show()
ed = w.open_path(sys.argv[2])
r = {"suggested": list(w._stash_defaults_for(ed, None))}
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_save_as_draft_first_suggests_the_files_details(tmp_path):
    """B2: right after opening, it suggested the file's name for the title
    and the slug, and no summary, and the next save wrote them."""
    path = tmp_path / "plain.md"
    path.write_text(SOURCE, encoding="utf-8")
    r = run_window_script(STASH_SCRIPT, tmp_path, str(path))
    assert r["suggested"][:3] == ["FM Title", "fm-slug", "FM summary"]
