# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins that the Edit menu's commands act on, and show the state of,
whatever has the focus.

What must hold:

  Cut, Copy and Delete are dimmed without a selection (the HIG dims a
  command that cannot act) and live with one, in the document and in the
  find field alike.

  Edit > Undo and Redo, from the menu, undo the find field's typing while
  it has the focus, never the document's.

  A right-click leaves no menu behind.
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
from PySide6.QtCore import QEvent, QPoint
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QMenu
app = QApplication(sys.argv[:1])
import main_window

w = main_window.MainWindow()
w.show()
ed = w.new_tab()
ed.setFocus()
app.processEvents()
r = {"empty": [w.act_cut.isEnabled(), w.act_copy.isEnabled(), w.act_delete.isEnabled()]}
ed.insertPlainText("document words")
ed.selectAll()
app.processEvents()
r["selected"] = [w.act_cut.isEnabled(), w.act_copy.isEnabled(), w.act_delete.isEnabled()]

w._show_find()
app.processEvents()
field = w.findbar.edit
field.setFocus()
app.processEvents()
r["field_empty"] = [w.act_cut.isEnabled(), w.act_copy.isEnabled()]
field.insert("abc")
field.selectAll()
app.processEvents()
r["field_selected"] = [w.act_cut.isEnabled(), w.act_copy.isEnabled()]
field.setCursorPosition(3)
field.insert("d")
w._undo()
r["field_after_undo"] = field.text()
r["document_after_undo"] = ed.toPlainText()

# A right-click leaves no menu behind.
from PySide6.QtCore import QTimer


def close_menu():
    popup = QApplication.activePopupWidget()
    if popup is not None:
        popup.close()


before = len(ed.findChildren(QMenu))
for _ in range(5):
    QTimer.singleShot(0, close_menu)
    ed.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5),
                                          ed.mapToGlobal(QPoint(5, 5))))
app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
r["menus_left"] = len(ed.findChildren(QMenu)) - before
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_edit_commands_follow_the_focus_and_the_selection(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", SCRIPT, REPO], env=env,
                          capture_output=True, text=True, timeout=120)
    line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT ")), None)
    assert line, f"child failed:\n{proc.stdout}\n{proc.stderr}"
    r = json.loads(line[len("RESULT "):])
    assert r["empty"] == [False, False, False]          # review L3 (early range)
    assert r["selected"] == [True, True, True]
    assert r["field_empty"] == [False, False]
    assert r["field_selected"] == [True, True]
    assert r["field_after_undo"] == "abc"                # review L4 (early range)
    assert r["document_after_undo"] == "document words"
    assert r["menus_left"] == 0                          # review L2 (early range)
