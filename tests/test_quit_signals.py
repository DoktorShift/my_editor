# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins that Ctrl+C, or a request to stop, quits MyEditor as Quit does
(quit_signals.py).

What must hold: the signal is handled at once, with no timer polling for
it; the first one asks the app's own Quit (which asks about unsaved
work), a second while that is under way ends the process at once, and
keeping on editing makes the next signal ask again. The exit status is
128 and the signal's number (130 for Ctrl+C). The real app, started from
a terminal, quits on Ctrl+C and on SIGTERM.
"""

import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quit_signals import QuitOnSignal  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    yield QApplication.instance() or QApplication(sys.argv)


def settle(seconds=0.3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)


@pytest.fixture
def quitting():
    asked, forced, answers = [], [], []

    def quit():
        asked.append(True)
        return answers.pop(0) if answers else True

    made = QuitOnSignal(quit, force_exit=forced.append)
    yield made, asked, forced, answers
    made.uninstall()


def test_ctrl_c_asks_the_apps_quit_and_says_130(quitting):
    made, asked, forced, _answers = quitting
    signal.raise_signal(signal.SIGINT)
    settle()
    assert asked == [True] and forced == []
    assert made.exit_status(0) == 130
    assert made.exit_status(1) == 1                # an error stays an error


def test_asked_twice_it_ends_at_once(quitting):
    made, asked, forced, _answers = quitting
    signal.raise_signal(signal.SIGINT)
    signal.raise_signal(signal.SIGINT)
    settle()
    assert forced == [130]


def test_kept_editing_the_next_ctrl_c_asks_again(quitting):
    made, asked, forced, answers = quitting
    answers.append(False)                         # Keep Editing
    signal.raise_signal(signal.SIGINT)
    settle()
    assert made.received is None and made.exit_status(0) == 0
    signal.raise_signal(signal.SIGINT)
    settle()
    assert asked == [True, True] and forced == []


def test_uninstalled_the_handlers_are_as_before():
    before = signal.getsignal(signal.SIGINT)
    made = QuitOnSignal(lambda: True, force_exit=lambda status: None)
    assert signal.getsignal(signal.SIGINT) is not before
    made.uninstall()
    assert signal.getsignal(signal.SIGINT) is before


@pytest.mark.skipif(sys.platform == "win32", reason="Windows sends no SIGINT or SIGTERM to a process")
@pytest.mark.parametrize("number, status", [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_the_app_started_from_a_terminal_quits_on_the_signal(tmp_path, number, status):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               QT_QPA_PLATFORM="offscreen",
               MYEDITOR_IPC_NAME=f"myeditor-quit-test-{os.getpid()}-{number}")
    app = subprocess.Popen([sys.executable, os.path.join(root, "main.py")], cwd=root, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        # Up: its window takes a second launch's files (the local socket
        # it listens on), however long a busy machine takes to start it.
        from PySide6.QtNetwork import QLocalSocket
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and app.poll() is None:
            probe = QLocalSocket()
            probe.connectToServer(env["MYEDITOR_IPC_NAME"])
            if probe.waitForConnected(200):
                probe.disconnectFromServer()
                break
            time.sleep(0.2)
        assert app.poll() is None, app.stdout.read().decode(errors="replace")
        os.kill(app.pid, number)
        output, _ = app.communicate(timeout=20)
    finally:
        if app.poll() is None:
            app.kill()
    assert app.returncode == status, output.decode(errors="replace")[-2000:]
    assert b"KeyboardInterrupt" not in output
