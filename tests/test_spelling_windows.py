# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Windows spell checker: its COM calls everywhere, the real one on Windows."""

import logging
import sys

import pytest

from spelling.backends import create_backend
from spelling.windows import WindowsBackend
from tests.spelling_com_fakes import FakeOle

WORDS = {"en-US": ["the", "this", "is", "a", "test", "hello", "help", "world"],
         "de-DE": ["das", "ist", "ein", "Haus"]}


def backend_with(words=WORDS, **options):
    ole = FakeOle(words, **options)
    return WindowsBackend(ole), ole


# -- the COM calls, through stand-ins ------------------------------------------

def test_languages_are_read_and_every_string_freed():
    backend, ole = backend_with()
    assert backend.is_available()
    assert backend.languages() == ["en-US", "de-DE"]
    assert ole.registry.strings == {}
    assert len(ole.registry.freed) == 2
    assert ole.registry.held() == ["ISpellCheckerFactory"]


def test_a_paragraph_is_checked_in_one_call_in_utf16_units():
    backend, ole = backend_with()
    text = "😀 this is a tesst, the the wrld"
    found = backend.check_text(text, "en-US")
    assert [text[start:end] for start, end in found.misspelled] == ["tesst", "wrld"]
    assert found.language == "en-US"
    # The repeated "the" is Windows's own grammar note, not a misspelling.
    assert sorted(ole.registry.held()) == ["ISpellChecker en-US", "ISpellCheckerFactory"]


def test_single_words_and_suggestions():
    backend, ole = backend_with()
    assert backend.check("hello", "en-US") is True
    assert backend.check("helo", "en-US") is False
    assert backend.suggestions("helo", "en-US") == ["hello", "help"]
    assert backend.suggestions("hello", "en-US") == []       # right: S_FALSE
    assert ole.registry.strings == {}
    assert sorted(ole.registry.held()) == ["ISpellChecker en-US", "ISpellCheckerFactory"]


def test_learning_and_ignoring_reach_windows():
    backend, ole = backend_with()
    assert backend.learn("Nostr", "en-US") is True
    assert backend.ignore("zap", "en-US") is True
    checker = ole.factory.checkers["en-US"]
    assert checker.added == ["Nostr"] and checker.ignored == ["zap"]
    assert backend.check("Nostr", "en-US") and backend.check("zap", "en-US")


def test_one_checker_per_language_made_once():
    backend, ole = backend_with()
    backend.check("Haus", "de-DE")
    backend.check("Hous", "de-DE")
    backend.check("hello", "en-US")
    assert sorted(ole.factory.checkers) == ["de-DE", "en-US"]
    assert ole.registry.held().count("ISpellChecker de-DE") == 1


def test_a_language_windows_cannot_check_is_left_alone():
    backend, ole = backend_with()
    assert backend.check("Bonjur", "fr-FR") is True
    assert backend.suggestions("Bonjur", "fr-FR") == []
    assert backend.learn("Bonjur", "fr-FR") is False
    assert backend.is_available()


def test_the_default_language_is_the_system_language(monkeypatch):
    from spelling import backends
    monkeypatch.setattr(backends, "system_languages", lambda: ["de-AT", "en-US"])
    backend, _ole = backend_with()
    assert backend.default_language() == "de-DE"


def test_closing_lets_go_of_everything():
    backend, ole = backend_with()
    backend.check_text("the wrld", "en-US")
    backend.check_text("das Hous", "de-DE")
    backend.close()
    assert ole.registry.held() == []
    assert ole.initialized == ole.uninitialized == 1


def test_a_failing_call_turns_the_backend_off(caplog):
    backend, ole = backend_with()
    backend.check("hello", "en-US")
    ole.factory.checkers["en-US"].fail_check = True
    with caplog.at_level(logging.WARNING, logger="spelling.backends"):
        assert backend.check("helo", "en-US") is True
    assert backend.is_available() is False
    assert "0x80004005" in backend.problem
    assert ole.registry.held() == []                         # closed on the way out


def test_a_windows_without_spell_checking_says_so():
    backend, _ole = backend_with(registered=False)
    assert backend.is_available() is False
    assert backend.problem == "this version of Windows has no spell checking"


@pytest.mark.skipif(sys.platform == "win32", reason="checks the behavior off Windows")
def test_elsewhere_it_is_simply_unavailable():
    backend = WindowsBackend()
    assert backend.is_available() is False
    assert backend.check("helo", "en-US") is True


# -- the real one --------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="the Spell Checking API exists only on Windows")
def test_windows_checks_spelling_with_its_own_checker():
    backend = create_backend()
    assert isinstance(backend, WindowsBackend)
    if not backend.is_available():
        # The one acceptable reason: a Windows without the API at all.
        assert backend.problem == "this version of Windows has no spell checking", backend.problem
        pytest.skip(backend.problem)
    try:
        languages = backend.languages()
        assert languages
        assert backend.default_language() in languages
        if "en-US" not in languages:
            pytest.skip(f"no English dictionary here, only {languages}")
        assert backend.check("hello", "en-US") is True
        assert backend.check("helo", "en-US") is False
        assert "hello" in backend.suggestions("helo", "en-US")
        text = "😀 This is a tesst of the the checker."
        found = backend.check_text(text, "en-US")
        assert [text[start:end] for start, end in found.misspelled] == ["tesst"]
        assert backend.ignore("Qwrtzqx", "en-US") is True
        assert backend.check("Qwrtzqx", "en-US") is True
        assert backend.problem is None
    finally:
        backend.close()
