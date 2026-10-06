# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A spell checker that knows a handful of words, for the spelling tests."""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence

from spelling.backends import AUTOMATIC, SpellBackend, TextCheck

ENGLISH = ("the", "a", "is", "this", "text", "word", "words", "with", "one", "mistake",
           "and", "here", "see", "it", "spelled", "right", "code", "link", "line",
           "hello", "world", "well", "known", "don't", "it's", "naïve", "e-mail", "first",
           "second", "third", "Alice", "read", "more", "at", "or", "on", "in", "of",
           "title", "write", "every", "block", "check", "after", "before", "end")
GERMAN = ("das", "Das", "ist", "ein", "eine", "Haus", "schön", "Straße", "Grüße", "und",
          "mit", "Wörter", "Wort", "geht's", "Adresse", "Mail", "Fehler", "hier", "der",
          "die", "bzw.", "Text", "Zeile")
WORDS = {"en-US": ENGLISH, "de-DE": GERMAN}

_WORD = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*")
# A system checker reads "_" as part of a word, as macOS does: "_Wrot_".
_SYSTEM_WORD = re.compile(r"[^\W\d]+(?:['’\-][^\W\d]+)*")


class FakeBackend(SpellBackend):
    """Checks word by word against a short list, and records every call."""

    name = "fake"

    def __init__(self, words: Optional[Dict[str, Iterable[str]]] = None, *,
                 default: Optional[str] = "en-US") -> None:
        super().__init__()
        self.words = {language: set(known) for language, known in (words or WORDS).items()}
        self.default = default
        self.calls: List[tuple] = []
        self.learned: List[str] = []
        self.ignored: List[str] = []
        self.fail: Optional[str] = None       # the hook that raises next
        self.absent: Optional[str] = None     # open() says the system has no checker
        self.opened = 0
        self.closed = 0

    def _boom(self, hook: str) -> None:
        if self.fail == hook:
            raise RuntimeError(f"{hook} broke")

    def _open(self) -> None:
        from spelling.backends import Unavailable
        self.opened += 1
        if self.absent:
            raise Unavailable(self.absent)
        self._boom("open")

    def _close(self) -> None:
        self.closed += 1

    def _languages(self) -> Sequence[str]:
        self._boom("languages")
        return list(self.words)

    def _default_language(self) -> Optional[str]:
        return self.default

    def knows(self, word: str, language: str) -> bool:
        return (word in self.words.get(language, ()) or word in self.learned
                or word in self.ignored)

    def _check(self, word: str, language: str) -> bool:
        self.calls.append(("check", word, language))
        self._boom("check")
        return self.knows(word, language)

    def _suggestions(self, word: str, language: str) -> Sequence[str]:
        self.calls.append(("suggestions", word, language))
        self._boom("suggestions")
        return sorted(known for known in self.words.get(language, ())
                      if known[:1].lower() == word[:1].lower() and abs(len(known) - len(word)) <= 1)

    def _learn(self, word: str, language: str) -> None:
        self.calls.append(("learn", word, language))
        self._boom("learn")
        self.learned.append(word)

    def _ignore(self, word: str, language: str) -> None:
        self.calls.append(("ignore", word, language))
        self._boom("ignore")
        self.ignored.append(word)

    def checked_words(self) -> List[str]:
        return [call[1] for call in self.calls if call[0] == "check"]


class FakeTextBackend(FakeBackend):
    """Checks a whole paragraph in one pass, the way macOS and Windows do,
    and tells German from English by a few common words."""

    name = "fake-text"

    def _check_text(self, text: str, language: str) -> Optional[TextCheck]:
        self.calls.append(("check_text", text, language))
        self._boom("check_text")
        if language == AUTOMATIC:
            language = self.identify(text)
        languages = list(self.words) if language == AUTOMATIC else [language]
        misspelled = tuple((m.start(), m.end()) for m in _SYSTEM_WORD.finditer(text)
                           if not any(self.knows(m.group(0), each) for each in languages))
        return TextCheck(misspelled, language)

    def _resolve(self, language: str) -> str:
        return language or AUTOMATIC

    @staticmethod
    def identify(text: str) -> str:
        found = set(_WORD.findall(text))
        if found & {"das", "Das", "ist", "und", "ein"}:
            return "de-DE"
        if found & {"the", "is", "and", "this"}:
            return "en-US"
        return AUTOMATIC

    def checked_texts(self) -> List[str]:
        return [call[1] for call in self.calls if call[0] == "check_text"]
