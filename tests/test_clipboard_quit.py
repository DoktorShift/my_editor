# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins that Copy and Cut leave MyEditor able to quit cleanly (review H2).

The clipboard keeps what was copied after the app quits, as every Mac and
Windows app's does, so it must not be an object Python owns: Qt destroys
the clipboard's data after the Python interpreter has gone, and data
made in Python then ends the process with a crash. Copy builds it with
Qt's own (HtmlEditor.createMimeDataFromSelection), and the formats are
set on that.
"""

import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
w = main_window.MainWindow()
w.show()

def go():
    ed = w.new_tab()
    ed.setFocus()
    QTest.keyClicks(ed, "some **words** here")
    cursor = ed.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    ed.setTextCursor(cursor)
    getattr(ed, sys.argv[2])()
    print("clipboard:", QApplication.clipboard().text(), flush=True)
    ed.document().setModified(False)
    QTimer.singleShot(50, w.close)
    QTimer.singleShot(200, app.quit)

QTimer.singleShot(100, go)
sys.exit(app.exec())
"""


@pytest.mark.parametrize("command", ["copy", "cut"])
def test_quitting_after_copy_or_cut_ends_cleanly(tmp_path, command):
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               QT_QPA_PLATFORM="offscreen")
    result = subprocess.run([sys.executable, "-c", SCRIPT, REPO, command], env=env,
                            capture_output=True, text=True, timeout=60)
    assert "clipboard: some **words** here" in result.stdout or \
        "clipboard: some words here" in result.stdout, result.stdout + result.stderr
    assert result.returncode == 0, result.stderr[-2000:]
