# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins that a closed tab lets go of everything: what it set going stops,
and what was waiting to act on it does not.

What must hold:

  Closing a tab closes its spelling (no late result reaches it), lets go
  of its backup's listener, and forgets Find's matches when they were its
  own. Work put off until the event loop is free (Split's width check) is
  tied to the editor, so it is dropped with it.

  And the closed tab goes: a long session that opens and closes many tabs
  does not keep growing. Tabs of every kind (new documents, Markdown,
  HTML, plain text, R Markdown, code, Markdown opened as its text), worked
  in (typing, the format bar, Find, Split, spelling, audio being read,
  Article Details and the Drafts panel), and closed in any order,
  including while a large file is still being read, while spelling is
  being checked and with Find active: each closed tab's editor is deleted
  once the event loop has run, and nothing raises or crashes then or
  after, while the window goes on being used.
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.app_process import CHILD_EPILOGUE, CHILD_PROLOGUE, REPO, child_env  # noqa: E402
from tests.app_process import run_window_script  # noqa: E402

CLOSE_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import shiboken6
app = QApplication(sys.argv[:1])
import main_window
w = main_window.MainWindow()
w.resize(1200, 700)
w.show()
w.act_check_spelling.setChecked(True)
ed = w.new_tab()
ed.setFocus()
QTest.keyClicks(ed, "Some wrods to find")
app.processEvents()
spelling = ed.spelling()
w._show_find(replace=False)
w.findbar.edit.setText("wrods")
app.processEvents()
r = {"spelling_before": spelling is not None, "matches_before": len(w._search_matches)}
backup = ed._backup
ed.document().setModified(False)
w.close_tab(w.tabs.currentIndex())
r["spelling_after"] = [ed.spelling() is None if shiboken6.isValid(ed) else True,
                       spelling is None or not shiboken6.isValid(spelling) or spelling._closed]
r["backup_listener"] = backup._listener is None
r["matches_after"] = [len(w._search_matches), getattr(w, "_matches_of", None) is not ed]

# Split's width check, put off until the loop is free, goes with its editor.
calls = []
w._width_settled = lambda e: calls.append(1)
other = w.new_tab()
w.act_view_split.trigger()
app.processEvents()
w._width_changed(other)
container = w.tabs.currentWidget()
other.document().setModified(False)
w.close_tab(w.tabs.currentIndex())
container.deleteLater()                    # (once closing deletes the tab itself)
app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
app.processEvents()
r["width_calls"] = len(calls)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_a_closed_tab_lets_go_of_what_it_set_going(tmp_path):
    r = run_window_script(CLOSE_SCRIPT, tmp_path)
    assert r["spelling_before"] and r["matches_before"] == 1
    assert r["spelling_after"] == [True, True]
    assert r["backup_listener"]
    assert r["matches_after"] == [0, True]
    assert r["width_calls"] == 0


SCRIPT = r"""
import json, os, random, sys, types
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import shiboken6
app = QApplication(sys.argv[:1])
import main_window
from editor import HtmlEditor
main_window.ask = lambda parent, **kw: False
main_window.inform = lambda parent, **kw: None
main_window.confirm_destructive = lambda parent, **kw: True
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None
home = os.environ["HOME"]
rng = random.Random(int(sys.argv[2]))
w = main_window.MainWindow()
w.resize(1000, 700)
w.show()
w.activateWindow()
w.nostr_state._active = True
w._update_nostr_commands()
w._profile_store.default = lambda: types.SimpleNamespace(
    user_pubkey="ab" * 32, display_name="Me", npub_short=lambda: "npub1me")
w._upload_limit = lambda: 10 ** 9
w._media_store.upload_bytes = lambda body, name, mime_type: None
w.act_check_spelling.setChecked(True)
song = os.path.join(home, "song.mp3")
with open(song, "wb") as f:
    f.write(b"ID3" + b"\0" * (5 * 1024 * 1024))       # read in the background
FILES = {"doc.md": "# Title\n\nSome wrods here.\n", "doc.html": "<p>Some wrods</p>",
         "doc.txt": "Some wrods\n", "doc.Rmd": "Some wrods\n", "code.py": "x = 1\n",
         "raw.md": "<div>raw</div>\n\nSome wrods\n"}
for name, body in FILES.items():
    open(os.path.join(home, name), "w").write(body)

def settle():
    for _ in range(3):
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()

def open_one():
    choice = rng.choice(["new"] + sorted(FILES))
    if choice == "new":
        ed = w.new_tab()
    else:
        # A file open already comes forward instead: a copy of its own.
        path = os.path.join(home, "%d-%s" % (rng.randrange(10 ** 6), choice))
        open(path, "w").write(FILES[choice])
        w.open_path(path)
        ed = w.current_editor()
    ed.setFocus()
    return ed

def work(ed):
    QTest.keyClicks(ed, " more wrods to check")
    for step in rng.sample(["bar", "find", "split", "audio", "details", "drafts"], 3):
        if step == "bar":
            point = ed.cursorRect().center() - QPoint(20, 0)
            for send in (QTest.mouseClick, QTest.mouseDClick, QTest.mouseRelease):
                send(ed.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     point)
        elif step == "find":
            w._show_find(replace=False)
            w.findbar.edit.setText("wrods")
            ed.setFocus()
        elif step == "split":
            w.act_view_split.trigger()
        elif step == "audio":
            w._upload_into(ed, song)
        elif step == "details":
            w.act_article_details.setChecked(not w.act_article_details.isChecked())
        elif step == "drafts":
            w.act_nostr_drafts.trigger()
    app.processEvents()

closed = []
for _ in range(int(sys.argv[3])):
    if w.tabs.count() < 2 or rng.random() < 0.6:
        work(open_one())
    else:
        index = rng.randrange(w.tabs.count())
        ed = w._editor_from_widget(w.tabs.widget(index))
        if ed is not None:
            ed.document().setModified(False)
            closed.append(ed)
        w.close_tab(index)
        if rng.random() < 0.5:
            settle()                     # else: the next step before the loop runs
settle()
# The window goes on: typing, Bold, the Undo list, Find, a new tab.
ed = w.current_editor() or w.new_tab()
ed.setFocus()
QTest.keyClicks(ed, "after")
w.act_bold.trigger()
w.format_toolbar.undo_menu.aboutToShow.emit()
w._show_find(replace=False)
w.findbar.edit.setText("after")
w.new_tab()
settle()
open_editors = [w._editor_from_widget(w.tabs.widget(i)) for i in range(w.tabs.count())]
r = {"closed": len(closed),
     "alive": sum(1 for e in closed if shiboken6.isValid(e)),
     "editors": len(w.findChildren(HtmlEditor)),
     "open": len([e for e in open_editors if e is not None])}
for e in open_editors:
    if e is not None:
        e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r), flush=True)
"""


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_closed_tabs_go_and_nothing_breaks(tmp_path, seed):
    proc = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", CHILD_PROLOGUE + SCRIPT + CHILD_EPILOGUE,
         REPO, str(seed), "40"],
        env=child_env(tmp_path), capture_output=True, text=True, timeout=300)
    line = next((out for out in proc.stdout.splitlines() if out.startswith("RESULT ")), "")
    assert line, proc.stdout + proc.stderr
    import json
    r = json.loads(line[len("RESULT "):])
    assert r["closed"] > 0
    assert r["alive"] == 0, r
    assert r["editors"] == r["open"], r
    assert "Traceback" not in proc.stderr and "RuntimeError" not in proc.stderr, proc.stderr
    assert proc.returncode == 0, proc.stderr
