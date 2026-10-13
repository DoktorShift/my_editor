# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins what the status bar says about files added in the Media Library
that did not upload after its window was closed.

What must hold:

  Each failure is counted: one names the file, several say how many,
  and every reason is in the tooltip. It stays, as a backup problem
  does, until the person opens the Media Library (where each is shown
  with Retry), however many other messages come and go; Open Media
  Library beside it does that. A failure while the library is open is
  said there, not here. It shortens in a narrow window instead of
  widening it.
"""

from tests.app_process import run_window_script  # noqa: E402

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from tests.accessibility import unnamed_controls

w = main_window.MainWindow()
w.resize(1100, 700)
w.show()
app.processEvents()
store = w._media_store
store.can_retry = lambda name: True
opened = []
w._on_nostr_media_library = lambda: opened.append(True) or w._clear_library_failures()


def shown():
    return [w._library_problem.isVisible(), w._library_problem.full_text(),
            w._library_open.isVisible()]


r = {"quiet": shown()}
store.upload_failed.emit("Holiday.png", "Your media server is busy. Try again later.")
app.processEvents()
r["one"] = shown()
for name in ("Talk.mp4", "Scan.pdf"):
    store.upload_failed.emit(name, "Couldn’t reach your media server. Check your internet connection.")
app.processEvents()
r["three"] = shown()
r["tooltip"] = w._library_problem.toolTip()
w.status.showMessage("Saved: /tmp/x.txt", 0)
app.processEvents()
r["after_other_message"] = shown()
r["names"] = unnamed_controls(w.statusBar())
r["min_width"] = w.minimumSizeHint().width()
w._library_open.click()
app.processEvents()
r["opened"] = opened
r["after_open"] = shown()
w.close()
print("RESULT " + json.dumps(r))
"""


def test_failures_after_the_library_closed_are_counted_and_stay_until_it_opens(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert r["quiet"] == [False, "", False]
    assert r["one"] == [True, "“Holiday.png” wasn’t added to your Media Library.", True]
    assert r["three"] == [True, "3 files weren’t added to your Media Library.", True]
    assert r["tooltip"].splitlines() == [
        "“Holiday.png” wasn’t added to your Media Library. Your media server is busy. "
        "Try again later.",
        "“Talk.mp4” wasn’t added to your Media Library. Couldn’t reach your media server. "
        "Check your internet connection.",
        "“Scan.pdf” wasn’t added to your Media Library. Couldn’t reach your media server. "
        "Check your internet connection.",
    ]
    assert r["after_other_message"] == r["three"]
    assert r["names"] == []
    assert r["min_width"] < 420
    assert r["opened"] == [True]
    assert r["after_open"] == [False, "", False]
