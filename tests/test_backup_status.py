# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins what the status bar says about backups (decision Q-J).

What must hold:

  Nothing while backups succeed. When the current document's backup
  cannot be written, the status bar says so, with Try Again; when it is
  written again (by Try Again or by the next automatic attempt), the
  message goes. Another tab whose backup works shows nothing. A document
  too large to back up says so, without Try Again, which could not help.
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
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import atomic_file, main_window, recovery
from tests.accessibility import unnamed_controls

w = main_window.MainWindow()
w.show()
first = w.new_tab()
first.insertPlainText("first document")
broken = w.new_tab()
broken.insertPlainText("second document")
app.processEvents()


def shown():
    return [w._backup_problem.isVisible(), w._backup_problem.text(), w._backup_retry.isVisible()]


r = {"quiet": shown()}
real = atomic_file._replace
atomic_file._replace = lambda *_: (_ for _ in ()).throw(OSError(28, "No space left"))
broken._backup.write_now()
app.processEvents()
r["failed"] = shown()
r["names"] = unnamed_controls(w.statusBar())
w.tabs.setCurrentIndex(w._editor_widget_index(first))
app.processEvents()
r["other_tab"] = shown()
w.tabs.setCurrentIndex(w._editor_widget_index(broken))
app.processEvents()
r["back"] = shown()
atomic_file._replace = real
w._backup_retry.click()
app.processEvents()
r["retried"] = shown()
recovery.MAX_BACKUP_BYTES = 10
broken.insertPlainText(" grows")
broken._backup.write_now()
app.processEvents()
r["too_large"] = shown()
for e in (first, broken):
    e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_the_status_bar_speaks_only_when_a_backup_fails(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", SCRIPT, REPO], env=env,
                          capture_output=True, text=True, timeout=120)
    line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT ")), None)
    assert line, f"child failed:\n{proc.stdout}\n{proc.stderr}"
    r = json.loads(line[len("RESULT "):])
    assert r["quiet"] == [False, "", False]
    assert r["failed"] == [True, "Couldn't keep a backup of this document.", True]
    assert r["names"] == []
    assert r["other_tab"] == [False, "", False]
    assert r["back"] == r["failed"]
    assert r["retried"] == [False, "", False]
    assert r["too_large"] == [True, "This document is too large to keep a backup of.", False]
