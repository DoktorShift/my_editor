# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins what a screen reader finds in the document window's own parts.

What must hold:

  The format toolbar, the find bar (with Replace), the tabs, the menu bar
  and the status bar have no control without a name (tests/accessibility.py
  asks Qt's accessibility interfaces, as VoiceOver, NVDA and Orca do).

  The text area is announced by its document's name.

The drafts panel belongs to its own tests.
"""

from tests.app_process import run_window_script

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from tests.accessibility import unnamed_controls

note = os.path.join(os.environ["HOME"], "note.md")
with open(note, "w", encoding="utf-8") as f:
    f.write("Some words.\n")

w = main_window.MainWindow()
w.show()
new = w.new_tab()
app.processEvents()
r = {"new_name": new.accessibleName()}
w._show_find(replace=True)
r["unnamed"] = {part: unnamed_controls(widget) for part, widget in (
    ("toolbar", w.format_toolbar), ("find bar", w.findbar), ("tabs", w.tabs),
    ("menu bar", w.menuBar()), ("status bar", w.statusBar()))}
opened = w.open_path(note)
app.processEvents()
r["file_name"] = opened.accessibleName()
for e in (new, opened):
    e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_every_control_of_the_document_window_has_a_name(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert r["unnamed"] == {"toolbar": [], "find bar": [], "tabs": [], "menu bar": [],
                            "status bar": []}
    assert r["new_name"] == "Untitled"
    assert r["file_name"] == "note.md"
