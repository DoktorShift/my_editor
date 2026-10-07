# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Starting the app: main.main(), run for real in a child process.

What must hold:

  From the moment the application object exists, Python's garbage is
  collected on the GUI thread only (main_thread_gc): automatic
  collection is off and one GuiThreadCollector belongs to the
  application, before anything else can start a worker.

  A file named on the command line is handed to a running window as an
  absolute path, made in the folder the second launch started in: the
  running window started somewhere else, where a relative path would
  mean another file. Then the second launch ends.

The handover itself is recorded instead of sent, so no socket is used.
"""

import os

from file_paths import same_file
from tests.app_process import run_window_script

SECOND_LAUNCH = r"""
import gc, json, os, sys
sys.path.insert(0, sys.argv[1])
os.chdir(sys.argv[2])
import main
from main_thread_gc import GuiThreadCollector
from PySide6.QtWidgets import QApplication

seen = {}

def hand_over(path):
    seen["handed_over"] = path
    seen["automatic_collection"] = gc.isenabled()
    seen["collectors"] = len(QApplication.instance().findChildren(GuiThreadCollector))
    return True              # a running window took the file

main._forward_to_running_instance = hand_over
sys.argv = [sys.argv[0], "notes.md"]
try:
    main.main()
except SystemExit as end:
    seen["exit"] = end.code
print("RESULT " + json.dumps(seen))
"""


def test_a_second_launch_collects_on_its_gui_thread_and_hands_over_an_absolute_path(tmp_path):
    started_in = tmp_path / "elsewhere"
    started_in.mkdir()
    (started_in / "notes.md").write_text("text", encoding="utf-8")

    r = run_window_script(SECOND_LAUNCH, tmp_path / "home", str(started_in))

    assert r["automatic_collection"] is False
    assert r["collectors"] == 1
    assert os.path.isabs(r["handed_over"])
    assert same_file(r["handed_over"], str(started_in / "notes.md"))
    assert r["exit"] == 0
