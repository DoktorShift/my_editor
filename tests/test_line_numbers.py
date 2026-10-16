# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins that the line numbers beside a long file start at its first line.

Qt lays a long document out a part at a time after it is opened, and
until it has finished, the top of the screen can map to a paragraph far
down. The gutter paints while that runs and only repaints on a scroll,
an edit or a caret move, so a wrong early paint stayed on screen: a
3,000-line file showed "827, 828, ..." beside its lines 1, 2, ... until
the person scrolled. The screen is read back here, not repainted.
"""

from tests.app_process import run_window_script

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QPoint
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window

code = os.path.join(os.environ["HOME"], "long.py")
with open(code, "w", encoding="utf-8") as f:
    f.write("".join(f"value_{n} = {n}\n" for n in range(3000)))
w = main_window.MainWindow()
w.resize(900, 600)
w.show()
if not w.show_line_numbers:
    w.act_toggle_line_numbers.trigger()
app.processEvents()
ed = w.open_path(code)
QTest.qWait(1500)                  # the layout of the rest runs meanwhile
gutter = ed._line_gutter
corner = gutter.mapTo(w, QPoint(0, 0))
screen = w.screen().grabWindow(w.winId()).copy(
    corner.x(), corner.y(), gutter.width(), gutter.height()
).toImage().convertToFormat(QImage.Format_RGB32)
painted_now = gutter.grab().toImage().convertToFormat(QImage.Format_RGB32)
r = {"first": ed.first_visible_block().blockNumber(),
     "screen_is_current": screen == painted_now}
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_a_long_file_opens_with_its_first_line_numbers_on_screen(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert r["first"] == 0
    assert r["screen_is_current"]


ALIGNED = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QPoint
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window

w = main_window.MainWindow()
w.resize(1000, 700)
w.show()
if not w.show_line_numbers:
    w.act_toggle_line_numbers.trigger()
app.processEvents()


def misplaced(ed):
    # Each number's baseline against its line's, in the gutter's pixels.
    gutter = ed._line_gutter
    found = []
    for number, baseline in gutter.numbers():
        block = ed.document().findBlockByNumber(number - 1)
        line = block.layout().lineAt(0)
        top = ed.cursorRect(QTextCursor(block)).top()
        text = gutter.mapFromGlobal(ed.viewport().mapToGlobal(
            QPoint(0, round(top + line.ascent())))).y()
        if abs(baseline - text) > 1:
            found.append([number, baseline, text])
    return found


r = {}
writing = w.new_tab()                        # as in the report: a new document
writing.setPlainText("\n".join(f"line {n}" for n in range(1, 14)))
app.processEvents()
r["writing"] = misplaced(writing)
r["writing_count"] = len(writing._line_gutter.numbers())

code = os.path.join(os.environ["HOME"], "code.py")
with open(code, "w", encoding="utf-8") as f:
    f.write("".join(f"value_{n} = {n}\n" for n in range(200)))
ed = w.open_path(code)
app.processEvents()
ed.verticalScrollBar().setValue(ed.verticalScrollBar().value() + 37)   # part of a line
app.processEvents()
r["scrolled"] = misplaced(ed)
r["scrolled_first"] = ed._line_gutter.numbers()[0][0]

writing.set_top_inset(48)                    # room for a header above the text
w.tabs.setCurrentIndex(w._editor_widget_index(writing))
app.processEvents()
r["inset"] = misplaced(writing)
for e in (writing, ed):
    e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_each_number_stands_on_its_lines_baseline(tmp_path):
    """Seen in the app: every number stood a line above its line, so "2"
    was beside the first. The gutter guessed where lines are (8 px down,
    in its own font) instead of asking the editor; the document's margin,
    the room above the text and a part-scrolled line all moved the text
    and not the numbers."""
    r = run_window_script(ALIGNED, tmp_path)
    assert r["writing"] == [] and r["writing_count"] == 13
    assert r["scrolled"] == [] and r["scrolled_first"] >= 1
    assert r["inset"] == []
