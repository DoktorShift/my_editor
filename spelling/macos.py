# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""macOS spell checking: AppKit's NSSpellChecker.

Reached through rubicon-objc, which calls Objective-C through ctypes in
pure Python (about 240 KB). PyObjC would do the same with 27 MB of
compiled bridges, for the dozen calls made here.

macOS tells the language of each paragraph by itself when the person
keeps "Automatic by Language" in System Settings (the default): then
the default language is ``AUTOMATIC`` and a paragraph is checked the
way TextEdit checks it, in the language macOS finds in it. Learned
words go to the person's own dictionary that every app shares;
ignored words last until the app quits.
"""

from __future__ import annotations

from ctypes import byref
from typing import Dict, List, Optional, Sequence

from . import words
from .backends import AUTOMATIC, SpellBackend, TextCheck, Unavailable, normalize_language

_SPELLING = 1 << 1                      # NSTextCheckingTypeSpelling
_NOT_FOUND = (1 << 63) - 1              # NSNotFound on 64-bit macOS
_UNDETERMINED = {"und", "mul", "zxx"}   # what macOS says when it cannot tell


class MacBackend(SpellBackend):
    name = "macOS"

    def __init__(self) -> None:
        super().__init__()
        self._checker = None
        self._tag = 0
        self._native: Dict[str, str] = {}   # BCP 47 tag -> macOS's name for it

    def _open(self) -> None:
        try:
            from rubicon.objc import NSRange, ObjCClass, ObjCInstance, objc_id
            from rubicon.objc.runtime import autoreleasepool, load_library
        except ImportError as missing:
            raise Unavailable("rubicon-objc is not installed") from missing
        self._rubicon = (NSRange, ObjCInstance, objc_id, autoreleasepool)
        load_library("AppKit")
        spell_checker = ObjCClass("NSSpellChecker")
        self._checker = spell_checker.sharedSpellChecker
        self._tag = int(spell_checker.uniqueSpellDocumentTag())

    def _close(self) -> None:
        if self._checker is not None:
            self._checker.closeSpellDocumentWithTag(self._tag)
        self._checker = None

    # -- languages ----------------------------------------------------------

    def _languages(self) -> Sequence[str]:
        with self._pool():
            names = [str(name) for name in self._checker.availableLanguages]
        for name in names:
            tag = normalize_language(name)
            if tag:
                self._native.setdefault(tag, name)
        return names

    def _default_language(self) -> Optional[str]:
        with self._pool():
            if self._checker.automaticallyIdentifiesLanguages:
                return AUTOMATIC
            # The language chosen in System Settings ("Multilingual" is none).
            return normalize_language(str(self._checker.language)) or AUTOMATIC

    def _resolve(self, language: str) -> str:
        return language or self._default_language() or AUTOMATIC

    def _name(self, language: str) -> Optional[str]:
        """macOS's name for a language; None (nil) lets it tell."""
        if language == AUTOMATIC:
            return None
        if not self._native:
            self._languages()
        if language in self._native:
            return self._native[language]
        primary = language.split("-")[0]
        return self._native.get(primary, language.replace("-", "_"))

    # -- words --------------------------------------------------------------

    def _check(self, word: str, language: str) -> bool:
        with self._pool():
            found = self._checker.checkSpellingOfString(
                word, startingAt=0, language=self._name(language), wrap=False,
                inSpellDocumentWithTag=self._tag, wordCount=None)
            return found.length == 0 or found.location == _NOT_FOUND

    def _suggestions(self, word: str, language: str) -> Sequence[str]:
        NSRange = self._rubicon[0]
        with self._pool():
            guesses = self._checker.guessesForWordRange(
                NSRange(0, _utf16_length(word)), inString=word, language=self._name(language),
                inSpellDocumentWithTag=self._tag)
            return [str(guess) for guess in guesses] if guesses is not None else []

    def _learn(self, word: str, language: str) -> None:
        with self._pool():
            self._checker.learnWord(word)

    def _ignore(self, word: str, language: str) -> None:
        with self._pool():
            self._checker.ignoreWord(word, inSpellDocumentWithTag=self._tag)

    # -- paragraphs -----------------------------------------------------------

    def _check_text(self, text: str, language: str) -> Optional[TextCheck]:
        offsets = words.utf16_offsets(text)
        with self._pool():
            if language == AUTOMATIC:
                units, language = self._check_identifying(text)
            else:
                units = self._check_in(text, self._name(language))
        spans = tuple((words.from_utf16(offsets, start), words.from_utf16(offsets, start + length))
                      for start, length in units)
        return TextCheck(spans, language)

    def _check_identifying(self, text: str):
        """Check a paragraph in the language macOS finds in it, the way
        TextEdit does: one call that also tells the language."""
        NSRange, ObjCInstance, objc_id, _pool = self._rubicon
        orthography = objc_id()
        results = self._checker.checkString(
            text, range=NSRange(0, _utf16_length(text)), types=_SPELLING, options=None,
            inSpellDocumentWithTag=self._tag, orthography=byref(orthography), wordCount=None)
        units = []
        for result in results:
            if int(result.resultType) == _SPELLING:
                found = result.range
                units.append((int(found.location), int(found.length)))
        language = AUTOMATIC
        if orthography.value:
            dominant = ObjCInstance(orthography).dominantLanguage
            dominant = str(dominant() if callable(dominant) else dominant)
            if dominant not in _UNDETERMINED:
                language = normalize_language(dominant) or AUTOMATIC
        return units, language

    def _check_in(self, text: str, name: Optional[str]) -> List[tuple]:
        """Check a paragraph in one given language."""
        units = []
        position = 0
        length = _utf16_length(text)
        while position < length:
            found = self._checker.checkSpellingOfString(
                text, startingAt=position, language=name, wrap=False,
                inSpellDocumentWithTag=self._tag, wordCount=None)
            if found.length == 0 or found.location == _NOT_FOUND:
                break
            units.append((int(found.location), int(found.length)))
            position = int(found.location) + int(found.length)
        return units

    def _pool(self):
        """Release what AppKit hands back right after each call, also
        where no event loop drains it (tests, the first call at start)."""
        return self._rubicon[3]()


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2
