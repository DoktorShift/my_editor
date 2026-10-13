# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins that highlights (the spelling underline, Find's matches) survive a
change to a table's rows and columns.

What must hold:

  With words underlined above a table, adding a column before the
  caret's, or a row above it, and every other table command, repaints the
  text: no crash. (Qt crashed painting a highlight whose cursor had come
  through such a change; found by h_random, seed 29.)
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.app_process import CHILD_EPILOGUE, CHILD_PROLOGUE, REPO, child_env  # noqa: E402

SCRIPT = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtGui import QTextCharFormat, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTextEdit
app = QApplication(sys.argv[:1])
from editor import HtmlEditor
ed = HtmlEditor()
ed.resize(900, 600)
ed.show()
ed.set_structure_check(lambda: True)
QTest.keyClicks(ed, "some wrods here and more wrods")
ed.insert_table(2, 2)
QTest.keyClicks(ed, "abc")

def underline(start, end):
    selection = QTextEdit.ExtraSelection()
    cursor = QTextCursor(ed.document())
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    selection.cursor = cursor
    fmt = QTextCharFormat()
    fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
    selection.format = fmt
    return selection

ed.set_highlights("spelling", [underline(5, 10), underline(25, 30)])
app.processEvents()
ed.table_command(sys.argv[2])
ed.viewport().repaint()
app.processEvents()
print("RESULT", [(s.cursor.selectionStart(), s.cursor.selectionEnd())
                 for s in ed.highlights("spelling")], flush=True)
"""


@pytest.mark.parametrize("command", ["add_column_before", "add_row_above", "add_column_after",
                                     "add_row_below", "delete_column", "delete_row",
                                     "delete_table"])
def test_a_table_command_with_words_underlined_above_does_not_crash(tmp_path, command):
    proc = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", CHILD_PROLOGUE + SCRIPT + CHILD_EPILOGUE,
         REPO, command], env=child_env(tmp_path), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "RESULT [(5, 10), (25, 30)]" in proc.stdout
