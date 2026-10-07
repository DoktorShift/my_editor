# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""How the tests run the app: the clipboard is let go of before the end.

What must hold:

  Clipboard data made in Python is freed while Python still runs, in
  the test run (at its end) and in every child process that builds the
  window (after its script). Qt's offscreen platform would otherwise
  free it after the interpreter is gone, which crashed processes on
  their way out after everything had passed: on Windows they ended with
  an error, on a Mac they waited for the crash reporter for good.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shiboken6  # noqa: E402
from PySide6.QtCore import QMimeData  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from tests.app_process import CHILD_EPILOGUE, REPO, child_env, release_clipboard  # noqa: E402


def test_releasing_the_clipboard_frees_what_python_put_there():
    mime = QMimeData()
    mime.setText("copied in a test")
    QGuiApplication.clipboard().setMimeData(mime)
    release_clipboard()
    assert not shiboken6.isValid(mime)
    assert QGuiApplication.clipboard().text() == ""


def test_a_child_that_copied_ends_without_an_error(tmp_path):
    import subprocess
    copies = (
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from PySide6.QtCore import QMimeData\n"
        "from PySide6.QtWidgets import QApplication\n"
        "app = QApplication(sys.argv[:1])\n"
        "mime = QMimeData()\n"
        "mime.setText('copied in a child')\n"
        "app.clipboard().setMimeData(mime)\n"
        "print('RESULT {}')\n"
    )
    proc = subprocess.run([sys.executable, "-c", copies + CHILD_EPILOGUE, REPO],
                          env=child_env(tmp_path), capture_output=True, text=True, timeout=60)
    assert "RESULT {}" in proc.stdout
    assert proc.returncode == 0, proc.stderr[-1000:]
