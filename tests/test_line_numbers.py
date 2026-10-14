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
