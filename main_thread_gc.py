# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Python's garbage collection, run on the GUI thread only.

Python frees an object as soon as nothing refers to it any more. Objects
that refer to each other in a cycle wait for the cycle collector, and
Python runs the collector on whichever thread happens to be executing
Python code once enough objects have piled up: an import working in the
background, an account backup being encrypted, or, in the tests on
Windows, the threads that read a child process's output.

Such a cycle often holds Qt objects, such as a panel with the timers,
labels and layouts it owns, and a Qt object must be destroyed on the
thread it belongs to. Destroyed on another one, a running timer stays
registered with the GUI thread and later fires into freed memory, which
ends the app ("QObject::~QObject: Timers cannot be stopped from another
thread" is the last thing it says).

So automatic collection is switched off, and the collection Python
would have made is made on the GUI thread instead: ``collect_due``
applies Python's own thresholds, and ``GuiThreadCollector`` calls it a
few times a second while the app runs. The thresholds are Python's, so
collections cost what they always did, only on the right thread.
"""

from __future__ import annotations

import gc
from typing import Optional

from PySide6.QtCore import QObject, QTimer

# How often the GUI thread looks whether a collection is due.
CHECK_INTERVAL_MS = 500


def collect_due() -> int:
    """Make the collection Python's thresholds call for, if one is due, on
    the calling thread. Returns the number of unreachable objects found.

    As Python does: once the youngest generation has grown past its
    threshold, the oldest generation whose count has passed its threshold
    is collected, with every younger one.
    """
    threshold = gc.get_threshold()
    count = gc.get_count()
    if count[0] <= threshold[0]:
        return 0
    oldest = max(generation for generation in range(len(count))
                 if count[generation] > threshold[generation])
    return gc.collect(oldest)


class GuiThreadCollector(QObject):
    """Turns Python's automatic collection off and collects on the GUI
    thread instead, for as long as it runs.

    Made once, on the GUI thread, right after the application object;
    it lives as long as its parent.
    """

    def __init__(self, parent: Optional[QObject] = None, *,
                 interval_ms: int = CHECK_INTERVAL_MS) -> None:
        super().__init__(parent)
        self._was_enabled = gc.isenabled()
        gc.disable()
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(collect_due)
        self._timer.start()

    def stop(self) -> None:
        """Hand collection back to Python, as it was before."""
        self._timer.stop()
        if self._was_enabled:
            gc.enable()
