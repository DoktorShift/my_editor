# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Enchant, the Linux spell checker, where the system has it."""

import os
import sys
import uuid

import pytest

from PySide6.QtGui import QTextDocument

from spelling.backends import create_backend
from spelling.enchant import EnchantBackend
from spelling.service import DocumentSpelling, SpellChecker


def test_without_the_library_spelling_is_unavailable_and_says_why():
    backend = EnchantBackend(library_names=("libenchant-missing.so.9",))
    assert backend.is_available() is False
    assert backend.problem == "libenchant-2 is not installed"
    assert backend.check("helo", "en-US") is True


def test_a_library_that_does_not_load_says_what_the_loader_said(tmp_path):
    broken = tmp_path / "libenchant-2.so.2"
    broken.write_bytes(b"not a library")
    backend = EnchantBackend(library_names=(str(broken), "libenchant-missing.so.9"))
    assert backend.is_available() is False
    assert backend.problem.startswith("libenchant-2 could not be loaded: ")
    assert len(backend.problem) > len("libenchant-2 could not be loaded: ") + 10


# -- the real library ----------------------------------------------------------

@pytest.fixture
def backend(tmp_path, monkeypatch):
    # Words learned in tests go to a word list of their own, not the person's.
    monkeypatch.setenv("ENCHANT_CONFIG_DIR", str(tmp_path / "enchant"))
    backend = EnchantBackend()
    if not backend.is_available():
        pytest.skip(f"Enchant is not usable here: {backend.problem}")
    if "en-US" not in backend.languages():
        pytest.skip(f"no American English dictionary, only {backend.languages()}")
    yield backend
    backend.close()


def test_the_platform_backend_is_enchant_on_linux():
    if not sys.platform.startswith("linux"):
        pytest.skip("Enchant is the spell checker of Linux")
    made = create_backend()
    assert isinstance(made, EnchantBackend)
    made.close()


def test_words_are_checked_with_the_installed_dictionary(backend):
    assert backend.check("hello", "en-US") is True
    assert backend.check("helo", "en-US") is False
    assert backend.check("don't", "en-US") is True
    assert "hello" in backend.suggestions("helo", "en-US")


def test_german_with_umlauts_and_sharp_s(backend):
    if "de-DE" not in backend.languages():
        pytest.skip("no German dictionary installed")
    assert backend.check("Straße", "de-DE") is True
    assert backend.check("schön", "de-DE") is True
    assert backend.check("Strase", "de-DE") is False
    assert "Straße" in backend.suggestions("Strase", "de-DE")


def test_a_learned_word_goes_into_the_persons_word_list(backend, tmp_path):
    word = "Zxqvlearn" + uuid.uuid4().hex[:6]
    assert backend.check(word, "en-US") is False
    assert backend.learn(word, "en-US") is True
    assert backend.check(word, "en-US") is True
    lists = [os.path.join(root, name) for root, _dirs, names in os.walk(tmp_path / "enchant")
             for name in names]
    assert any(word in open(path, encoding="utf-8").read() for path in lists)


def test_an_ignored_word_counts_only_for_this_session(backend):
    word = "Qwrtzq" + uuid.uuid4().hex[:6]
    assert backend.ignore(word, "en-US") is True
    assert backend.check(word, "en-US") is True
    again = EnchantBackend()
    assert again.check(word, "en-US") is False
    again.close()


def test_a_document_is_checked_word_by_word(backend):
    checker = SpellChecker(backend)
    doc = QTextDocument()
    doc.setPlainText("This is a tesst with https://exmple.org and `cde`.\n"
                     "A well-known e-mail adress, see @alise and #bitcoinn.")
    spelling = DocumentSpelling(checker, doc, language="en-US")
    spelling.check_all()
    found = []
    block = doc.begin()
    while block.isValid():
        found.append([block.text()[m.start:m.end] for m in spelling.misspellings(block)])
        block = block.next()
    assert found == [["tesst"], ["adress"]]
