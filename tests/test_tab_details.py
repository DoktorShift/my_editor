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

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.app_process import run_window_script  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = "---\ntitle: FM Title\nsummary: FM summary\nslug: fm-slug\ntags: [a, b]\n---\n\nBody.\n"


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
r = {}
for case in ("restored", "restored copy", "resumed"):
    open(path, "w", encoding="utf-8").write(sys.argv[4])
    ed = w.open_path(path)
    w._set_article_details(ed, w._article_details_of(ed).with_changes(
        title="Changed Title", hashtags=("a", "b", "c")))
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
    r[case] = open(target, encoding="utf-8").read()
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
                          str(tmp_path / "copy.md"), SOURCE)
    for case in ("restored", "restored copy", "resumed"):
        assert "title: Changed Title\n" in r[case] and "tags: [a, b, c]\n" in r[case], case
        assert "summary: FM summary\n" in r[case] and "slug: fm-slug\n" in r[case], case
        assert r[case].endswith("\nBody.Z\n"), case


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
