# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins that a closed tab lets go of everything: what it set going stops,
and what was waiting to act on it does not.

What must hold:

  Closing a tab closes its spelling (no late result reaches it), lets go
  of its backup's listener, and forgets Find's matches when they were its
  own. Work put off until the event loop is free (Split's width check) is
  tied to the editor, so it is dropped with it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.app_process import run_window_script  # noqa: E402

CLOSE_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import shiboken6
app = QApplication(sys.argv[:1])
import main_window
w = main_window.MainWindow()
w.resize(1200, 700)
w.show()
w.act_check_spelling.setChecked(True)
ed = w.new_tab()
ed.setFocus()
QTest.keyClicks(ed, "Some wrods to find")
app.processEvents()
spelling = ed.spelling()
w._show_find(replace=False)
w.findbar.edit.setText("wrods")
app.processEvents()
r = {"spelling_before": spelling is not None, "matches_before": len(w._search_matches)}
backup = ed._backup
ed.document().setModified(False)
w.close_tab(w.tabs.currentIndex())
r["spelling_after"] = [ed.spelling() is None if shiboken6.isValid(ed) else True,
                       spelling is None or not shiboken6.isValid(spelling) or spelling._closed]
r["backup_listener"] = backup._listener is None
r["matches_after"] = [len(w._search_matches), getattr(w, "_matches_of", None) is not ed]

# Split's width check, put off until the loop is free, goes with its editor.
calls = []
w._width_settled = lambda e: calls.append(1)
other = w.new_tab()
w.act_view_split.trigger()
app.processEvents()
w._width_changed(other)
container = w.tabs.currentWidget()
other.document().setModified(False)
w.close_tab(w.tabs.currentIndex())
container.deleteLater()                    # (once closing deletes the tab itself)
app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
app.processEvents()
r["width_calls"] = len(calls)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_a_closed_tab_lets_go_of_what_it_set_going(tmp_path):
    r = run_window_script(CLOSE_SCRIPT, tmp_path)
    assert r["spelling_before"] and r["matches_before"] == 1
    assert r["spelling_after"] == [True, True]
    assert r["backup_listener"]
    assert r["matches_after"] == [0, True]
    assert r["width_calls"] == 0
