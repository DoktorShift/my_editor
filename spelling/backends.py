# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each system's own spell checker behind one small interface.

A backend answers five questions about words (is it spelled right, what
might be meant, learn it, ignore it, which languages are there) and one
about a whole paragraph, for the systems that check text in one pass.
Every public method is safe to call at any time: a backend that fails
does not raise into the caller. It logs one warning, says why in
``problem``, and from then on reports itself unavailable and answers
neutrally (every word is spelled right, no suggestions), so a broken
system checker never underlines the whole document.

A backend belongs to the thread that created it (Windows COM objects
must stay on their thread, and AppKit expects its main thread). A call
from another thread gets the neutral answer and a warning.

Languages are BCP 47 tags (``de-DE``, ``en``), whatever the system calls
them; ``AUTOMATIC`` asks the system to tell the language from the text,
which only macOS can do.
"""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple, TypeVar

_log = logging.getLogger(__name__)

AUTOMATIC = "auto"
"""Let the system tell the language from the text. Never a BCP 47 tag
(a primary language subtag has two, three or five to eight letters)."""

MAX_WORD_LENGTH = 100
"""Longer runs of letters are not words a person typed (a hash, a key);
they are never sent to a system checker. Windows refuses words over 128
characters with an error."""

Span = Tuple[int, int]
_T = TypeVar("_T")


@dataclass(frozen=True)
class TextCheck:
    """What a system checker found in one paragraph."""

    misspelled: Tuple[Span, ...]
    """(start, end) of each misspelled word, as indices into the text."""
    language: str
    """The language the text was checked in; ``AUTOMATIC`` when the
    system could not tell."""


class Unavailable(Exception):
    """The system has no spell checker this backend can use (a missing
    library, no dictionary). Not a failure: said once, without a trace."""


def valid_word(word: str) -> bool:
    """Whether ``word`` may be handed to a system checker at all."""
    return (0 < len(word) <= MAX_WORD_LENGTH
            and not any(ch.isspace() or ord(ch) < 32 for ch in word))


class SpellBackend:
    """A system spell checker. Subclasses implement the ``_`` hooks; the
    public methods wrap them so that nothing ever raises."""

    name = "none"

    def __init__(self) -> None:
        self._thread = threading.get_ident()
        self._opened = False
        self._problem: Optional[str] = None
        self._warned_thread = False

    # -- public interface: never raises --------------------------------------

    @property
    def problem(self) -> Optional[str]:
        """Why the backend is unavailable, for the log and for tests; None
        while it works (or before it was first asked)."""
        return self._problem

    def is_available(self) -> bool:
        """Whether the system checker works. Opens it on first use."""
        return self._call(lambda: True, False)

    def languages(self) -> List[str]:
        """The languages the system can check, as BCP 47 tags, in the
        system's order."""
        return self._call(lambda: _unique_tags(self._languages()), [])

    def default_language(self) -> Optional[str]:
        """The language to check in when nobody chose one: ``AUTOMATIC``
        where the system tells languages apart by itself, otherwise the
        system language (the closest one the system can check). None
        when there is no language at all."""
        return self._call(self._default_language, None)

    def check(self, word: str, language: str) -> bool:
        """Whether ``word`` is spelled right in ``language``. A word that
        cannot be checked counts as right."""
        if not valid_word(word):
            return True
        return self._call(lambda: self._check(word, self._resolve(language)), True)

    def suggestions(self, word: str, language: str) -> List[str]:
        """What ``word`` might have meant, best first."""
        if not valid_word(word):
            return []
        return self._call(lambda: _unique_words(self._suggestions(word, self._resolve(language)),
                                                word), [])

    def learn(self, word: str, language: str) -> bool:
        """Add ``word`` to the person's own dictionary for good (the
        system's, so other apps learn it too where the system shares
        one). Returns whether the system took it."""
        if not valid_word(word):
            return False
        return self._call(lambda: self._learn(word, self._resolve(language)) is not False, False)

    def ignore(self, word: str, language: str) -> bool:
        """Accept ``word`` until the app quits. Returns whether the
        system took it."""
        if not valid_word(word):
            return False
        return self._call(lambda: self._ignore(word, self._resolve(language)) is not False, False)

    def check_text(self, text: str, language: str) -> Optional[TextCheck]:
        """Check a whole paragraph in one pass, for systems that can
        (macOS, Windows). None when this backend checks word by word;
        the caller then asks ``check`` for each word."""
        fallback = TextCheck((), language)
        return self._call(lambda: self._check_text(text, self._resolve(language)), fallback)

    def close(self) -> None:
        """Let go of the system checker (at quit, or in tests)."""
        if self._opened and self._on_own_thread():
            try:
                self._close()
            except Exception:  # noqa: BLE001, closing must never stop the app
                _log.warning("Closing %s spell checking failed", self.name, exc_info=True)
        self._opened = False

    # -- hooks for subclasses -------------------------------------------------

    def _open(self) -> None:
        """Reach the system checker; raise ``Unavailable`` when there is none."""

    def _close(self) -> None:
        """Release what ``_open`` took."""

    def _languages(self) -> Sequence[str]:
        raise NotImplementedError

    def _default_language(self) -> Optional[str]:
        return best_language(system_languages(), self.languages())

    def _check(self, word: str, language: str) -> bool:
        raise NotImplementedError

    def _suggestions(self, word: str, language: str) -> Sequence[str]:
        raise NotImplementedError

    def _learn(self, word: str, language: str) -> Optional[bool]:
        raise NotImplementedError

    def _ignore(self, word: str, language: str) -> Optional[bool]:
        raise NotImplementedError

    def _check_text(self, text: str, language: str) -> Optional[TextCheck]:
        return None

    def _resolve(self, language: str) -> str:
        """The language a backend without automatic identification checks
        in when asked for ``AUTOMATIC`` (or for nothing)."""
        if language and language != AUTOMATIC:
            return language
        return self._default_language() or ""

    # -- the guard ------------------------------------------------------------

    def _on_own_thread(self) -> bool:
        if threading.get_ident() == self._thread:
            return True
        if not self._warned_thread:
            self._warned_thread = True
            _log.warning("%s spell checking was asked from another thread; it answers "
                         "only on the thread that created it", self.name)
        return False

    def _call(self, action: Callable[[], _T], fallback: _T) -> _T:
        if self._problem is not None or not self._on_own_thread():
            return fallback
        try:
            if not self._opened:
                self._open()
                self._opened = True
            return action()
        except Unavailable as absent:
            self._problem = str(absent) or "unavailable"
            _log.warning("Spell checking is unavailable (%s): %s", self.name, self._problem)
        except Exception as error:  # noqa: BLE001, a system checker must never break the app
            self._problem = f"{type(error).__name__}: {error}"
            _log.warning("Spell checking (%s) stopped working", self.name, exc_info=True)
        self.close()
        return fallback


class NullBackend(SpellBackend):
    """Where the system has no spell checker the app can use."""

    name = "none"

    def __init__(self, reason: str = "this system has no spell checker MyEditor can use") -> None:
        super().__init__()
        self._reason = reason

    def _open(self) -> None:
        raise Unavailable(self._reason)


def create_backend() -> SpellBackend:
    """The spell checker of the system the app runs on."""
    if sys.platform == "darwin":
        from .macos import MacBackend
        return MacBackend()
    if sys.platform == "win32":
        from .windows import WindowsBackend
        return WindowsBackend()
    return NullBackend()


# -- languages --------------------------------------------------------------

def normalize_language(tag: str) -> Optional[str]:
    """A language name as a BCP 47 tag: ``de_DE.UTF-8`` and ``de-de``
    become ``de-DE``, ``zh_hans_cn`` becomes ``zh-Hans-CN``. None for
    names that are no language (``C``, ``POSIX``, ``Multilingual``)."""
    base = (tag or "").strip().split(".")[0].split("@")[0].replace("_", "-")
    parts = [part for part in base.split("-") if part]
    if not parts:
        return None
    language = parts[0].lower()
    if not (2 <= len(language) <= 3 and language.isascii() and language.isalpha()):
        return None
    out = [language]
    for part in parts[1:]:
        if not (part.isascii() and part.isalnum()):
            return None
        if len(part) == 2 and part.isalpha():
            out.append(part.upper())                  # region
        elif len(part) == 4 and part.isalpha():
            out.append(part.title())                  # script
        else:
            out.append(part.lower())                  # numeric region, variant
    return "-".join(out)


def system_languages() -> List[str]:
    """The person's languages, most preferred first, as BCP 47 tags."""
    try:
        from PySide6.QtCore import QLocale
        locale = QLocale.system()
        names = list(locale.uiLanguages()) + [locale.name()]
    except Exception:  # noqa: BLE001, no locale is no reason to fail
        names = []
    return _unique_tags(names)


def best_language(preferred: Sequence[str], available: Sequence[str]) -> Optional[str]:
    """The first preferred language the system can check, or the closest
    one (``de-CH`` falls back to ``de``, then ``de-DE``), then English,
    then whatever there is. None when nothing is available."""
    offered = _unique_tags(available)
    if not offered:
        return None
    for tag in _unique_tags(list(preferred) + ["en-US"]):
        if tag in offered:
            return tag
        language = tag.split("-")[0]
        same = [other for other in offered if other.split("-")[0] == language]
        if same:
            return _closest(tag, same)
    return offered[0]


def _closest(tag: str, same_language: List[str]) -> str:
    language = tag.split("-")[0]
    region = _region(tag)
    if region:
        for other in same_language:
            if _region(other) == region:
                return other
    if language in same_language:
        return language
    main = _main_region(language)
    if main:
        for other in same_language:
            if _region(other) == main:
                return other
    return same_language[0]


def _region(tag: str) -> Optional[str]:
    for part in tag.split("-")[1:]:
        if (len(part) == 2 and part.isalpha()) or (len(part) == 3 and part.isdigit()):
            return part.upper()
    return None


def _main_region(language: str) -> Optional[str]:
    """Where a language is mostly spoken (``de`` -> ``DE``, ``en`` ->
    ``US``, ``pt`` -> ``BR``), from Qt's locale data."""
    try:
        from PySide6.QtCore import QLocale
        name = QLocale(language).name()
    except Exception:  # noqa: BLE001
        return None
    head, _sep, region = name.partition("_")
    return region.upper() if head == language and len(region) == 2 else None


def _unique_tags(names) -> List[str]:
    out: List[str] = []
    for name in names:
        tag = normalize_language(str(name))
        if tag and tag not in out:
            out.append(tag)
    return out


def _unique_words(words, original: str) -> List[str]:
    out: List[str] = []
    for word in words:
        word = str(word)
        if word and word != original and word not in out:
            out.append(word)
    return out
