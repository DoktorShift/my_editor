# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Waiting in a test for what the app does on its own time: work on a
worker thread, a timer, a folder watched for changes."""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QCoreApplication, QEventLoop


def wait_until(condition: Callable[[], object], timeout_s: float = 5.0) -> bool:
    """Handle events until ``condition()`` holds, for at most ``timeout_s``
    seconds; whether it holds. Each round sleeps a moment, without
    Python's lock, so the app's worker threads get to run (QTest.qWait
    keeps the lock while it sleeps and starves them), and no event loop
    of its own is started."""
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        if condition():
            return True
        time.sleep(0.01)
    return bool(condition())
