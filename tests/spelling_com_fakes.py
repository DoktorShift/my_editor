# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stand-ins for the Windows Spell Checking API's COM objects.

Each is a real function table of ctypes callbacks, laid out by the
backend's own lists of each interface's functions (whose order
tests/test_spelling_windows.py pins to spellcheck.h), so the backend's
calls (arguments, strings to free, objects to release) run on every
platform. Error positions count UTF-16 units, as Windows does.
"""

from __future__ import annotations

import ctypes
import re
from ctypes import POINTER, c_int, c_long, c_ulong, c_void_p, c_wchar_p
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from spelling.backends import Unavailable
from spelling.windows import (
    ENUM_SPELLING_ERROR, ENUM_STRING, S_FALSE, S_OK, SPELL_CHECKER, SPELL_CHECKER_2,
    SPELL_CHECKER_FACTORY, SPELLING_ERROR, _FUNCTYPE,
)

E_NOINTERFACE = -2147467262
E_FAIL = -2147467259
GET_SUGGESTIONS, DELETE = 1, 3

_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


class Registry:
    """Every stand-in object and string handed out, to see that the
    backend lets go of each exactly once."""

    def __init__(self) -> None:
        self.objects: List["FakeCom"] = []
        self.strings: Dict[int, ctypes.Array] = {}
        self.freed: List[int] = []

    def string(self, text: str) -> int:
        buffer = ctypes.create_unicode_buffer(text)
        address = ctypes.addressof(buffer)
        self.strings[address] = buffer
        return address

    def free(self, address: int) -> None:
        self.freed.append(address)
        del self.strings[address]          # a second free of one string fails here

    def held(self) -> List[str]:
        return [thing.name for thing in self.objects if thing.references > 0]


class FakeCom:
    """A COM object made in Python with the functions of ``interface``
    (their names, in order). ``functions`` gives some of them, by name,
    as an ``(argument types, function)`` pair returning an HRESULT; the
    others fail. IUnknown's three are its own, QueryInterface answered by
    ``query``."""

    def __init__(self, registry: Registry, name: str, interface: Sequence[str],
                 functions: Dict[str, tuple], query=None) -> None:
        self.name = name
        self.references = 1
        registry.objects.append(self)
        functions = {"QueryInterface": ((c_void_p, POINTER(c_void_p)),
                                        query or (lambda iid, out: E_NOINTERFACE)),
                     "AddRef": ((), self._add_ref), "Release": ((), self._release),
                     **functions}
        unknown = set(functions) - set(interface)
        assert not unknown, f"{name} has no {sorted(unknown)}"
        table = [functions.get(function, ((), lambda: E_FAIL)) for function in interface]
        self._callbacks = [_FUNCTYPE(c_long, c_void_p, *argtypes)(self._method(function))
                           for argtypes, function in table]
        self._table = (c_void_p * len(self._callbacks))(
            *[ctypes.cast(callback, c_void_p).value for callback in self._callbacks])
        self._object = (c_void_p * 1)(ctypes.addressof(self._table))
        self.pointer = ctypes.addressof(self._object)

    @staticmethod
    def _method(function: Callable) -> Callable:
        def call(_this, *args):
            return function(*args)
        return call

    def _add_ref(self) -> int:
        self.references += 1
        return self.references

    def _release(self) -> int:
        self.references -= 1
        return self.references


def _put(value) -> Callable:
    def method(out):
        out[0] = value
        return S_OK
    return method


def string_enum(registry: Registry, items: Iterable[str]) -> FakeCom:
    pending = list(items)

    def next_(count, out, fetched):
        if not pending:
            fetched[0] = 0
            return S_FALSE
        out[0] = registry.string(pending.pop(0))
        fetched[0] = 1
        return S_OK

    return FakeCom(registry, "IEnumString", ENUM_STRING,
                   {"Next": ((c_ulong, POINTER(c_void_p), POINTER(c_ulong)), next_)})


def error(registry: Registry, start: int, length: int, action: int) -> FakeCom:
    return FakeCom(registry, "ISpellingError", SPELLING_ERROR, {
        "StartIndex": ((POINTER(c_ulong),), _put(start)),
        "Length": ((POINTER(c_ulong),), _put(length)),
        "CorrectiveAction": ((POINTER(c_int),), _put(action)),
    })


def error_enum(registry: Registry, errors: Iterable[tuple]) -> FakeCom:
    pending = list(errors)

    def next_(out):
        if not pending:
            out[0] = None
            return S_FALSE
        out[0] = error(registry, *pending.pop(0)).pointer
        return S_OK

    return FakeCom(registry, "IEnumSpellingError", ENUM_SPELLING_ERROR,
                   {"Next": ((POINTER(c_void_p),), next_)})


class FakeChecker:
    """ISpellChecker for one language."""

    def __init__(self, registry: Registry, language: str, known: Iterable[str]) -> None:
        self.registry = registry
        self.language = language
        self.known = set(known)
        self.added: List[str] = []
        self.ignored: List[str] = []
        self.fail_check = False
        self.removed: List[str] = []
        self.comprehensive_checks: List[str] = []
        self.windows_8 = False                  # no ISpellChecker2 (no Remove)
        self.second: Optional[FakeCom] = None
        self.com = FakeCom(registry, f"ISpellChecker {language}", SPELL_CHECKER,
                           self._functions(), query=self._query)

    def _functions(self) -> Dict[str, tuple]:
        return {"Check": ((c_wchar_p, POINTER(c_void_p)), self._check),
                "Suggest": ((c_wchar_p, POINTER(c_void_p)), self._suggest),
                "Add": ((c_wchar_p,), self._add),
                "Ignore": ((c_wchar_p,), self._ignore),
                "ComprehensiveCheck": ((c_wchar_p, POINTER(c_void_p)), self._comprehensive)}

    def _query(self, iid, out):
        """ISpellChecker2 (Windows 10): ISpellChecker's functions, then Remove."""
        if self.windows_8:
            return E_NOINTERFACE
        if self.second is None:
            self.second = FakeCom(self.registry, f"ISpellChecker2 {self.language}",
                                  SPELL_CHECKER_2,
                                  {**self._functions(), "Remove": ((c_wchar_p,), self._remove)})
        else:
            self.second._add_ref()
        out[0] = self.second.pointer
        return S_OK

    def _comprehensive(self, text, out):
        """Called by nothing the backend does: a call landing here is a
        call meant for another slot."""
        self.comprehensive_checks.append(text)
        return E_FAIL

    def _remove(self, word):
        self.removed.append(word)
        if word in self.added:
            self.added.remove(word)
        return S_OK

    def right(self, word: str) -> bool:
        return word in self.known or word in self.added or word in self.ignored

    def _check(self, text, out):
        if self.fail_check:
            return E_FAIL
        errors = []
        previous = None
        for match in _WORD.finditer(text):
            word = match.group(0)
            start, length = _units(text[:match.start()]), _units(word)
            if word == previous:
                errors.append((start, length, DELETE))
            elif not self.right(word):
                errors.append((start, length, GET_SUGGESTIONS))
            previous = word
        out[0] = error_enum(self.registry, errors).pointer
        return S_OK

    def _suggest(self, word, out):
        if self.right(word):
            out[0] = string_enum(self.registry, [word]).pointer
            return S_FALSE
        found = sorted(known for known in self.known if known[:2] == word[:2])
        out[0] = string_enum(self.registry, found).pointer
        return S_OK

    def _add(self, word):
        self.added.append(word)
        return S_OK

    def _ignore(self, word):
        self.ignored.append(word)
        return S_OK


class FakeFactory:
    """ISpellCheckerFactory with a few languages."""

    def __init__(self, registry: Registry, words: Dict[str, Iterable[str]]) -> None:
        self.registry = registry
        self.words = words
        self.checkers: Dict[str, FakeChecker] = {}
        self.com = FakeCom(registry, "ISpellCheckerFactory", SPELL_CHECKER_FACTORY, {
            "SupportedLanguages": ((POINTER(c_void_p),), self._languages),
            "IsSupported": ((c_wchar_p, POINTER(c_int)), self._supported),
            "CreateSpellChecker": ((c_wchar_p, POINTER(c_void_p)), self._create),
        })

    def _languages(self, out):
        out[0] = string_enum(self.registry, list(self.words)).pointer
        return S_OK

    def _supported(self, language, out):
        out[0] = 1 if language in self.words else 0
        return S_OK

    def _create(self, language, out):
        if language not in self.words:
            return E_FAIL
        checker = FakeChecker(self.registry, language, self.words[language])
        self.checkers[language] = checker
        out[0] = checker.com.pointer
        return S_OK


class FakeOle:
    """The ole32 calls, answered by the stand-ins."""

    def __init__(self, words: Dict[str, Iterable[str]], *, registered: bool = True) -> None:
        self.registry = Registry()
        self.factory = FakeFactory(self.registry, words)
        self.registered = registered
        self.initialized = 0
        self.uninitialized = 0

    def initialize(self) -> bool:
        self.initialized += 1
        return True

    def uninitialize(self) -> None:
        self.uninitialized += 1

    def create_factory(self) -> c_void_p:
        if not self.registered:
            raise Unavailable("this version of Windows has no spell checking")
        return c_void_p(self.factory.com.pointer)

    def free(self, address: int) -> None:
        self.registry.free(address)
