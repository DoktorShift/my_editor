# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The spell checker interface: languages, and never raising into the caller."""

import logging
import threading

from spelling import backends
from spelling.backends import (
    AUTOMATIC, MAX_WORD_LENGTH, NullBackend, TextCheck, best_language, normalize_language,
)
from tests.spelling_fakes import FakeBackend


# -- languages -----------------------------------------------------------------

def test_language_names_become_bcp47_tags():
    cases = {
        "de_DE": "de-DE", "de-de": "de-DE", "de_DE.UTF-8": "de-DE", "de_DE@euro": "de-DE",
        "en": "en", "EN_us": "en-US", "pt_BR": "pt-BR", "zh_hans_cn": "zh-Hans-CN",
        "es-419": "es-419", "de_DE_frami": "de-DE-frami", "ca-ES-valencia": "ca-ES-valencia",
    }
    for name, tag in cases.items():
        assert normalize_language(name) == tag, name


def test_names_that_are_no_language_are_dropped():
    for name in ("", "C", "POSIX", "C.UTF-8", "Multilingual", "x", "1234", "de_DE!"):
        assert normalize_language(name) is None, name
    # Four letters: no primary language subtag is that long, so the
    # marker for automatic identification can never be taken for one.
    assert normalize_language(AUTOMATIC) is None


def test_the_preferred_language_wins_when_it_is_there():
    assert best_language(["de-AT", "en-US"], ["en-US", "de-DE", "de-AT"]) == "de-AT"
    assert best_language(["de_DE.UTF-8"], ["en_US", "de_DE"]) == "de-DE"


def test_a_missing_region_falls_back_to_the_same_language():
    # Swiss German without a Swiss dictionary: plain German, then Germany's.
    assert best_language(["de-CH"], ["en-US", "de", "de-AT"]) == "de"
    assert best_language(["de-CH"], ["en-US", "de-AT", "de-DE"]) == "de-DE"
    # A bare language picks the region where it is mostly spoken.
    assert best_language(["de"], ["de-AT", "de-DE"]) == "de-DE"
    assert best_language(["en"], ["en-GB", "en-US"]) == "en-US"
    assert best_language(["pt"], ["pt-PT", "pt-BR"]) == "pt-BR"


def test_without_a_match_english_comes_next_then_anything():
    assert best_language(["ja-JP"], ["de-DE", "en-GB"]) == "en-GB"
    assert best_language(["ja-JP"], ["de-DE", "fr-FR"]) == "de-DE"
    assert best_language([], ["fr-FR"]) == "fr-FR"
    assert best_language(["de-DE"], []) is None


# -- never raising ---------------------------------------------------------------

def test_the_null_backend_is_unavailable_and_says_why(caplog):
    backend = NullBackend()
    with caplog.at_level(logging.WARNING, logger="spelling.backends"):
        assert backend.is_available() is False
    assert backend.problem
    assert any("unavailable" in record.getMessage() for record in caplog.records)
    assert backend.languages() == []
    assert backend.default_language() is None
    assert backend.check("anything", "en-US") is True
    assert backend.suggestions("anything", "en-US") == []
    assert backend.learn("anything", "en-US") is False
    assert backend.ignore("anything", "en-US") is False
    assert backend.check_text("anything", "en-US") == TextCheck((), "en-US")


def test_an_absent_system_checker_is_said_once_without_a_trace(caplog):
    backend = FakeBackend()
    backend.absent = "libenchant-2 is not installed"
    with caplog.at_level(logging.WARNING, logger="spelling.backends"):
        assert backend.is_available() is False
        assert backend.check("helo", "en-US") is True
    assert backend.problem == "libenchant-2 is not installed"
    assert backend.opened == 1
    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is None


def test_a_failing_backend_turns_unavailable_and_answers_neutrally(caplog):
    backend = FakeBackend()
    assert backend.check("helo", "en-US") is False
    backend.fail = "check"
    with caplog.at_level(logging.WARNING, logger="spelling.backends"):
        assert backend.check("helo", "en-US") is True        # never underline when broken
        assert backend.check("xyzzy", "en-US") is True
        assert backend.suggestions("helo", "en-US") == []
    assert backend.is_available() is False
    assert "check broke" in backend.problem
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and warnings[0].exc_info is not None
    assert backend.checked_words().count("xyzzy") == 0       # not asked again
    assert backend.closed == 1


def test_every_hook_failing_is_caught():
    for hook, call in (("languages", lambda b: b.languages()),
                       ("suggestions", lambda b: b.suggestions("helo", "en-US")),
                       ("learn", lambda b: b.learn("helo", "en-US")),
                       ("ignore", lambda b: b.ignore("helo", "en-US")),
                       ("open", lambda b: b.check("helo", "en-US"))):
        backend = FakeBackend()
        backend.fail = hook
        call(backend)                                            # does not raise
        assert backend.is_available() is False, hook


def test_words_no_system_should_see_are_never_sent():
    backend = FakeBackend()
    for word in ("", "two words", "tab\tword", "new\nline", "x" * (MAX_WORD_LENGTH + 1)):
        assert backend.check(word, "en-US") is True
        assert backend.suggestions(word, "en-US") == []
        assert backend.learn(word, "en-US") is False
        assert backend.ignore(word, "en-US") is False
    assert backend.calls == []


def test_learning_and_ignoring_reach_the_system():
    backend = FakeBackend()
    assert backend.learn("Nostr", "en-US") is True
    assert backend.ignore("zap", "en-US") is True
    assert backend.check("Nostr", "en-US") and backend.check("zap", "en-US")
    assert backend.learned == ["Nostr"] and backend.ignored == ["zap"]


def test_suggestions_never_repeat_the_word_or_each_other():
    backend = FakeBackend({"en-US": ["help", "hello", "hello"]})
    assert backend.suggestions("helo", "en-US") == ["hello", "help"]
    backend.words["en-US"].add("helo")
    assert "helo" not in backend.suggestions("helo", "en-US")


def test_automatic_means_the_default_language_where_the_system_cannot_tell():
    backend = FakeBackend(default="de-DE")
    backend.check("Haus", AUTOMATIC)
    backend.check("Haus", "")
    assert [call[2] for call in backend.calls] == ["de-DE", "de-DE"]


def test_a_backend_answers_only_on_its_own_thread(caplog):
    backend = FakeBackend()
    answers = []
    with caplog.at_level(logging.WARNING, logger="spelling.backends"):
        for _ in range(2):
            worker = threading.Thread(target=lambda: answers.append(backend.check("helo", "en-US")))
            worker.start()
            worker.join()
    assert answers == [True, True]
    assert backend.calls == []
    assert len(caplog.records) == 1
    # The owner thread still gets real answers.
    assert backend.check("helo", "en-US") is False
    assert backend.is_available() is True


def test_the_default_language_follows_the_system(monkeypatch):
    class Plain(FakeBackend):
        _default_language = backends.SpellBackend._default_language

    monkeypatch.setattr(backends, "system_languages", lambda: ["de-AT", "en-US"])
    assert Plain().default_language() == "de-DE"
    monkeypatch.setattr(backends, "system_languages", lambda: [])
    assert Plain().default_language() == "en-US"


def test_this_platform_gets_a_backend_that_never_raises():
    backend = backends.create_backend()
    backend.is_available()
    backend.check("word", backend.default_language() or "en-US")
    backend.close()
