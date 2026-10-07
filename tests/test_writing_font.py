# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins which font each document is written in (owner decision Q-K).

What must hold:

  Markdown files, new documents and drafts are set in the system's own
  proportional text font at a reading size, in a column of a comfortable
  line length; plain text and code keep the monospace font across the
  whole width.

  View > Use Monospace Font for Writing sets all writing in monospace,
  and is remembered.
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
with open(md, "w", encoding="utf-8") as f:
    f.write("# Notes\n\nSome words.\n")
with open(txt, "w", encoding="utf-8") as f:
    f.write("plain text\n")

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
w.act_monospace_writing.trigger()
r["md_mono"] = fixed(md_ed)
r["remembered"] = settings.load_settings().get("monospace_writing")
w.act_monospace_writing.trigger()
r["md_back"] = fixed(md_ed)
for e in (new, md_ed, txt_ed):
    e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_each_document_gets_its_font(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert r["new"] is False and r["md"] is False          # writing: proportional
    assert r["new_margin"] > 0                              # in a readable column
    assert r["txt"] is True and r["txt_margin"] == 0        # plain text: monospace, full width
    assert r["md_mono"] is True and r["remembered"] is True
    assert r["md_back"] is False
