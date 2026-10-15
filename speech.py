# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Edit > Speech: Start Speaking reads the selection, or the whole document,
in the system's voice; Stop Speaking stops, as in Apple's Edit menus.

Hearing a draft read aloud catches what reading it over misses. The voice
is the system's, through Qt's text to speech: AVSpeechSynthesizer on a Mac,
the Windows voices, and speech-dispatcher on Linux, where the app says so
when that service is missing. Nothing leaves the computer.
"""

from __future__ import annotations

import sys
from typing import Optional

from PySide6.QtCore import QObject, Signal

from i18n import _

# Qt's engine for tests: it "speaks" at once, without a sound.
TEST_ENGINE = "mock"
# What a document holds besides words: a picture, the end of a paragraph.
_OBJECT, _PARAGRAPH, _LINE = "\ufffc", "\u2029", "\u2028"


def spoken_text(text: str) -> str:
    """``text`` as it is read: pictures left out, paragraphs as lines."""
    return (text.replace(_OBJECT, "").replace(_PARAGRAPH, "\n").replace(_LINE, "\n")
            .strip())


class Speech(QObject):
    """The app's voice. ``speaking_changed(bool)`` as it starts and stops;
    ``failed(str)`` says why it could not speak. ``engine`` names Qt's
    engine (the system's when None). The engine is made at the first word,
    so an app that never speaks loads no voice."""

    speaking_changed = Signal(bool)
    failed = Signal(str)

    def __init__(self, engine: Optional[str] = None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._engine_name = engine
        self._engine = None
        self._speaking = False

    def is_speaking(self) -> bool:
        return self._speaking

    def speak(self, text: str) -> None:
        """Read ``text`` aloud from its start, instead of what was being read."""
        words = spoken_text(text)
        engine = self._ensure_engine() if words else None
        if engine is None:
            return
        engine.stop()
        engine.say(words)

    def stop(self) -> None:
        if self._engine is not None:
            self._engine.stop()

    def _ensure_engine(self):
        """Qt's engine, made now if it is not yet; None when it can't start."""
        from PySide6.QtTextToSpeech import QTextToSpeech
        if self._engine is None:
            engine = (QTextToSpeech(self._engine_name, self) if self._engine_name
                      else QTextToSpeech(self))
            engine.stateChanged.connect(self._state_changed)
            engine.errorOccurred.connect(lambda _reason, why: self.failed.emit(why))
            self._engine = engine
        if self._engine.state() == QTextToSpeech.State.Error:
            self.failed.emit(_no_voice())
            return None
        return self._engine

    def _state_changed(self, state) -> None:
        from PySide6.QtTextToSpeech import QTextToSpeech
        speaking = state in (QTextToSpeech.State.Speaking, QTextToSpeech.State.Synthesizing)
        if speaking != self._speaking:
            self._speaking = speaking
            self.speaking_changed.emit(speaking)


def _no_voice() -> str:
    if sys.platform.startswith("linux"):
        return _("Speaking needs speech-dispatcher, the system’s speech service. Install it "
                 "with your system’s software manager.")
    return _("This computer’s voice could not be started.")
