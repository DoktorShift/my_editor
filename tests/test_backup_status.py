# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins what the status bar says about backups (decision Q-J).

What must hold:

  Nothing while backups succeed. When the current document's backup
  cannot be written, the status bar says so, with Try Again; when it is
  written again (by Try Again or by the next automatic attempt), the
  message goes. Another tab whose backup works shows nothing. A document
  too large to back up says so, without Try Again, which could not help.
  The message is whole where it fits, and shortens in a narrow window
  instead of widening it.
"""

from tests.app_process import run_window_script  # noqa: E402

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import atomic_file, main_window, recovery
from tests.accessibility import unnamed_controls

w = main_window.MainWindow()
# The status bar's own width is measured: the folder sidebar, whose
# narrowest width the window keeps, is put away.
w._library_window.set_pane_shown("sidebar", False)
# The message in a larger font, so that it is wider than the room the
# narrowest window gives it: in the system's own font it may fit even
# there (on Windows it does).
w.statusBar().setStyleSheet("#BackupProblem { font-size: 24px; }")
w.resize(1100, 700)
w.show()
first = w.new_tab()
first.insertPlainText("first document")
broken = w.new_tab()
broken.insertPlainText("second document")
app.processEvents()


def shown():
    return [w._backup_problem.isVisible(), w._backup_problem.full_text(),
            w._backup_retry.isVisible()]


r = {"quiet": shown()}
real = atomic_file._replace
atomic_file._replace = lambda *_: (_ for _ in ()).throw(OSError(28, "No space left"))
broken._backup.write_now()
app.processEvents()
r["failed"] = shown()
r["whole"] = w._backup_problem.text()
r["names"] = unnamed_controls(w.statusBar())
r["min_width"] = w.minimumSizeHint().width()
w.resize(r["min_width"], 600)
app.processEvents()
from PySide6.QtGui import QFontMetricsF
r["narrow"] = [w._backup_problem.text().endswith("\u2026"),
               w._backup_problem.accessibleName()]
r["narrow_room"] = (QFontMetricsF(w._backup_problem.font()).horizontalAdvance(
    w._backup_problem.full_text()) > w._backup_problem.width())
w.resize(1100, 700)
app.processEvents()
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
    r = run_window_script(SCRIPT, tmp_path)
    assert r["quiet"] == [False, "", False]
    assert r["failed"] == [True, "Couldn't keep a backup of this document.", True]
    assert r["whole"] == "Couldn't keep a backup of this document."   # room: never shortened
    assert r["names"] == []
    assert r["min_width"] < 420                       # the message does not widen it
    assert r["narrow_room"]                           # the message is wider than its room
    assert r["narrow"] == [True, "Couldn't keep a backup of this document."]
    assert r["other_tab"] == [False, "", False]
    assert r["back"] == r["failed"]
    assert r["retried"] == [False, "", False]
    assert r["too_large"] == [True, "This document is too large to keep a backup of.", False]
