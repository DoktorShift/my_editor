# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""An update restart, end to end, with real windows.

One window opens a file, edits it without saving, opens an untitled tab
linked to a Nostr draft, selects some text, and closes for an update. A
second window starts the way the relaunched app does. What must hold:

  Every tab comes back in its order, with its unsaved content still
  marked unsaved, its file, its draft link and its selection, and the
  tab that was active is active again.

  Crash recovery does not open the same work a second time.

  A finished update says so ("You're now using MyEditor ..."); an update
  that did not install says that instead, and the tabs still come back.

  Quitting normally afterwards leaves no record and no backups behind.

Each run happens in a child process with its own HOME, because the app
keeps its settings, session and backups under the home folder, and a test
must never read or touch the real ones.
"""

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from types import SimpleNamespace
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)
import main_window
from constants import APP_VERSION

informed = []
main_window.inform = lambda parent, **kw: informed.append(kw.get("title", ""))

home = os.environ["HOME"]
doc = os.path.join(home, "notes.txt")
with open(doc, "w") as f:
    f.write("line one\nline two\n")

w = main_window.MainWindow()
w.open_path(doc)
notes = w.current_editor()
notes.insertPlainText("UNSAVED ")
w.new_tab()
draft = w.current_editor()
draft.insertPlainText("draft body")
draft._draft_binding = main_window.DraftBinding(
    identifier="abc", inner_kind=30023, title="My Draft", profile_pubkey="f" * 64)
w.tabs.setCurrentIndex(1)
cursor = notes.textCursor()
cursor.setPosition(8)
cursor.setPosition(12, cursor.MoveMode.KeepAnchor)
notes.setTextCursor(cursor)

target = APP_VERSION if sys.argv[2] == "same" else "999.0"
w._pending_release = SimpleNamespace(
    version=target, notes="# MyEditor\n\n- New thing",
    page_url="https://github.com/rinbal/my_editor/releases/tag/v" + target)
prepared = w._prepare_workspace_for_update()
w._closing_for_update = True
w.close()

w2 = main_window.MainWindow()
app.processEvents()
tabs = []
for i in range(w2.tabs.count()):
    e = w2._editor_from_widget(w2.tabs.widget(i))
    binding = getattr(e, "_draft_binding", None)
    tabs.append({
        "title": w2.tabs.tabText(i),
        "text": e.toPlainText(),
        "modified": e.document().isModified(),
        "path": getattr(e, "_file_path", None),
        "draft": binding.identifier if binding else None,
        "selection": [e.textCursor().anchor(), e.textCursor().position()],
    })
result = {
    "prepared": prepared,
    "tabs": tabs,
    "active": w2.tabs.currentIndex(),
    "bar": "" if w2.update_bar.isHidden() else w2.update_bar._text.text(),
    "informed": informed,
    "record_left": os.path.exists(os.path.join(home, ".cache", "my_editor", "workspace.json")),
    "version": APP_VERSION,
    "doc": doc,
}
for i in range(w2.tabs.count()):
    w2._editor_from_widget(w2.tabs.widget(i)).document().setModified(False)
w2.close()
backups = os.path.join(home, ".cache", "my_editor", "backups")
result["backups_after_quit"] = os.listdir(backups) if os.path.isdir(backups) else []
result["record_after_quit"] = os.path.exists(
    os.path.join(home, ".cache", "my_editor", "workspace.json"))
print("RESULT " + json.dumps(result))
"""


def restart(tmp_path, target: str) -> dict:
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT, REPO, target],
        env=env, capture_output=True, text=True, timeout=120,
    )
    line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT ")), None)
    assert line, f"child failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(line[len("RESULT "):])


def test_every_tab_comes_back_after_an_update(tmp_path):
    r = restart(tmp_path, "same")
    assert r["prepared"] is True
    assert [t["title"] for t in r["tabs"]] == ["Welcome", "notes.txt*", "⚿ My Draft*"]

    notes = r["tabs"][1]
    assert notes["text"].startswith("UNSAVED line one")
    assert notes["modified"] is True
    assert notes["path"] == r["doc"]
    assert notes["selection"] == [8, 12]

    draft = r["tabs"][2]
    assert draft["text"] == "draft body"
    assert draft["draft"] == "abc"
    assert draft["path"] is None

    assert r["active"] == 1
    assert not any("recovered" in t["title"] for t in r["tabs"])
    assert r["bar"] == f"You’re now using MyEditor {r['version']}."
    assert r["informed"] == []
    assert r["record_left"] is False


def test_an_update_that_did_not_install_says_so_and_keeps_the_tabs(tmp_path):
    r = restart(tmp_path, "newer")
    assert r["informed"] == ["The update wasn’t installed"]
    assert r["bar"] == ""
    assert [t["title"] for t in r["tabs"]] == ["Welcome", "notes.txt*", "⚿ My Draft*"]


def test_a_normal_quit_afterwards_leaves_nothing_behind(tmp_path):
    r = restart(tmp_path, "same")
    assert r["backups_after_quit"] == []
    assert r["record_after_quit"] is False
