# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins dictation on this computer (dictation/on_device.py).

What must hold:

  The microphone's sound, in whatever form the device gives it, reaches
  the engine as 16 kHz mono. The engine hands the voice detector whole
  windows only, and each phrase it cuts is written while it is still
  there (the native library frees it once taken off its queue). What the
  model "hears" in noise ([Musik], (Applaus)) is never written. A
  dictation says when it listens and when it stops, writes each phrase as
  it is ready and the last one when stopped, reports the input level, and
  says plainly when there is no microphone or the model can't be loaded.
  The work runs off the interface thread.
"""

import array
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QThread, Signal  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from dictation import on_device  # noqa: E402
from dictation.on_device import AudioForm, OnDeviceDictation, WhisperEngine  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    yield QApplication.instance() or QApplication(sys.argv)


@pytest.mark.parametrize("heard, written", [
    (" Hello there. ", "Hello there."),
    ("[Musik]", ""), ("(Applaus)", ""), ("*Musik*", ""), ("[BLANK_AUDIO]", ""),
    ("Gut. [Musik]", "Gut."),
])
def test_what_the_model_hears_in_noise_is_never_written(heard, written):
    assert on_device.clean_phrase(heard) == written


def test_sound_in_any_form_becomes_16_khz_mono():
    stereo = array.array("h", [16384, -16384, 8192, 8192]).tobytes()
    assert on_device.to_mono(stereo, AudioForm(16000, 2, "int16")) == [0.0, 0.25]
    floats = struct.pack("<2f", 0.5, -0.5)
    assert on_device.to_mono(floats, AudioForm(16000, 1, "float")) == [0.5, -0.5]


class Segment:
    def __init__(self, samples):
        self._samples = samples
        self.freed = False

    @property
    def samples(self):
        assert not self.freed, "read after the queue let it go"
        return self._samples


class Detector:
    """Silero's voice detector as the engine uses it: whole windows in, a
    queue of segments out, each freed once popped."""
    window = 4

    def __init__(self):
        self.windows, self.queue = [], []

    def accept_waveform(self, samples):
        assert len(samples) == self.window, "a short window crashes the native detector"
        self.windows.append(list(samples))
        if len(self.windows) % 3 == 0:                  # a pause after three windows
            self.queue.append(Segment([w for window in self.windows[-3:] for w in window]))

    def empty(self):
        return not self.queue

    @property
    def front(self):
        return self.queue[0]

    def pop(self):
        self.queue.pop(0).freed = True

    def flush(self):
        if len(self.windows) % 3:
            self.queue.append(Segment([0.0] * self.window))


class Recognizer:
    def __init__(self):
        self.heard = []

    def write(self, samples):
        self.heard.append(len(samples))
        return f"phrase {len(self.heard)}"


def test_the_engine_hands_over_whole_windows_and_writes_each_phrase_in_time():
    detector, recognizer = Detector(), Recognizer()
    engine = WhisperEngine(detector, recognizer.write, window=Detector.window)
    assert engine.accept([0.1] * 6) == []                     # one window, two kept
    assert engine.accept([0.1] * 6) == ["phrase 1"]           # three windows: a pause
    assert recognizer.heard == [12]
    assert engine.accept([0.1] * 3) == []
    assert engine.finish() == ["phrase 2"]                    # the rest, when stopped


class Source(QObject):
    """A microphone for tests: sound arrives when a test sends it."""
    sound = Signal(bytes)
    failed = Signal(str)

    def __init__(self, missing=False):
        super().__init__()
        self.form = AudioForm(16000, 1, "int16")
        self.missing, self.running = missing, False

    def start(self):
        if self.missing:
            return False
        self.running = True
        return True

    def stop(self):
        self.running = False


class Engine:
    def __init__(self):
        self.thread = None
        self.heard = 0

    def accept(self, samples):
        self.thread = QThread.currentThread()
        self.heard += len(samples)
        return ["Hallo Welt."] if self.heard >= 16000 else []

    def finish(self):
        return ["Ende."]


def wait_for(condition, timeout=3000):
    for _ in range(timeout // 10):
        if condition():
            return True
        QTest.qWait(10)
    return condition()


def run_dictation(source, make_engine):
    dictation = OnDeviceDictation(make_engine, make_source=lambda: source)
    seen = {"phrases": [], "levels": [], "listening": [], "failed": [], "ready": []}
    dictation.ready.connect(lambda: seen["ready"].append(True))
    dictation.phrase.connect(seen["phrases"].append)
    dictation.level.connect(seen["levels"].append)
    dictation.listening_changed.connect(seen["listening"].append)
    dictation.failed.connect(seen["failed"].append)
    return dictation, seen


def test_a_dictation_listens_writes_each_phrase_and_the_last_when_stopped():
    engine = Engine()
    source = Source()
    dictation, seen = run_dictation(source, lambda: engine)
    dictation.start()
    assert wait_for(lambda: seen["listening"] == [True]) and source.running
    assert wait_for(lambda: seen["ready"] == [True])            # the model listens
    loud = array.array("h", [8000, -8000] * 4000).tobytes()       # half a second
    source.sound.emit(loud)
    source.sound.emit(loud)
    assert wait_for(lambda: seen["phrases"] == ["Hallo Welt."])
    assert seen["levels"] and max(seen["levels"]) > 0.1
    assert engine.thread is not QThread.currentThread()           # off the interface
    dictation.stop()
    assert wait_for(lambda: seen["listening"] == [True, False])
    assert seen["phrases"] == ["Hallo Welt.", "Ende."] and not source.running


def test_no_microphone_or_no_model_is_said_plainly():
    dictation, seen = run_dictation(Source(missing=True), Engine)
    dictation.start()
    assert wait_for(lambda: seen["failed"])
    assert "microphone" in seen["failed"][0].lower() and seen["listening"] in ([], [False])

    def broken():
        raise RuntimeError("model file damaged")
    dictation, seen = run_dictation(Source(), broken)
    dictation.start()
    assert wait_for(lambda: seen["failed"])
    assert "model" in seen["failed"][0].lower() and seen["ready"] == []
    # It stops by itself, its thread done: nothing is left running.
    assert wait_for(lambda: seen["listening"] == [True, False])


def test_quitting_mid_dictation_leaves_no_thread_running():
    dictation, seen = run_dictation(Source(), Engine)
    dictation.start()
    assert wait_for(lambda: seen["ready"] == [True])
    dictation.shutdown()
    assert seen["listening"] == [True, False] and not dictation.is_listening()
