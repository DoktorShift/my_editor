# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Linux spell checking: Enchant, with the dictionaries the system has.

Enchant is the spell checking library Linux desktops share. It finds a
provider for each language among those installed (Hunspell, Nuspell,
Aspell), and keeps the words a person adds in their own word list
(``~/.config/enchant``), shared with the other apps that use it.

The library is the system's own, reached through ctypes: libenchant-2
is a small C library, and pyenchant would only wrap the same calls. It
is not bundled with the app, because Enchant finds its providers and
dictionaries next to where it was installed; where it is missing,
spell checking is unavailable and the Spelling commands are dimmed.
The .deb recommends it, with an English dictionary.

Enchant checks word by word, so the caller asks for each word.
"""

from __future__ import annotations

import ctypes
from ctypes import CFUNCTYPE, POINTER, byref, c_char_p, c_int, c_size_t, c_ssize_t, c_void_p
from typing import Dict, List, Optional, Sequence

from .backends import SpellBackend, Unavailable, normalize_language

# The library's file names (enchant 2), tried in order. Named here rather
# than written into the call, so that the installer build does not find
# and bundle the build machine's copy.
LIBRARY_NAMES = ("libenchant-2.so.2", "libenchant-2.so")

_DESCRIBE = CFUNCTYPE(None, c_char_p, c_char_p, c_char_p, c_char_p, c_void_p)


class EnchantBackend(SpellBackend):
    name = "Enchant"

    def __init__(self, library_names: Sequence[str] = LIBRARY_NAMES) -> None:
        super().__init__()
        self._names = tuple(library_names)
        self._lib = None
        self._broker: Optional[int] = None
        self._dicts: Dict[str, Optional[int]] = {}
        self._native: Dict[str, str] = {}      # BCP 47 tag -> Enchant's name for it

    def _open(self) -> None:
        lib = _load(self._names)
        broker = lib.enchant_broker_init()
        if not broker:
            raise Unavailable("Enchant could not start")
        self._lib, self._broker = lib, broker
        if not self._languages():
            self._close()
            raise Unavailable("Enchant has no dictionaries (install hunspell-en-us, "
                              "or the dictionary of your language)")

    def _close(self) -> None:
        if self._lib is None:
            return
        for dictionary in self._dicts.values():
            if dictionary:
                self._lib.enchant_broker_free_dict(self._broker, dictionary)
        self._dicts.clear()
        if self._broker:
            self._lib.enchant_broker_free(self._broker)
        self._broker = None
        self._lib = None

    def _languages(self) -> Sequence[str]:
        names: List[str] = []

        def describe(tag, _provider, _description, _file, _data):
            names.append(tag.decode("utf-8", "replace"))

        callback = _DESCRIBE(describe)
        self._lib.enchant_broker_list_dicts(self._broker, callback, None)
        for name in names:
            tag = normalize_language(name)
            if tag:
                self._native.setdefault(tag, name)
        return names

    def _check(self, word: str, language: str) -> bool:
        dictionary = self._dict(language)
        if not dictionary:
            return True
        data = word.encode("utf-8")
        result = self._lib.enchant_dict_check(dictionary, data, len(data))
        if result < 0:
            raise OSError(self._error(dictionary))
        return result == 0

    def _suggestions(self, word: str, language: str) -> Sequence[str]:
        dictionary = self._dict(language)
        if not dictionary:
            return []
        data = word.encode("utf-8")
        count = c_size_t()
        found = self._lib.enchant_dict_suggest(dictionary, data, len(data), byref(count))
        if not found:
            return []
        try:
            return [found[index].decode("utf-8", "replace") for index in range(count.value)]
        finally:
            self._lib.enchant_dict_free_string_list(dictionary, found)

    def _learn(self, word: str, language: str) -> Optional[bool]:
        dictionary = self._dict(language)
        if not dictionary:
            return False
        data = word.encode("utf-8")
        self._lib.enchant_dict_add(dictionary, data, len(data))
        return None

    def _ignore(self, word: str, language: str) -> Optional[bool]:
        dictionary = self._dict(language)
        if not dictionary:
            return False
        data = word.encode("utf-8")
        self._lib.enchant_dict_add_to_session(dictionary, data, len(data))
        return None

    def _dict(self, language: str) -> Optional[int]:
        """The dictionary of one language, asked for once; None when
        Enchant has none for it (its words then all count as right)."""
        if language not in self._dicts:
            name = self._native.get(language, language.replace("-", "_"))
            self._dicts[language] = self._lib.enchant_broker_request_dict(
                self._broker, name.encode("utf-8")) or None
        return self._dicts[language]

    def _error(self, dictionary: int) -> str:
        message = self._lib.enchant_dict_get_error(dictionary)
        return message.decode("utf-8", "replace") if message else "Enchant failed"


def _load(names: Sequence[str]):
    for name in names:
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        _declare(lib)
        return lib
    raise Unavailable("libenchant-2 is not installed")


def _declare(lib) -> None:
    lib.enchant_broker_init.argtypes = []
    lib.enchant_broker_init.restype = c_void_p
    lib.enchant_broker_free.argtypes = [c_void_p]
    lib.enchant_broker_free.restype = None
    lib.enchant_broker_list_dicts.argtypes = [c_void_p, _DESCRIBE, c_void_p]
    lib.enchant_broker_list_dicts.restype = None
    lib.enchant_broker_request_dict.argtypes = [c_void_p, c_char_p]
    lib.enchant_broker_request_dict.restype = c_void_p
    lib.enchant_broker_free_dict.argtypes = [c_void_p, c_void_p]
    lib.enchant_broker_free_dict.restype = None
    for name in ("enchant_dict_add", "enchant_dict_add_to_session"):
        function = getattr(lib, name)
        function.argtypes = [c_void_p, c_char_p, c_ssize_t]
        function.restype = None
    lib.enchant_dict_check.argtypes = [c_void_p, c_char_p, c_ssize_t]
    lib.enchant_dict_check.restype = c_int
    lib.enchant_dict_suggest.argtypes = [c_void_p, c_char_p, c_ssize_t, POINTER(c_size_t)]
    lib.enchant_dict_suggest.restype = POINTER(c_char_p)
    lib.enchant_dict_free_string_list.argtypes = [c_void_p, POINTER(c_char_p)]
    lib.enchant_dict_free_string_list.restype = None
    lib.enchant_dict_get_error.argtypes = [c_void_p]
    lib.enchant_dict_get_error.restype = c_char_p
