# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins what surrounds the document: no control twice.

What must hold:

  There is no header row of switches: the theme, line numbers and
  syntax highlighting are in the View menu, undo and redo in the Edit
  menu, and nowhere else. The format toolbar sits directly above the
  text.

  The account chip shows at the end of the tab row only while a Nostr
  account is in use; someone who never chose Nostr sees none.

  The credit lives in the About box.
"""

from tests.app_process import run_window_script

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window

about = []
main_window.ask = lambda parent, **kw: about.append(kw.get("message", "")) or "ok"
w = main_window.MainWindow()
w.show()
app.processEvents()
r = {}
r["header"] = hasattr(w, "header_widget")
column = w.format_toolbar.parentWidget().layout()
order = [column.itemAt(i).widget() for i in range(column.count())]
r["toolbar_first"] = order.index(w.format_toolbar) == 0
r["tabs_after"] = order.index(w.tabs) > order.index(w.format_toolbar)
view = [a.objectName() for a in w.m_view.actions()]
r["view"] = view
edit = [a.objectName() for a in w.m_edit.actions()]
r["edit"] = edit
r["chip_without_account"] = w.profile_chip.isVisible()
w.nostr_state.changed.emit(True)
app.processEvents()
r["chip_with_account"] = w.profile_chip.isVisible()
w._show_about()
r["about"] = about[0] if about else ""
for i in range(w.tabs.count()):
    e = w._editor_from_widget(w.tabs.widget(i))
    if e is not None:
        e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_nothing_around_the_document_repeats_a_menu(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert r["header"] is False
    assert r["toolbar_first"] and r["tabs_after"]
    assert {"view.theme", "view.line_numbers", "view.syntax_highlighting"} <= set(r["view"])
    assert {"edit.undo", "edit.redo"} <= set(r["edit"])
    assert r["chip_without_account"] is False
    assert r["chip_with_account"] is True
    assert "rinbal" in r["about"]
