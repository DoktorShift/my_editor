# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the alert that offers dictation on this computer (dictation/setup.py).

What must hold, as an Apple alert asks before it downloads:

  Nothing is downloaded until the person chooses Download; the alert says
  how much, that it works without the internet afterwards and that the
  voice never leaves the computer. Not Now downloads nothing. Download
  shows its progress in the same alert, with Cancel; it finishes by
  closing the alert, model installed. A download that fails says why and
  offers Try Again; one cancelled leaves nothing behind. The download
  runs off the interface thread.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dictation import models, setup  # noqa: E402
from verified_download import Cancelled  # noqa: E402

MODEL = models.MODELS[models.STANDARD]


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    yield QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))             # nothing downloaded yet


def wait_for(condition, timeout=3000):
    for _ in range(timeout // 10):
        if condition():
            return True
        QTest.qWait(10)
    return condition()


def test_it_asks_first_and_says_what_it_downloads():
    calls = []
    alert = setup.SetupAlert(None, MODEL, install=lambda *a, **k: calls.append(a))
    assert alert.title_label.text() == "Dictate on this computer?"
    words = alert.message_label.text()
    assert "161 MB" in words and "never leaves" in words and "without the internet" in words
    alert.show()
    alert.buttons["Not Now"].click()
    assert alert.value == setup.NOT_NOW and calls == []


def test_download_shows_its_progress_and_closes_when_done():
    threads = []

    def install(model, *, progress, cancel):
        threads.append(QThread.currentThread())
        for done in (40, 80, 100):
            progress(done, 100)

    alert = setup.SetupAlert(None, MODEL, install=install)
    alert.show()
    alert.buttons["Download"].click()
    assert wait_for(lambda: not alert.isVisible())
    assert alert.value == setup.INSTALLED and alert.progress.value() == 100
    assert threads and threads[0] is not QThread.currentThread()


def test_a_failed_download_says_why_and_offers_try_again():
    attempts = []

    def install(model, *, progress, cancel):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("network is unreachable")

    alert = setup.SetupAlert(None, MODEL, install=install)
    alert.show()
    alert.buttons["Download"].click()
    assert wait_for(lambda: alert.buttons["Download"].text() == "Try Again")
    assert "internet connection" in alert.message_label.text() and alert.isVisible()
    alert.buttons["Download"].click()
    assert wait_for(lambda: not alert.isVisible()) and alert.value == setup.INSTALLED


def test_cancel_stops_the_download():
    stopped = []

    def install(model, *, progress, cancel):
        while not cancel.is_set():
            QThread.msleep(5)
        stopped.append(True)
        raise Cancelled()

    alert = setup.SetupAlert(None, MODEL, install=install)
    alert.show()
    alert.buttons["Download"].click()
    cancel = alert.buttons["Not Now"]
    assert wait_for(lambda: cancel.text() == "Cancel")
    cancel.click()
    assert wait_for(lambda: not alert.isVisible())
    assert alert.value == setup.NOT_NOW and stopped == [True]
