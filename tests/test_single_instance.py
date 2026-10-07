# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The single-instance socket a second launch hands its file to.

What must hold:

  The running window listens on the socket named by MYEDITOR_IPC_NAME
  when that is set, and a test run sets it, so a window a test builds
  never removes the socket of the MyEditor the person is using; a second
  launch would otherwise open its file in a test window, and the real
  window would get no more files until it restarted.

  Without the variable, the app's own name is used, as every release
  always has.
"""

import os
import subprocess
import sys

from tests.app_process import REPO, run_window_script

WINDOW = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
w = main_window.MainWindow()
result = {"listening": w._ipc_server.isListening(), "name": w._ipc_server.serverName()}
for i in range(w.tabs.count()):
    e = w._editor_from_widget(w.tabs.widget(i))
    if e is not None:
        e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(result))
"""


def test_a_test_window_listens_on_the_runs_own_socket(tmp_path):
    r = run_window_script(WINDOW, tmp_path)
    assert r["listening"]
    assert r["name"] == os.environ["MYEDITOR_IPC_NAME"] != "minimal-texteditor-ipc"


def test_without_the_variable_the_apps_own_name_is_used():
    env = {k: v for k, v in os.environ.items() if k != "MYEDITOR_IPC_NAME"}
    proc = subprocess.run(
        [sys.executable, "-c", "import constants; print(constants.IPC_SERVER_NAME)"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
    assert proc.stdout.strip() == "minimal-texteditor-ipc", proc.stderr
