# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Work off the window's thread (background.py): what it finds comes back
on the window's thread, in order, only while someone wants it."""

from __future__ import annotations

import threading

from PySide6.QtCore import QCoreApplication, QEvent, QObject

import background
from tests.waiting import wait_until


def test_the_result_comes_back_on_the_window_thread():
    seen = []
    background.run(lambda: threading.current_thread(),
                   lambda worker: seen.append((worker, threading.current_thread())))
    assert wait_until(lambda: seen)
    worker, delivered_on = seen[0]
    assert worker is not threading.main_thread()
    assert delivered_on is threading.main_thread()


def test_an_error_goes_to_failed_and_otherwise_to_the_log(caplog):
    errors = []
    background.run(lambda: 1 / 0, lambda _result: errors.append("done"), errors.append)
    assert wait_until(lambda: errors)
    assert isinstance(errors[0], ZeroDivisionError)
    background.run(lambda: 1 / 0)
    assert wait_until(lambda: "failed" in caplog.text)


def test_progress_comes_in_order_and_before_the_result():
    heard = []

    def work(report):
        for share in (0.25, 0.5, 1.0):
            report(share)
        return "read"

    background.run(work, lambda result: heard.append(result), progress=heard.append)
    assert wait_until(lambda: "read" in heard)
    assert heard == [0.25, 0.5, 1.0, "read"]


def test_nothing_comes_back_to_a_receiver_that_is_gone():
    receiver = QObject()
    go = threading.Event()
    heard, discarded = [], []
    background.run(lambda: go.wait(5) and "late", heard.append, heard.append,
                   receiver=receiver, discard=discarded.append)
    receiver.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    go.set()
    assert wait_until(lambda: discarded)
    assert discarded == ["late"]
    for _ in range(5):
        QCoreApplication.processEvents()
    assert heard == []


def test_a_cancelled_token_stops_the_work_and_what_it_found():
    token = background.Token()
    started, go = threading.Event(), threading.Event()
    heard, stopped = [], []

    def work():
        started.set()
        go.wait(5)
        stopped.append(token.is_set())
        return "stale"

    background.run(work, heard.append, receiver=None, token=token)
    assert started.wait(5)
    token.cancel()
    go.set()
    assert wait_until(lambda: stopped)
    for _ in range(5):
        QCoreApplication.processEvents()
    assert stopped == [True] and heard == []


def test_one_worker_runs_its_jobs_in_order_and_can_be_waited_for():
    owner = QObject()
    workers = background.Workers(owner, threads=1)
    order, heard = [], []
    go = threading.Event()

    def job(number):
        def work():
            if number == 0:
                go.wait(5)
            order.append(number)
            return number
        return work

    for number in range(3):
        workers.run(job(number), heard.append, receiver=owner)
    go.set()
    assert workers.wait(5000)
    # Delivered by wait itself, before the event loop ran.
    assert order == [0, 1, 2] and heard == [0, 1, 2]
