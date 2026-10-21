# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins which font each document is written in (owner decision Q-K).

What must hold:

  Markdown files, new documents and drafts are set in the system's own
  proportional text font at a reading size; plain text and code keep the
  monospace font.

  Text runs across the whole width (owner decision, 2026-10-21): prose,
  in either font (writing, and plain text too), with a quiet border at
  each side; code, and any other file (a table, a log), right up to the
  document's own margin, as in Xcode. Paper Mode sets prose on a page of
  WRITING_MEASURE characters of its font, and code on its wider page.
  While line numbers are shown the text starts beside them.

  View > Use Monospace Font for Writing sets all writing in monospace,
  keeps its border, and is remembered.

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
import editor, main_window, settings

home = os.environ["HOME"]
md = os.path.join(home, "notes.md")
txt = os.path.join(home, "notes.txt")
py = os.path.join(home, "script.py")
# A log: text kept as it is (a CSV table opens in a sheet tab now).
log = os.path.join(home, "events.log")
with open(md, "w", encoding="utf-8") as f:
    f.write("# Notes\n\nSome words.\n")
with open(txt, "w", encoding="utf-8") as f:
    f.write("plain text\n")
with open(py, "w", encoding="utf-8") as f:
    f.write("print('code')\n")
with open(log, "w", encoding="utf-8") as f:
    f.write("12:00 start\n")

w = main_window.MainWindow()
w.resize(1400, 800)
w.show()
app.processEvents()

def fixed(ed):
    return QFontInfo(ed.font()).fixedPitch()

def sides(ed):
    m = ed.viewportMargins()
    return [m.left(), m.right()]

r = {}
r["inset"] = editor.PROSE_INSET
new = w.new_tab()
r["new"] = fixed(new)
r["new_margin"] = new.viewportMargins().left()
r["new_sides"] = sides(new)
r["doc_margin"] = new.document().documentMargin()
md_ed = w.open_path(md)
r["md"] = fixed(md_ed)
txt_ed = w.open_path(txt)
r["txt"] = fixed(txt_ed)
r["txt_margin"] = txt_ed.viewportMargins().left()
r["md_sides"] = sides(md_ed)
py_ed = w.open_path(py)
r["py"] = fixed(py_ed)
r["py_sides"] = sides(py_ed)
log_ed = w.open_path(log)
r["log_sides"] = sides(log_ed)
# The border does not grow with the window: the line does.
w.resize(1800, 800)
app.processEvents()
r["wider_sides"] = sides(md_ed)
w.resize(1400, 800)
app.processEvents()
# Paper Mode: prose on a page of WRITING_MEASURE characters of its font
# (averageCharWidth, as the readable rule measures), code on its page of
# 88 measured with "0".
w._toggle_paper_mode(True)
app.processEvents()
def page(ed):
    return ed.viewport().width() - 2 * ed.document().documentMargin()
mm = md_ed.fontMetrics()
r["md_page"] = [page(md_ed), mm.averageCharWidth() * editor.WRITING_MEASURE]
pm = py_ed.fontMetrics()
r["py_page"] = [page(py_ed), pm.horizontalAdvance("0") * editor.CODE_PAGE_MEASURE]
r["paper_centred"] = [sides(md_ed)[0] > r["inset"], abs(sides(md_ed)[0] - sides(md_ed)[1]) <= 1]
# Laid out to its page: its lines wrap inside it, none cut off.
r["fits"] = [e.document().textWidth() <= e.viewport().width() for e in (md_ed, txt_ed)]
w._toggle_paper_mode(False)
app.processEvents()
r["paper_off_sides"] = sides(md_ed)
r["fits_full"] = [e.document().textWidth() <= e.viewport().width() for e in (md_ed, txt_ed)]
w.tabs.setCurrentIndex(w._editor_widget_index(txt_ed))
w._toggle_line_numbers()                     # on: the column beside them
m = txt_ed.viewportMargins()
r["numbered"] = [m.left(), m.right()]
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
for e in (new, md_ed, txt_ed, py_ed, log_ed):
    e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_each_document_gets_its_font(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert r["new"] is False and r["md"] is False          # writing: proportional
    # Prose: the whole width, a quiet border at each side; the border and
    # the document's margin together are PROSE_INSET from the edge.
    border = r["inset"] - r["doc_margin"]
    assert border > 0
    assert r["new_sides"] == [border, border] and r["md_sides"] == [border, border]
    assert r["wider_sides"] == [border, border]             # the line grows, not the border
    assert r["txt"] is True and r["txt_margin"] == border   # plain text: monospace, prose
    assert r["py"] is True and r["py_sides"] == [0, 0]      # code: monospace, full width
    assert r["log_sides"] == [0, 0]                         # a log: full width
    # Paper Mode: prose on a page of 72 characters, code on one of 88.
    assert abs(r["md_page"][0] - r["md_page"][1]) <= 2
    assert abs(r["py_page"][0] - r["py_page"][1]) <= 2
    assert r["paper_centred"] == [True, True]
    assert r["paper_off_sides"] == [border, border]         # back to the whole width
    assert r["numbered"] == [0, border] and r["unnumbered"] is True   # beside the numbers
    assert r["renamed_margin"] == 0                         # renamed to code: full width
    assert r["md_mono"] is True and r["remembered"] is True
    assert r["md_mono_margin"] == border                    # monospace writing keeps its border
    assert r["fits"] == [True, True]                        # wrapped on the page, not cut off
    assert r["fits_full"] == [True, True]                   # and across the width again
    # The text cursor is the one chosen in Settings (Medium unless changed),
    # in writing and in code alike: the font does not change it.
    assert r["caret_width"] == [2, 2, 2] and r["md_mono_caret"] == 2
    assert r["block_writing"] is False and r["block_text"] is False
    assert r["md_back"] is False
