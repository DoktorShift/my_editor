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

The suite can also run as Windows and Linux see it, on any computer:

  MYEDITOR_TEST_AS=linux   sys.platform reads "linux" from the start, so
                           every module that asks at import sees Linux
                           (alerts.IS_MAC among them)
  MYEDITOR_TEST_AS=other   only the keyboard rules of Windows and Linux
                           (alerts.IS_MAC False); the platform as it is

Window scripts that run in a process of their own (tests/app_process.py)
are run the same way.

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

# Before anything else is imported: see the module docstring.
if os.environ.get("MYEDITOR_TEST_AS") == "linux":
    sys.platform = "linux"

import pytest  # noqa: E402

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
# The run speaks English, whatever this computer's language or the
# person's language setting: importing main installs the chosen language
# (main.py, before any text is read), and on a German Mac every test
# after the first that imports main saw German texts.
os.environ.setdefault("MYEDITOR_LANGUAGE", "en")

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from main_thread_gc import collect_due  # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv[:1])
# As main.py does: a widget given a style sheet is polished at once.
import theme  # noqa: E402

theme.install_style_sheet_guard(_APP)

gc.disable()


@pytest.fixture(autouse=True)
def _backups_of_its_own(tmp_path_factory, monkeypatch):
    """A window a test builds backs up what is typed in it, as the app does:
    into a folder of the test's own, never the person's ~/.cache, from where
    MyEditor offers every backup back at its next launch."""
    import recovery
    monkeypatch.setattr(recovery, "BACKUP_DIR", str(tmp_path_factory.mktemp("backups")))


def pytest_configure(config):
    if os.environ.get("MYEDITOR_TEST_AS") == "other":
        import alerts
        alerts.IS_MAC = False


def pytest_sessionfinish(session, exitstatus):
    """Every window a test left behind is deleted while the application
    still lives.

    A main window that was shown and then closed (not deleted, so the
    application's signals still reach it during its test) was otherwise
    destroyed by the interpreter's last collection, after the
    QApplication, and the run ended in a segmentation fault although
    every test had passed. Widgets are deleted the way Qt deletes them
    (deleteLater, then the posted deferred deletes), and what Python
    still held of them is collected on this thread afterwards. A window
    with a parent (a menu, a pop-up) goes with that parent: deleted on
    its own first, it left its parent pointing at it (a toolbar's
    chevron menu, held by the toolbar's layout)."""
    for widget in QApplication.topLevelWidgets():
        if widget.parent() is None:
            widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


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
