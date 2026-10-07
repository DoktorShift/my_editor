# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One QApplication for the whole test run, and garbage collected on its
thread only.

Qt allows one application object per process. A test module that made
a plain QCoreApplication first left every widget test after it in the
same run without the QApplication widgets need, and the interpreter
aborted ("Fatal Python error: Aborted"). Making the QApplication here,
before any test module is imported, means every module's own
``QCoreApplication.instance() or ...`` fixture finds it, whatever order
the files run in.

Python's cycle collector runs on whichever thread is executing Python
when it is due. On Windows that was often one of the threads reading a
child process's output while a test waited for it; Qt objects a cycle
held were destroyed there, a timer stayed registered with this thread,
and it later fired into freed memory and ended the whole run. So the
run collects the way the app does (main_thread_gc): never
automatically, and on this thread after every test, as much as
Python's own thresholds call for.
"""

import gc
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.app_process import offscreen_fonts, release_clipboard  # noqa: E402

# Offscreen unless the run chose another platform (the Windows job uses
# Windows' own), with the system's fonts also on Windows, so a run on a
# Windows machine measures text as the workflow does.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
offscreen_fonts(os.environ)
# The run's own single-instance socket (constants.IPC_SERVER_NAME), set
# before any app module is imported: a window a test builds never takes
# over the socket of a MyEditor the person is using.
os.environ.setdefault("MYEDITOR_IPC_NAME", f"myeditor-tests-{os.getpid()}")

from PySide6.QtWidgets import QApplication  # noqa: E402

from main_thread_gc import collect_due  # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv[:1])

gc.disable()


def pytest_unconfigure(config):
    release_clipboard()


@pytest.hookimpl(trylast=True)
def pytest_runtest_teardown(item, nextitem):
    if gc.isenabled():
        # Every later test would collect on any thread again.
        gc.disable()
        pytest.fail("the test left automatic garbage collection on; put it back as it "
                    "was (the run collects on this thread only, see main_thread_gc)",
                    pytrace=False)
    collect_due()
