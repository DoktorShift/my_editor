# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Work off the GUI thread, and what it found back on it.

Reading a folder's index, a picture, a workbook or the notes that link
to a moved file takes longer than the window may stand still. ``run``
does such work on a worker thread and hands what it returns to ``done``
on the GUI thread (or what it raised to ``failed``), so the callbacks may
touch widgets and the work never does.

    run(work, done, failed, receiver=tab)

The result goes to the callbacks only while ``receiver`` lives: a tab
closed while its picture was read hears nothing, and ``discard`` may let
go of what was read for it (a workbook closes its file). Without a
receiver the result is always delivered.

``progress`` makes the work ``work(report)``: each ``report(...)`` on the
worker thread reaches ``progress(...)`` on the GUI thread, in order and
always before ``done``.

A ``Token`` stops what is no longer wanted: the work may ask
``token.is_set()`` to stop early, and once it is cancelled nothing more
is delivered.

The work runs on Qt's shared pool of threads, on ``Workers`` of the
caller's own (a fixed number of threads; one runs its jobs one at a time,
in the order given, and can be waited for), or on a daemon thread of its
own (``daemon=True``) that never holds up quitting the app.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)

_PROGRESS, _DONE, _FAILED = range(3)


class Token:
    """Says that work is no longer wanted. The work asks ``is_set()``
    (as it would a threading.Event) to stop early; ``cancel()`` is called
    on the GUI thread, and nothing more is delivered after it."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()


# Deliveries on their way, kept alive until they have delivered, and
# numbered in the order the work was given.
_PENDING: set = set()
_NUMBERS = itertools.count()


class _Delivery(QObject):
    """Carries what one piece of work hands back to the GUI thread. It is
    made there, as a child of the receiver, and goes with it: emitting
    ``arrived`` from the worker then raises, and nothing is delivered.
    What the worker hands over waits in a queue, so ``deliver`` can also
    be called directly (Workers.wait) instead of from the event loop."""

    arrived = Signal()

    def __init__(self, receiver: Optional[QObject], done, failed, progress,
                 token: Optional[Token], workers: Optional["Workers"]) -> None:
        super().__init__(receiver)
        self._done, self._failed, self._progress = done, failed, progress
        self._token = token
        self.workers = workers
        self.number = next(_NUMBERS)
        self._items: "queue.SimpleQueue" = queue.SimpleQueue()
        self._finished = False
        # A bound method: the connection takes this object's thread, so an
        # emit on the worker thread is queued to the GUI thread.
        self.arrived.connect(self.deliver)
        _PENDING.add(self)
        self.destroyed.connect(lambda *_args: _PENDING.discard(self))

    # -- on the worker thread --

    def hand_over(self, kind: int, value: Any) -> bool:
        """Queue ``value`` for the GUI thread; False when the receiver is
        gone and nobody will take it."""
        self._items.put((kind, value))
        try:
            self.arrived.emit()
        except RuntimeError:            # the receiver is gone
            return False
        return True

    # -- on the GUI thread --

    def deliver(self) -> None:
        """Hand what arrived to the callbacks, in the order it came."""
        while not self._finished:
            try:
                kind, value = self._items.get_nowait()
            except queue.Empty:
                return
            cancelled = self._token is not None and self._token.is_set()
            if kind == _PROGRESS:
                if not cancelled:
                    self._progress(*value)
                continue
            self._finished = True
            try:
                if not cancelled:
                    self._finish(kind, value)
            finally:
                _PENDING.discard(self)
                self.deleteLater()


    def _finish(self, kind: int, value: Any) -> None:
        if kind == _DONE:
            if self._done is not None:
                self._done(value)
        elif self._failed is not None:
            self._failed(value)
        else:
            log.error("Work off the window's thread failed", exc_info=value)


class _Runnable(QRunnable):
    def __init__(self, job: Callable[[], None]) -> None:
        super().__init__()
        self._job = job

    def run(self) -> None:  # on a pool thread
        self._job()


def run(work: Callable[..., Any], done: Optional[Callable[[Any], None]] = None,
        failed: Optional[Callable[[BaseException], None]] = None, *,
        receiver: Optional[QObject] = None,
        progress: Optional[Callable[..., None]] = None,
        token: Optional[Token] = None,
        discard: Optional[Callable[[Any], None]] = None,
        workers: Optional["Workers"] = None,
        daemon: bool = False, name: str = "background") -> None:
    """Run ``work()`` (``work(report)`` with ``progress``) off the GUI
    thread; ``done(result)`` or ``failed(error)`` follow on the GUI
    thread, unless ``receiver`` is gone or ``token`` cancelled by then.
    Without ``failed`` an error is logged. ``discard(result)`` is called
    on the worker thread with a result whose receiver was gone. The work
    runs on ``workers``, on a daemon thread named ``name`` (``daemon``),
    else on Qt's shared pool. Call it on the GUI thread."""
    delivery = _Delivery(receiver, done, failed, progress, token, workers)

    def report(*values) -> None:
        delivery.hand_over(_PROGRESS, values)

    def job() -> None:
        try:
            result = work(report) if progress is not None else work()
        except BaseException as error:  # noqa: BLE001, a worker always reports back
            delivery.hand_over(_FAILED, error)
            return
        if not delivery.hand_over(_DONE, result) and discard is not None:
            discard(result)

    if daemon:
        threading.Thread(target=job, name=name, daemon=True).start()
    elif workers is not None:
        workers.pool.start(_Runnable(job))
    else:
        QThreadPool.globalInstance().start(_Runnable(job))


class Workers(QObject):
    """Worker threads of the caller's own, going with ``parent``: with
    ``threads=1`` the jobs run one at a time, in the order given."""

    def __init__(self, parent: Optional[QObject] = None, threads: int = 1) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(threads)

    def run(self, work: Callable[..., Any], done=None, failed=None, **options) -> None:
        """``run`` on these threads."""
        run(work, done, failed, workers=self, **options)

    def wait(self, msecs: int = -1) -> bool:
        """Wait (at most ``msecs``, -1 for as long as it takes) until every
        job given has run, then deliver what they handed back at once, in
        the order the jobs were given, instead of from the event loop. False when the time ran out."""
        finished = self.pool.waitForDone(msecs)
        mine = sorted((d for d in _PENDING if d.workers is self), key=lambda d: d.number)
        for delivery in mine:
            delivery.deliver()
        return finished

    def clear(self) -> None:
        """Drop the jobs that have not started yet."""
        self.pool.clear()
