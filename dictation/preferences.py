# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the person chose for dictation (Settings > Dictation), as kept in
the settings file.

- way: the system's dictation or dictation on this computer, where there
  is a choice (Windows); the first way there is when none was made.
- model: Standard or More accurate (models.py).
- language: what is dictated, "" to let Whisper tell. At first the app's
  own language: telling the language from a short phrase can go wrong, so
  naming it writes better.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Tuple

import i18n

from . import models

# The settings file's keys.
_KEYS = {"way": "dictation", "model": "dictation_model", "language": "dictation_language"}

AUTOMATIC = ""

# The languages offered, each named in itself, as a Mac lists them; Whisper
# writes many more, which Automatic finds.
LANGUAGES: Tuple[Tuple[str, str], ...] = (
    ("en", "English"), ("de", "Deutsch"), ("fr", "Français"), ("es", "Español"),
    ("it", "Italiano"), ("pt", "Português"), ("nl", "Nederlands"), ("pl", "Polski"),
    ("cs", "Čeština"), ("sv", "Svenska"), ("da", "Dansk"), ("no", "Norsk"), ("fi", "Suomi"),
    ("tr", "Türkçe"), ("uk", "Українська"), ("ru", "Русский"), ("ar", "العربية"),
    ("hi", "हिन्दी"), ("ja", "日本語"), ("ko", "한국어"), ("zh", "中文"),
)


@dataclass(frozen=True)
class Preferences:
    way: str = ""
    model: str = models.STANDARD
    language: str = AUTOMATIC

    @classmethod
    def from_settings(cls, settings: Dict) -> "Preferences":
        language = settings.get(_KEYS["language"])
        known = {code for code, _name in LANGUAGES} | {AUTOMATIC}
        if language not in known:
            app = i18n.language()
            language = app if app in known else AUTOMATIC
        model = settings.get(_KEYS["model"])
        return cls(way=str(settings.get(_KEYS["way"]) or ""),
                   model=model if model in models.MODELS else models.STANDARD,
                   language=language)

    def to_settings(self) -> Dict[str, str]:
        return {_KEYS[name]: getattr(self, name) for name in _KEYS}

    def with_(self, **changes) -> "Preferences":
        return replace(self, **changes)
