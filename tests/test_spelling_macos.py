# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The macOS spell checker itself (NSSpellChecker), where it exists."""

import sys
import uuid

import pytest

from PySide6.QtGui import QTextDocument

from spelling.backends import AUTOMATIC, create_backend
from spelling.service import DocumentSpelling, SpellChecker
from tests.spelling_fakes import Editor

if sys.platform != "darwin":
    pytest.skip("NSSpellChecker exists only on macOS", allow_module_level=True)
pytest.importorskip("rubicon.objc", reason="rubicon-objc reaches NSSpellChecker")

from spelling.macos import MacBackend  # noqa: E402


@pytest.fixture(scope="module")
def backend():
    backend = MacBackend()
    yield backend
    backend.close()


def test_the_platform_backend_is_the_macos_one():
    made = create_backend()
    assert isinstance(made, MacBackend)
    made.close()


def test_it_is_available_with_english_and_german(backend):
    assert backend.is_available(), backend.problem
    languages = backend.languages()
    assert "en" in languages and "de" in languages
    assert backend.default_language() == AUTOMATIC or backend.default_language() in languages


def test_words_are_checked_in_the_language_asked(backend):
    assert backend.check("Haus", "de") is True
    assert backend.check("Straße", "de-DE") is True
    assert backend.check("Strase", "de") is False
    assert backend.check("house", "en-US") is True
    assert backend.check("hosue", "en") is False


def test_suggestions_come_best_first(backend):
    assert "Straße" in backend.suggestions("Strase", "de")
    assert backend.suggestions("hosue", "en")[0] == "house"


def test_a_paragraph_is_checked_in_one_language_in_utf16_units(backend):
    text = "😀 Das ist ein Tesst mit Fehlr."
    found = backend.check_text(text, "de")
    assert [text[start:end] for start, end in found.misspelled] == ["Tesst", "Fehlr"]
    assert found.language == "de"


def test_macos_tells_the_language_of_each_paragraph(backend):
    german = backend.check_text("Das ist ein Tesst mit vielen Wörtern und einem Fehlr.",
                                AUTOMATIC)
    english = backend.check_text("This is a tesst with many words and one mistaek.",
                                 AUTOMATIC)
    assert german.language == "de" and english.language == "en"
    assert len(german.misspelled) == 2 and len(english.misspelled) == 2


def test_mistakes_inside_underscore_emphasis_are_found(backend):
    checker = SpellChecker(backend)
    for text, wrong in (("Das ist ein _Wrot_ in einem Satz hier.", "Wrot"),
                        ("Das ist ein __Fehlr__ in einem Satz hier.", "Fehlr"),
                        ("This is a _mistaek_ in a sentence here.", "mistaek")):
        assert [m.word for m in checker.find_misspellings(text, AUTOMATIC)] == [wrong], text


def test_an_ignored_word_counts_as_right_until_the_app_quits(backend):
    word = "Qwrtzq" + uuid.uuid4().hex[:6]
    assert backend.check(word, "de") is False
    assert backend.ignore(word, "de") is True
    assert backend.check(word, "de") is True


def test_a_learned_word_goes_into_the_persons_dictionary(backend):
    word = "Zxqvlearn" + uuid.uuid4().hex[:6]
    try:
        assert backend.learn(word, "en") is True
        assert backend.check(word, "en") is True
        assert backend._checker.hasLearnedWord(word)
    finally:
        backend._checker.unlearnWord(word)
    assert not backend._checker.hasLearnedWord(word)


def test_the_last_word_of_a_paragraph_gets_its_underline_once_typed():
    editor = Editor(MacBackend())
    editor.type("Das ist ein Fehlr.\nNeuer Absatz")
    assert editor.underlined()[0] == ["Fehlr"]
    editor.checker.backend.close()


def test_a_document_is_checked_paragraph_by_paragraph():
    checker = SpellChecker(MacBackend())
    doc = QTextDocument()
    doc.setPlainText("Das ist ein Tesst mit vielen Wörtern.\n"
                     "This is a tesst with https://exmple.org and `cde`.\n"
                     "Grüße an @alise und #bitcoinn")
    spelling = DocumentSpelling(checker, doc)
    spelling.check_all()
    found = []
    block = doc.begin()
    while block.isValid():
        found.append([block.text()[m.start:m.end] for m in spelling.misspellings(block)])
        block = block.next()
    assert found == [["Tesst"], ["tesst"], []]
    checker.backend.close()
