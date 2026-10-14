# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins Continue Editing (a draft's preview, Return, a double click).

What must hold: the draft opens ready to type in, with the keyboard in
its text and the caret at the end, where writing goes on; asked again,
its tab comes to the front, with no second copy and the caret where it
was left.
"""

from tests.app_process import run_window_script

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from nostr.drafts import DraftWrapMeta
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None

w = main_window.MainWindow()
w.show()
w.activateWindow()
store = w._draft_store
store.bind_profile("a" * 64)
store.upsert_skeleton(DraftWrapMeta(identifier="d1", inner_kind=1, event_id="e1",
                                    pubkey="a" * 64, created_at=1700000000, expiration=None,
                                    ciphertext="c"))
store.set_decrypted("d1", inner={"kind": 1, "content": "First line\nThe end", "tags": []})
app.processEvents()
tabs = w.tabs.count()
w._on_panel_open_draft("d1")
app.processEvents()
ed = w.current_editor()
r = {"new_tab": w.tabs.count() == tabs + 1,
     "at_end": ed.textCursor().atEnd(),
     "focused": ed.hasFocus()}
cursor = ed.textCursor()
cursor.setPosition(3)
ed.setTextCursor(cursor)
w.tabs.setCurrentIndex(0)
w._on_panel_open_draft("d1")
app.processEvents()
r["no_copy"] = w.tabs.count() == tabs + 1
r["same_tab"] = w.current_editor() is ed
r["kept_caret"] = ed.textCursor().position() == 3
r["focused_again"] = ed.hasFocus()
for i in range(w.tabs.count()):
    e = w._editor_from_widget(w.tabs.widget(i))
    if e is not None:
        e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_continue_editing_opens_the_draft_ready_to_type(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert r == {"new_tab": True, "at_end": True, "focused": True, "no_copy": True,
                 "same_tab": True, "kept_caret": True, "focused_again": True}
