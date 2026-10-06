# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Garbage is collected on the GUI thread only.

What must hold:

  Python's own thresholds decide when a collection is due and which
  generations it takes; nothing is collected before that.

  While the collector runs, garbage piling up on another thread is not
  collected there: the GUI thread collects it, Qt objects included.

  Stopped, it hands collection back to Python as it was.
"""

import gc
import os
import sys
import threading
import weakref

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main_thread_gc  # noqa: E402
from main_thread_gc import GuiThreadCollector, collect_due  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def gc_as_it_was():
    enabled, threshold = gc.isenabled(), gc.get_threshold()
    gc.collect()
    yield
    gc.set_threshold(*threshold)
    (gc.enable if enabled else gc.disable)()
    gc.collect()


@pytest.fixture
def collections(monkeypatch):
    """Which generation each collection asked for."""
    asked = []
    monkeypatch.setattr(main_thread_gc.gc, "collect", lambda generation=2: asked.append(generation) or 0)
    return asked


@pytest.mark.parametrize("count, expected", [
    ((700, 50, 50), []),          # the youngest has not passed its threshold yet
    ((701, 3, 4), [0]),
    ((701, 11, 4), [1]),
    ((701, 3, 11), [2]),          # the oldest that passed its threshold, with the younger ones
    ((701, 11, 11), [2]),
])
def test_the_thresholds_decide_what_is_collected(monkeypatch, collections, count, expected):
    monkeypatch.setattr(main_thread_gc.gc, "get_threshold", lambda: (700, 10, 10))
    monkeypatch.setattr(main_thread_gc.gc, "get_count", lambda: count)
    collect_due()
    assert collections == expected


class _Cycle:
    def __init__(self):
        self.me = self
        self.timer = QTimer()
        self.timer.start(60_000)


def _garbage_cycle():
    cycle = _Cycle()
    return weakref.ref(cycle)


def test_nothing_is_collected_before_it_is_due():
    gc.disable()
    gc.set_threshold(1_000_000)
    alive = _garbage_cycle()
    assert collect_due() == 0
    assert alive() is not None


def test_a_due_collection_frees_the_cycles():
    gc.disable()
    gc.set_threshold(10)
    alive = _garbage_cycle()
    kept = [[] for _ in range(50)]
    assert collect_due() > 0
    assert alive() is None
    assert len(kept) == 50


def test_garbage_from_another_thread_is_collected_on_the_gui_thread():
    gc.set_threshold(100)
    threads = []

    def note(phase, _info):
        if phase == "start":
            threads.append(threading.current_thread())

    collector = GuiThreadCollector(interval_ms=10)
    gc.callbacks.append(note)
    try:
        assert not gc.isenabled()
        alive = _garbage_cycle()
        kept = []
        # Far past the threshold, on another thread: Python itself would
        # collect there, the Qt timer in the cycle included.
        worker = threading.Thread(target=lambda: kept.append([[[]] for _ in range(5_000)]))
        worker.start()
        worker.join()
        assert threads == [] and alive() is not None

        waited = 0
        while alive() is not None and waited < 2_000:
            QTest.qWait(10)
            waited += 10
        assert alive() is None
        assert threads and all(t is threading.main_thread() for t in threads)
    finally:
        gc.callbacks.remove(note)
        collector.stop()


@pytest.mark.parametrize("enabled", [True, False])
def test_stopping_hands_collection_back_as_it_was(enabled):
    (gc.enable if enabled else gc.disable)()
    collector = GuiThreadCollector()
    assert not gc.isenabled()
    collector.stop()
    assert gc.isenabled() is enabled
