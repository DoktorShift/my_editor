# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dictation on this computer: the microphone, cut into phrases where the
speech pauses, each written by Whisper as soon as it is spoken.

    microphone ─▶ 16 kHz mono ─▶ voice detector ─▶ phrase ─▶ Whisper ─▶ text
    (interface thread)            (worker thread ......................)

The sound arrives on the interface thread (MicrophoneSource) and goes at
once to a worker thread, which turns it into 16 kHz mono, hands it to the
voice detector (Silero) and has each phrase it cuts written by Whisper
(sherpa-onnx, models.py). The interface never waits: typing goes on while a
phrase is written, and the phrase arrives as a signal.

The voice detector keeps silence away from Whisper, which "hears" words in
it ([Musik]); what it still notes about noise is never written
(clean_phrase). Nothing is recorded: the sound lives only as long as the
phrase it belongs to.
"""

from __future__ import annotations

import array
import math
import re
from typing import Callable, List, NamedTuple, Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot

from i18n import _

RATE = 16000                     # what the voice detector and Whisper take

# What Whisper writes for sounds rather than words: [Musik], (Applaus),
# *Musik*, [BLANK_AUDIO].
_NOISE_NOTE = re.compile(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*")


def clean_phrase(text: str) -> str:
    """A phrase as it is written: notes about noise left out, spaces tidied."""
    return " ".join(_NOISE_NOTE.sub(" ", text).split())


class AudioForm(NamedTuple):
    """How a microphone gives its sound: samples a second, channels, and the
    kind of sample ("int16", "int32", "float" or "uint8")."""
    rate: int
    channels: int
    kind: str


_ARRAY_TYPE = {"int16": ("h", 32768.0, 0.0), "int32": ("i", 2147483648.0, 0.0),
               "float": ("f", 1.0, 0.0), "uint8": ("B", 128.0, 128.0)}


def to_mono(data: bytes, form: AudioForm) -> List[float]:
    """``data`` as samples from -1 to 1, its channels mixed into one."""
    code, scale, middle = _ARRAY_TYPE[form.kind]
    samples = array.array(code)
    samples.frombytes(data[:len(data) - len(data) % samples.itemsize])
    channels = max(1, form.channels)
    if channels == 1:
        return [(s - middle) / scale for s in samples]
    return [sum(samples[i:i + channels]) / channels / scale - middle / scale
            for i in range(0, len(samples) - channels + 1, channels)]


def resample(samples: List[float], rate: int) -> List[float]:
    """``samples`` at ``rate`` as RATE samples a second (linear)."""
    if rate == RATE or not samples:
        return samples
    step = rate / RATE
    count = int(len(samples) / step)
    last = len(samples) - 1
    out = []
    for i in range(count):
        at = i * step
        low = int(at)
        high = min(low + 1, last)
        out.append(samples[low] + (samples[high] - samples[low]) * (at - low))
    return out


def level_of(samples: List[float]) -> float:
    """How loud ``samples`` are, from 0 to 1 (their root mean square)."""
    if not samples:
        return 0.0
    return min(1.0, math.sqrt(sum(s * s for s in samples) / len(samples)))


class WhisperEngine:
    """Phrases out of sound: the voice detector cuts them, ``write`` turns
    each into text.

    The native voice detector takes whole windows only (a shorter one
    crashes it), so what does not fill one waits for the next sound. A
    phrase is read while it is still on the detector's queue: popping it
    frees it."""

    def __init__(self, detector, write: Callable[[List[float]], str], *, window: int) -> None:
        self._detector = detector
        self._write = write
        self._window = window
        self._waiting: List[float] = []

    def accept(self, samples: List[float]) -> List[str]:
        """Take RATE mono ``samples``; the phrases they ended."""
        self._waiting.extend(samples)
        whole = len(self._waiting) - len(self._waiting) % self._window
        for start in range(0, whole, self._window):
            self._detector.accept_waveform(self._waiting[start:start + self._window])
        del self._waiting[:whole]
        return self._phrases()

    def finish(self) -> List[str]:
        """The phrase still being spoken, when dictation stops."""
        if self._waiting:
            padded = self._waiting + [0.0] * (self._window - len(self._waiting))
            self._detector.accept_waveform(padded)
            self._waiting = []
        self._detector.flush()
        return self._phrases()

    def _phrases(self) -> List[str]:
        written = []
        while not self._detector.empty():
            samples = list(self._detector.front.samples)
            self._detector.pop()
            text = clean_phrase(self._write(samples))
            if text:
                written.append(text)
        return written


def prepare() -> None:
    """Import the engine on the interface thread, before a dictation starts:
    imported from the worker it competes for Python's lock with the
    interface, and takes seconds instead of a moment."""
    import sherpa_onnx  # noqa: F401


def load_whisper(model, language: str) -> WhisperEngine:
    """The engine with ``model`` (models.Model) writing ``language`` ("" to
    let Whisper tell). Raises when the engine or the model can't be loaded."""
    import sherpa_onnx

    from . import models

    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = models.path_of(models.VOICE_DETECTOR)
    config.silero_vad.min_silence_duration = 0.5     # a pause between phrases
    config.silero_vad.max_speech_duration = 20.0     # a long one is written in parts
    config.sample_rate = RATE
    detector = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=60)
    recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
        encoder=models.path_of(model.encoder), decoder=models.path_of(model.decoder),
        tokens=models.path_of(model.tokens), language=language, task="transcribe",
        num_threads=max(1, min(4, QThread.idealThreadCount() - 1)))

    def write(samples: List[float]) -> str:
        stream = recognizer.create_stream()
        stream.accept_waveform(RATE, samples)
        recognizer.decode_stream(stream)
        return stream.result.text

    return WhisperEngine(detector, write, window=config.silero_vad.window_size)


class MicrophoneSource(QObject):
    """The system's default microphone, through Qt Multimedia. ``sound``
    carries what it hears, in ``form``; ``failed`` says it stopped."""

    sound = Signal(bytes)
    failed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._source = None
        self._device_io = None
        self.form = AudioForm(RATE, 1, "int16")

    def start(self) -> bool:
        """Start listening; False when there is no microphone."""
        from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSource, QMediaDevices
        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            return False
        wanted = QAudioFormat()
        wanted.setSampleRate(RATE)
        wanted.setChannelCount(1)
        wanted.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        audio_format = wanted if device.isFormatSupported(wanted) else device.preferredFormat()
        kinds = {QAudioFormat.SampleFormat.Int16: "int16", QAudioFormat.SampleFormat.Int32:
                 "int32", QAudioFormat.SampleFormat.Float: "float",
                 QAudioFormat.SampleFormat.UInt8: "uint8"}
        self.form = AudioForm(audio_format.sampleRate(), audio_format.channelCount(),
                              kinds.get(audio_format.sampleFormat(), "int16"))
        self._source = QAudioSource(device, audio_format, self)
        self._device_io = self._source.start()
        if self._device_io is None or self._source.error() != QAudio.Error.NoError:
            return False
        self._device_io.readyRead.connect(self._read)
        self._source.stateChanged.connect(self._state_changed)
        return True

    def stop(self) -> None:
        if self._source is not None:
            self._source.stateChanged.disconnect(self._state_changed)
            self._source.stop()
            self._source = None
            self._device_io = None

    def _read(self) -> None:
        if self._device_io is not None:
            data = bytes(self._device_io.readAll().data())
            if data:
                self.sound.emit(data)

    def _state_changed(self, _state) -> None:
        from PySide6.QtMultimedia import QAudio
        if self._source is not None and self._source.error() != QAudio.Error.NoError:
            self.failed.emit(_("The microphone stopped. Check that it is connected and that "
                               "MyEditor may use it in the system’s privacy settings."))


class _Worker(QObject):
    """The engine, on its own thread."""

    loaded = Signal()
    phrases = Signal(list)
    level = Signal(float)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, make_engine: Callable[[], object], form: AudioForm) -> None:
        super().__init__()
        self._make_engine = make_engine
        self._form = form
        self._engine = None

    @Slot()
    def load(self) -> None:
        try:
            self._engine = self._make_engine()
        except Exception:              # a damaged or missing model, a broken engine
            self.failed.emit(_("The speech model couldn’t be loaded. Remove it in Settings > "
                               "Dictation and download it again."))
            return
        self.loaded.emit()

    @Slot(bytes)
    def hear(self, data: bytes) -> None:
        samples = resample(to_mono(data, self._form), self._form.rate)
        self.level.emit(level_of(samples))
        if self._engine is not None:
            self._emit(self._engine.accept(samples))

    @Slot()
    def finish(self) -> None:
        if self._engine is not None:
            self._emit(self._engine.finish())
        self.finished.emit()

    def _emit(self, written: List[str]) -> None:
        if written:
            self.phrases.emit(written)


class OnDeviceDictation(QObject):
    """One dictation, from start() to stop(). ``ready`` once the model
    listens; ``phrase(str)`` for each phrase written; ``level(float)`` how
    loud the sound is; and ``listening_changed(bool)`` and ``failed(str)``. ``make_engine`` loads
    the engine, on the worker thread (load_whisper); ``make_source`` gives
    the microphone (MicrophoneSource)."""

    ready = Signal()
    phrase = Signal(str)
    level = Signal(float)
    listening_changed = Signal(bool)
    failed = Signal(str)

    # To the worker, across the threads.
    _load = Signal()
    _hear = Signal(bytes)
    _finish = Signal()

    def __init__(self, make_engine: Callable[[], object], *,
                 make_source: Callable[[], object] = MicrophoneSource,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._make_engine = make_engine
        self._make_source = make_source
        self._source = None
        self._thread = None
        self._worker = None
        self._listening = False

    def is_listening(self) -> bool:
        return self._listening

    def start(self) -> None:
        source = self._make_source()
        if not source.start():
            self.failed.emit(_("No microphone was found. Connect one, or let MyEditor use it "
                               "in the system’s privacy settings."))
            return
        self._source = source
        self._thread = QThread(self)
        self._worker = _Worker(self._make_engine, source.form)
        self._worker.moveToThread(self._thread)
        self._load.connect(self._worker.load)
        self._hear.connect(self._worker.hear)
        self._finish.connect(self._worker.finish)
        self._worker.loaded.connect(self.ready)
        self._worker.phrases.connect(self._written)
        self._worker.level.connect(self.level)
        self._worker.failed.connect(self._worker_failed)
        self._worker.finished.connect(self._worker_finished)
        source.sound.connect(self._hear)
        source.failed.connect(self._worker_failed)
        self._thread.start()
        self._load.emit()
        self._set_listening(True)

    def shutdown(self) -> None:
        """Stop at once, the app quitting: the phrase being written is let
        go, and the worker is waited for, so no thread outlives the app."""
        if self._source is not None:
            self._source.sound.disconnect(self._hear)
            self._source.stop()
            self._source = None
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait()
        self._set_listening(False)

    def stop(self) -> None:
        """Stop listening; the phrase being spoken is still written."""
        if self._source is None:
            return
        self._source.sound.disconnect(self._hear)
        self._source.stop()
        self._source = None
        self._finish.emit()

    def _written(self, phrases: list) -> None:
        for text in phrases:
            self.phrase.emit(text)

    def _worker_failed(self, why: str) -> None:
        self.failed.emit(why)
        self.stop()

    def _worker_finished(self) -> None:
        self._thread.quit()
        self._thread.wait()
        self._worker.deleteLater()
        self._worker = None
        self._set_listening(False)

    def _set_listening(self, listening: bool) -> None:
        if listening != self._listening:
            self._listening = listening
            self.listening_changed.emit(listening)
