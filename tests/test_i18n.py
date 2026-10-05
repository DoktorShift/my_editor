# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins how MyEditor speaks the reader's language.

What must hold:

  A .po file is read the way gettext tools write it: contexts, plurals,
  multi-line texts, escapes; fuzzy and empty translations are not used,
  so English shows instead of a guess.

  "System" follows the operating system's languages in order, and
  anything without a translation is English.

  Every translation fills the same placeholders as the English, so no
  language can make a window fail to open, and the German translation is
  complete (scripts/i18n.py check).

  The code never hands an f-string to _(): it would never be found in a
  translation.
"""

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import i18n  # noqa: E402

PO = r'''
msgid ""
msgstr ""
"Language: de\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\n"

#: main_window.py:1
msgid "Save"
msgstr "Sichern"

msgctxt "verb"
msgid "Open"
msgstr "Öffnen"

#, python-brace-format
msgid "{n} file"
msgid_plural "{n} files"
msgstr[0] "{n} Datei"
msgstr[1] "{n} Dateien"

msgid ""
"A long text that goes "
"over two lines.\n"
"With a \"quote\"."
msgstr ""
"Ein langer Text über "
"zwei Zeilen.\n"
"Mit einem \"Zitat\"."

#, fuzzy
msgid "Guessed"
msgstr "Geraten"

msgid "Not yet"
msgstr ""

#~ msgid "Old"
#~ msgstr "Alt"
'''


@pytest.fixture
def german(tmp_path):
    (tmp_path / "de.po").write_text(PO, encoding="utf-8")
    i18n.install("de", locale_dir=str(tmp_path))
    yield
    i18n.install(i18n.ENGLISH)


def test_a_po_file_is_read_like_gettext_writes_it(german):
    assert i18n._("Save") == "Sichern"
    assert i18n.pgettext("verb", "Open") == "Öffnen"
    assert i18n._("Open") == "Open"                    # without the context: not this one
    assert i18n.ngettext("{n} file", "{n} files", 1) == "{n} Datei"
    assert i18n.ngettext("{n} file", "{n} files", 3) == "{n} Dateien"
    assert i18n._('A long text that goes over two lines.\nWith a "quote".') == (
        'Ein langer Text über zwei Zeilen.\nMit einem "Zitat".')


def test_guesses_and_gaps_show_in_english(german):
    assert i18n._("Guessed") == "Guessed"
    assert i18n._("Not yet") == "Not yet"
    assert i18n._("Old") == "Old"
    assert i18n.ngettext("{n} thing", "{n} things", 2) == "{n} things"


def test_english_needs_no_catalog():
    i18n.install(i18n.ENGLISH)
    assert i18n.language() == "en"
    assert i18n._("Save") == "Save"
    assert i18n.ngettext("{n} file", "{n} files", 1) == "{n} file"


def test_system_follows_the_systems_languages_in_order(monkeypatch):
    monkeypatch.setattr(i18n, "available", lambda: ["en", "de"])
    monkeypatch.setattr(i18n, "system_languages", lambda: ["fr", "de", "en"])
    assert i18n.resolve(i18n.SYSTEM) == "de"
    monkeypatch.setattr(i18n, "system_languages", lambda: ["fr"])
    assert i18n.resolve(i18n.SYSTEM) == "en"
    assert i18n.resolve("de") == "de"
    assert i18n.resolve("xx") == "en"                 # a choice that went away


def test_a_missing_catalog_falls_back_to_english(tmp_path):
    assert i18n.install("de", locale_dir=str(tmp_path)) == "en"
    assert i18n._("Save") == "Save"
    i18n.install(i18n.ENGLISH)


def test_the_pseudo_language_is_longer_and_keeps_placeholders():
    i18n.install(i18n.PSEUDO)
    try:
        text = i18n._("Save {name} now")
        assert "{name}" in text and len(text) > len("Save {name} now") * 1.3
        assert text.format(name="x")                   # still fills in
    finally:
        i18n.install(i18n.ENGLISH)


def test_the_setting_can_be_overridden_for_testing(monkeypatch):
    monkeypatch.setenv("MYEDITOR_LANGUAGE", "pseudo")
    assert i18n.chosen_language() == "pseudo"


def test_the_shipped_translations_are_complete_and_safe():
    """Every text the code shows is translated into German, with the same
    placeholders and no em dash (scripts/i18n.py check)."""
    result = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "i18n.py"),
                             "check"], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stdout[-4000:]


def test_every_shipped_catalog_reads():
    for name in os.listdir(os.path.join(ROOT, "locale")):
        if name.endswith(".po"):
            assert i18n.load_catalog(name[:-3]) is not None, name
