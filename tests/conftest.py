# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One QApplication for the whole test run.

Qt allows one application object per process. A test module that made
a plain QCoreApplication first left every widget test after it in the
same run without the QApplication widgets need, and the interpreter
aborted ("Fatal Python error: Aborted"). Making the QApplication here,
before any test module is imported, means every module's own
``QCoreApplication.instance() or ...`` fixture finds it, whatever order
the files run in.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv[:1])
