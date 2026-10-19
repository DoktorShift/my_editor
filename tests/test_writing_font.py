# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins which font each document is written in (owner decision Q-K).

What must hold:

  Markdown files, new documents and drafts are set in the system's own
  proportional text font at a reading size; plain text and code keep the
  monospace font.

  Prose keeps to a column of a comfortable line length, in either font:
  writing, and plain text too (owner request, 2026-10-19). Code, and any
  other file (a table, a log), runs across the whole width, as in Xcode.
  While line numbers are shown the column starts beside them.

  View > Use Monospace Font for Writing sets all writing in monospace,
  keeps its column, and is remembered.

  Writing has the thin insertion point of a word processor (a block
  would cover the next letter of a proportional font); code and plain
  text keep the block caret.
"""

from tests.app_process import run_window_script

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtGui import QFontInfo
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window, settings

home = os.environ["HOME"]
md = os.path.join(home, "notes.md")
txt = os.path.join(home, "notes.txt")
py = os.path.join(home, "script.py")
csv = os.path.join(home, "table.csv")
with open(md, "w", encoding="utf-8") as f:
    f.write("# Notes\n\nSome words.\n")
with open(txt, "w", encoding="utf-8") as f:
    f.write("plain text\n")
with open(py, "w", encoding="utf-8") as f:
    f.write("print('code')\n")
with open(csv, "w", encoding="utf-8") as f:
    f.write("a,b,c\n")

w = main_window.MainWindow()
w.resize(1400, 800)
w.show()
app.processEvents()

def fixed(ed):
    return QFontInfo(ed.font()).fixedPitch()

r = {}
new = w.new_tab()
r["new"] = fixed(new)
r["new_margin"] = new.viewportMargins().left()
md_ed = w.open_path(md)
r["md"] = fixed(md_ed)
txt_ed = w.open_path(txt)
r["txt"] = fixed(txt_ed)
r["txt_margin"] = txt_ed.viewportMargins().left()
# Laid out to its column: its lines wrap inside it, none cut off.
r["fits"] = [e.document().textWidth() <= e.viewport().width() for e in (md_ed, txt_ed)]
py_ed = w.open_path(py)
r["py"] = fixed(py_ed)
r["py_margin"] = py_ed.viewportMargins().left()
csv_ed = w.open_path(csv)
r["csv_margin"] = csv_ed.viewportMargins().left()
w.tabs.setCurrentIndex(w._editor_widget_index(txt_ed))
w._toggle_line_numbers()                     # on: the column beside them
m = txt_ed.viewportMargins()
r["numbered"] = [m.left(), m.right() > 0]
w._toggle_line_numbers()
r["unnumbered"] = txt_ed.viewportMargins().left() > 0
main_window.QInputDialog.getText = staticmethod(lambda *a, **k: ("notes.py", True))
w._rename_tab(w._editor_widget_index(txt_ed), txt_ed)
r["renamed_margin"] = txt_ed.viewportMargins().left()
for ed in (new, txt_ed):
    w.tabs.setCurrentIndex(w._editor_widget_index(ed))
    ed.setFocus()
    app.processEvents()
r["caret_width"] = [new.cursorWidth(), md_ed.cursorWidth(), txt_ed.cursorWidth()]
w.tabs.setCurrentIndex(w._editor_widget_index(new))
new.setFocus()
app.processEvents()
r["block_writing"] = new.block_caret_shown()
w.tabs.setCurrentIndex(w._editor_widget_index(txt_ed))
txt_ed.setFocus()
app.processEvents()
r["block_text"] = txt_ed.block_caret_shown()
w.act_monospace_writing.trigger()
r["md_mono"] = fixed(md_ed)
r["md_mono_caret"] = md_ed.cursorWidth()
r["md_mono_margin"] = md_ed.viewportMargins().left()
r["remembered"] = settings.load_settings().get("monospace_writing")
w.act_monospace_writing.trigger()
r["md_back"] = fixed(md_ed)
for e in (new, md_ed, txt_ed, py_ed, csv_ed):
    e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_each_document_gets_its_font(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert r["new"] is False and r["md"] is False          # writing: proportional
    assert r["new_margin"] > 0                              # in a readable column
    assert r["txt"] is True and r["txt_margin"] > 0         # plain text: monospace, a column
    assert r["py"] is True and r["py_margin"] == 0          # code: monospace, full width
    assert r["csv_margin"] == 0                             # a table: full width
    assert r["numbered"] == [0, True] and r["unnumbered"] is True   # beside the numbers
    assert r["renamed_margin"] == 0                         # renamed to code: full width
    assert r["md_mono"] is True and r["remembered"] is True
    assert r["md_mono_margin"] > 0                          # monospace writing keeps its column
    assert r["fits"] == [True, True]                        # wrapped in the column, not cut off
    # The text cursor is the one chosen in Settings (Medium unless changed),
    # in writing and in code alike: the font does not change it.
    assert r["caret_width"] == [2, 2, 2] and r["md_mono_caret"] == 2
    assert r["block_writing"] is False and r["block_text"] is False
    assert r["md_back"] is False
