# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The PDF reader's keys and the window's commands never cancel out.

The PDF reader binds keys of its own (Ctrl+0, Ctrl+1 and Ctrl+2 for
its zoom, Ctrl+C to copy its selection), and the window has commands
on some of the same keys (Heading 1 and 2 are Ctrl+1 and Ctrl+2 on
Windows and Linux, Edit > Copy is Ctrl+C). Two shortcuts on one key are
ambiguous to Qt, and then neither fires. What must hold:

  In a PDF tab, Ctrl+0, Ctrl+1 and Ctrl+2 reach the reader's zoom and
  Ctrl+C copies the reader's selection; no editing command runs (Body is
  Ctrl+0 on Windows and Linux).

  In a text tab, the same keys reach the editing commands.

Run in a child process with its own HOME, as every test that builds the
whole window is, so the real settings are never touched; the window's
keys are those of Windows and Linux there, where the keys collide.
"""

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = r"""
import json, os, sys, types
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import commands
# The keys of Windows and Linux, where Heading 1 is Ctrl+1.
commands.sys = types.SimpleNamespace(platform="linux")
import main_window
from export_pdf import export_pdf

home = os.environ["HOME"]
pdf = os.path.join(home, "sample.pdf")
doc = QTextDocument()
doc.setPlainText("needle in the haystack\n" + "plain body line\n" * 80)
export_pdf(doc, pdf, title="Sample")

w = main_window.MainWindow()
w.resize(1100, 720)
w.show()
w.activateWindow()
QTest.qWaitForWindowExposed(w)
ran = []
for action in (w.commands.action("format.style.h1"), w.commands.action("format.style.h2"),
               w.commands.action("format.style.body")):
    action.triggered.connect(lambda _c=False, n=action.objectName(): ran.append(n))
result = {}

viewer = w.open_path(pdf)
app.processEvents()
view = viewer.view
view.setFocus()
app.processEvents()
result["pdf_focus"] = app.focusWidget() is view
QTest.keyClick(view, Qt.Key.Key_2, Qt.KeyboardModifier.ControlModifier)
result["ctrl2_fit_page"] = view.zoomMode() == QPdfView.ZoomMode.FitInView
QTest.keyClick(view, Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier)
result["ctrl1_actual_size"] = (view.zoomMode() == QPdfView.ZoomMode.Custom
                               and abs(view.zoomFactor() - 1.0) < 1e-6)
QTest.keyClick(view, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
result["ctrl0_fit_width"] = view.zoomMode() == QPdfView.ZoomMode.FitToWidth
view._selections = [(0, viewer.document.getSelectionAtIndex(0, 0, 6))]
app.clipboard().setText("before")
QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
result["ctrl_c_pdf"] = app.clipboard().text()
result["commands_in_pdf"] = list(ran)

ed = w.new_tab()
app.processEvents()
ed.setFocus()
QTest.keyClicks(ed, "Title")
QTest.keyClick(ed, Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier)
result["ctrl1_heading"] = ed.textCursor().block().blockFormat().headingLevel()
QTest.keyClick(ed, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
result["ctrl0_body"] = ed.textCursor().block().blockFormat().headingLevel()
ed.selectAll()
QTest.keyClick(ed, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
result["ctrl_c_text"] = app.clipboard().text()
result["commands_in_text"] = list(ran)

for i in range(w.tabs.count()):
    e = w._editor_from_widget(w.tabs.widget(i))
    if e is not None:
        e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(result))
"""


def test_pdf_keys_reach_the_reader_and_text_keys_the_editor(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", SCRIPT, REPO], env=env,
                          capture_output=True, text=True, timeout=120)
    line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT ")), None)
    assert line, f"child failed:\n{proc.stdout}\n{proc.stderr}"
    r = json.loads(line[len("RESULT "):])
    assert r["pdf_focus"]
    assert r["ctrl2_fit_page"] and r["ctrl1_actual_size"] and r["ctrl0_fit_width"]
    assert r["ctrl_c_pdf"] == "needle"
    assert r["commands_in_pdf"] == []
    assert r["ctrl1_heading"] == 1 and r["ctrl0_body"] == 0
    assert r["commands_in_text"] == ["format.style.h1", "format.style.body"]
    assert r["ctrl_c_text"] == "Title"
