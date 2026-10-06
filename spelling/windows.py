# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Windows spell checking: the Spell Checking API (Windows 8 and later).

The system's own checker, the one Edge and Mail use: the dictionaries
of the languages installed in Windows, words added with Add to
Dictionary kept in the person's dictionary, and a whole paragraph
checked in one call. Reached through ctypes alone: the API is three
small COM interfaces, and comtypes would add a megabyte and code
generated at run time for them. The interfaces and their order are
those of spellcheck.h.

COM objects belong to the thread that created them: the backend's
guard lets only that thread call (the app's main thread).
"""

from __future__ import annotations

import ctypes
import sys
import uuid
from ctypes import POINTER, byref, c_int, c_long, c_ubyte, c_uint16, c_uint32, c_ulong, c_void_p, c_wchar_p
from typing import Dict, List, Optional, Sequence

from . import words
from .backends import SpellBackend, TextCheck, Unavailable, normalize_language

# Elsewhere than Windows (the tests' stand-in COM objects) there is one
# calling convention, as on 64-bit Windows.
_FUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)

S_OK = 0
S_FALSE = 1
_RPC_E_CHANGED_MODE = -2147417850       # 0x80010106: COM set up otherwise on this thread
_REGDB_E_CLASSNOTREG = -2147221164      # 0x80040154: no spell checking in this Windows
_COINIT_APARTMENTTHREADED = 0x2
_CLSCTX_INPROC_SERVER = 0x1
_CORRECTIVE_ACTION_DELETE = 3           # a repeated word ("the the"), not a misspelling


class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", c_uint16), ("Data3", c_uint16),
                ("Data4", c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "GUID":
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


CLSID_SpellCheckerFactory = GUID.of("7AB36653-1796-484B-BDFA-E74F1DB7C1DC")
IID_ISpellCheckerFactory = GUID.of("8E018A9D-2415-4677-BF08-794EA61F94BB")


def _method(index: int, *argtypes):
    """Call the ``index``-th function of a COM object's table."""
    prototype = _FUNCTYPE(c_long, c_void_p, *argtypes)

    def call(this, *args) -> int:
        table = ctypes.cast(this, POINTER(POINTER(c_void_p)))[0]
        return prototype(table[index])(this, *args)

    return call


_release = _method(2)                                                     # IUnknown::Release
_enum_string_next = _method(3, c_ulong, POINTER(c_void_p), POINTER(c_ulong))  # IEnumString::Next
_supported_languages = _method(3, POINTER(c_void_p))                   # ISpellCheckerFactory
_is_supported = _method(4, c_wchar_p, POINTER(c_int))
_create_spell_checker = _method(5, c_wchar_p, POINTER(c_void_p))
_check = _method(4, c_wchar_p, POINTER(c_void_p))                      # ISpellChecker
_suggest = _method(5, c_wchar_p, POINTER(c_void_p))
_add = _method(6, c_wchar_p)
_ignore = _method(7, c_wchar_p)
_next_error = _method(3, POINTER(c_void_p))                            # IEnumSpellingError
_start_index = _method(3, POINTER(c_ulong))                            # ISpellingError
_length = _method(4, POINTER(c_ulong))
_corrective_action = _method(5, POINTER(c_int))


def _ok(result: int) -> int:
    if result < 0:
        raise OSError(f"Windows spell checking failed with {result & 0xFFFFFFFF:#010x}")
    return result


class Ole:
    """The few ole32 calls the backend makes (the tests bring their own)."""

    def __init__(self) -> None:
        ole32 = ctypes.WinDLL("ole32")
        self._initialize = ole32.CoInitializeEx
        self._initialize.argtypes = [c_void_p, c_ulong]
        self._initialize.restype = c_long
        self._uninitialize = ole32.CoUninitialize
        self._uninitialize.argtypes = []
        self._uninitialize.restype = None
        self._create = ole32.CoCreateInstance
        self._create.argtypes = [POINTER(GUID), c_void_p, c_ulong, POINTER(GUID),
                                 POINTER(c_void_p)]
        self._create.restype = c_long
        self._free = ole32.CoTaskMemFree
        self._free.argtypes = [c_void_p]
        self._free.restype = None

    def initialize(self) -> bool:
        """Set up COM on this thread. Returns whether ``uninitialize``
        must balance it (not when Qt set it up otherwise first)."""
        result = self._initialize(None, _COINIT_APARTMENTTHREADED)
        if result == _RPC_E_CHANGED_MODE:
            return False
        _ok(result)
        return True

    def uninitialize(self) -> None:
        self._uninitialize()

    def create_factory(self) -> c_void_p:
        factory = c_void_p()
        result = self._create(byref(CLSID_SpellCheckerFactory), None, _CLSCTX_INPROC_SERVER,
                              byref(IID_ISpellCheckerFactory), byref(factory))
        if result == _REGDB_E_CLASSNOTREG:
            raise Unavailable("this version of Windows has no spell checking")
        _ok(result)
        return factory

    def free(self, address: int) -> None:
        self._free(address)


class WindowsBackend(SpellBackend):
    name = "Windows"

    def __init__(self, ole: Optional[Ole] = None) -> None:
        super().__init__()
        self._ole = ole
        self._factory: Optional[c_void_p] = None
        self._checkers: Dict[str, Optional[c_void_p]] = {}
        self._native: Dict[str, str] = {}       # BCP 47 tag -> Windows's spelling of it
        self._balance = False

    def _open(self) -> None:
        if self._ole is None:
            if sys.platform != "win32":
                raise Unavailable("Windows spell checking exists only on Windows")
            self._ole = Ole()
        self._balance = self._ole.initialize()
        self._factory = self._ole.create_factory()

    def _close(self) -> None:
        for checker in self._checkers.values():
            if checker:
                _release(checker)
        self._checkers.clear()
        if self._factory:
            _release(self._factory)
        self._factory = None
        if self._balance:
            self._ole.uninitialize()
            self._balance = False

    def _languages(self) -> Sequence[str]:
        found = c_void_p()
        _ok(_supported_languages(self._factory, byref(found)))
        try:
            names = self._strings(found)
        finally:
            _release(found)
        for name in names:
            tag = normalize_language(name)
            if tag:
                self._native.setdefault(tag, name)
        return names

    def _check(self, word: str, language: str) -> bool:
        return not self._errors(word, language)

    def _check_text(self, text: str, language: str) -> Optional[TextCheck]:
        offsets = words.utf16_offsets(text)
        spans = tuple((words.from_utf16(offsets, start), words.from_utf16(offsets, start + length))
                      for start, length, action in self._errors(text, language)
                      if action != _CORRECTIVE_ACTION_DELETE)
        return TextCheck(spans, language)

    def _suggestions(self, word: str, language: str) -> Sequence[str]:
        checker = self._checker(language)
        if not checker:
            return []
        found = c_void_p()
        result = _ok(_suggest(checker, word, byref(found)))
        try:
            # S_FALSE: the word is spelled right.
            return self._strings(found) if result == S_OK else []
        finally:
            if found.value:
                _release(found)

    def _learn(self, word: str, language: str) -> Optional[bool]:
        checker = self._checker(language)
        if not checker:
            return False
        _ok(_add(checker, word))
        return None

    def _ignore(self, word: str, language: str) -> Optional[bool]:
        checker = self._checker(language)
        if not checker:
            return False
        _ok(_ignore(checker, word))
        return None

    # -- inside ---------------------------------------------------------------

    def _checker(self, language: str) -> Optional[c_void_p]:
        """The spell checker of one language, made once; None when Windows
        cannot check that language (its words then all count as right)."""
        if language in self._checkers:
            return self._checkers[language]
        if not self._native:
            self._languages()
        name = self._native.get(language, language)
        supported = c_int()
        _ok(_is_supported(self._factory, name, byref(supported)))
        checker: Optional[c_void_p] = None
        if supported.value:
            checker = c_void_p()
            _ok(_create_spell_checker(self._factory, name, byref(checker)))
        self._checkers[language] = checker
        return checker

    def _errors(self, text: str, language: str) -> List[tuple]:
        """(start, length, corrective action) of each error Windows finds,
        in UTF-16 units."""
        checker = self._checker(language)
        if not checker:
            return []
        found = c_void_p()
        _ok(_check(checker, text, byref(found)))
        errors = []
        try:
            while True:
                error = c_void_p()
                if _ok(_next_error(found, byref(error))) != S_OK or not error.value:
                    break
                try:
                    start, length, action = c_ulong(), c_ulong(), c_int()
                    _ok(_start_index(error, byref(start)))
                    _ok(_length(error, byref(length)))
                    _ok(_corrective_action(error, byref(action)))
                    errors.append((start.value, length.value, action.value))
                finally:
                    _release(error)
        finally:
            _release(found)
        return errors

    def _strings(self, found: c_void_p) -> List[str]:
        """Every string of an IEnumString, each freed after reading."""
        out: List[str] = []
        while True:
            item = c_void_p()
            fetched = c_ulong()
            result = _ok(_enum_string_next(found, 1, byref(item), byref(fetched)))
            if fetched.value and item.value:
                try:
                    out.append(ctypes.wstring_at(item.value))
                finally:
                    self._ole.free(item.value)
            if result != S_OK or not fetched.value:
                return out
