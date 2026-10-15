# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ctrl+C in the terminal, or a request to stop (SIGTERM, as ``kill`` and
a logout send), quits MyEditor the way Quit does.

A Qt app spends its time in Qt's event loop, in C++, and Python handles a
signal only when it next runs Python code. By default Ctrl+C therefore
raised KeyboardInterrupt inside whatever ran next (the caret's blink, the
garbage collector's tick); Qt printed it and the app went on.

Here each signal also writes a byte to a socket (signal.set_wakeup_fd)
that the event loop watches (QSocketNotifier), so the loop wakes at once,
without a timer polling for it, and on Windows as well (a socket, not a
pipe). Then:

  * the first signal quits as Quit (⌘Q) does: unsaved documents are asked
    about, uploads and saves under way are too, and nothing is cut short;
    choosing to keep editing keeps the app, and the next signal asks again;
  * a second one while that quit is under way ends the process at once,
    as command-line programs do when asked twice;
  * the exit status says which signal ended it, 128 and its number (130
    for Ctrl+C), as a shell expects.
"""

from __future__ import annotations

import os
import signal
import socket
from typing import Callable, Optional, Sequence

from PySide6.QtCore import QObject, QSocketNotifier, QTimer

# The signals that ask MyEditor to quit; SIGTERM can't be sent to a
# process on Windows, but asking for it there does no harm.
QUIT_SIGNALS = (signal.SIGINT, signal.SIGTERM)


class QuitOnSignal(QObject):
    """Quits on :data:`QUIT_SIGNALS` (see the module). ``quit()`` is the
    app's own Quit and returns False when the person kept editing;
    ``force_exit(status)`` ends the process at once."""

    def __init__(self, quit: Callable[[], Optional[bool]], *,
                 signals: Sequence[int] = QUIT_SIGNALS,
                 force_exit: Callable[[int], None] = os._exit,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._quit = quit
        self._force_exit = force_exit
        # The signal that asked to quit, while that quit is under way or done.
        self.received: Optional[int] = None
        self._reader, self._writer = socket.socketpair()
        for end in (self._reader, self._writer):
            end.setblocking(False)
        self._previous_fd = signal.set_wakeup_fd(self._writer.fileno())
        self._notifier = QSocketNotifier(self._reader.fileno(), QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._drain)
        self._previous = {number: signal.signal(number, self._on_signal) for number in signals}

    def exit_status(self, status: int) -> int:
        """The process's exit status once the event loop returned
        ``status``: 128 and the signal's number when a signal quit it."""
        return 128 + self.received if self.received is not None and status == 0 else status

    def uninstall(self) -> None:
        """Signals are handled as before again."""
        for number, previous in self._previous.items():
            signal.signal(number, previous)
        signal.set_wakeup_fd(self._previous_fd)
        self._notifier.setEnabled(False)
        self._reader.close()
        self._writer.close()

    def _on_signal(self, number: int, _frame) -> None:
        # Runs in Python's own time, as soon as the woken loop runs Python.
        if self.received is not None:
            self._force_exit(128 + number)      # asked twice: at once
            return
        self.received = number
        # After this handler: the quit may show a dialog, with its own loop.
        QTimer.singleShot(0, self._ask_to_quit)

    def _ask_to_quit(self) -> None:
        if self._quit() is False:
            self.received = None                 # kept editing: ask again next time

    def _drain(self) -> None:
        """Empty the socket; the bytes only woke the loop."""
        try:
            while self._reader.recv(64):
                pass
        except (BlockingIOError, InterruptedError):
            pass
