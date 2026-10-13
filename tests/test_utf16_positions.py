# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins that a place found in a paragraph's text is the same place in the
document, whatever comes before it.

Python counts a string in code points; a QTextDocument counts positions
in UTF-16 units, where an emoji, a skin tone, a flag or any other
character beyond U+FFFF takes two. Adding a Python index to a block's
position put everything after such a character one place too far per
character: a mention after "🌸" in a note was replaced one place late,
which ate the space before it and doubled the end of its key, so the
note went out with an invalid key and no p tag (review H1).

What must hold: every part of the app that finds something in a
paragraph's text and acts on it in the document (mentions read back as
names in a note and in a draft) finds the same thing with emoji, ZWJ
sequences, skin tones and flags before and between.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr import bech32  # noqa: E402

BOB = bech32.decode_npub("npub1f2ktq2xcmh9wpmv0s6wqu6r9uvlvp7qr6sdqqk87x3dmnc9w0juswgtagx")
BOB_NPUB = bech32.encode_npub(BOB)
CAROL = "c3" * 32
CAROL_NPUB = bech32.encode_npub(CAROL)

# Before and between the mentions: an emoji, a skin tone, a ZWJ family,
# a flag, and nothing astral at all (the case that always worked).
AROUND = ["🌸 ", "emoji 👍🏽 and 👨‍👩‍👧 then ", "🇩🇪🇺🇦 ", "Grüße ", "🌸👍🏽"]


def notes():
    for before in AROUND:
        for between in AROUND:
            yield (f"{before}nostr:{BOB_NPUB} thanks {between}nostr:{CAROL_NPUB} and "
                   f"{between}the end")


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("text", list(notes()))
def test_a_note_with_emoji_shows_names_and_goes_out_as_written(tmp_path, text):
    from tests.test_publish_sheets import Captured, note, p_tags
    import nostr.ui.publish_note_dialog as note_module
    Captured.made = []
    real = note_module.PublishJob
    note_module.PublishJob = Captured
    try:
        sheet = note(tmp_path, content=text)
        shown = sheet._text_edit.toPlainText()
        assert "nostr:" not in shown
        assert shown == text.replace(f"nostr:{BOB_NPUB}", "@Bob Writer").replace(
            f"nostr:{CAROL_NPUB}", "@Carol")
        assert sheet._text_edit.note() == text
        sheet._on_publish()
        assert Captured.made[0]["content"] == text
        assert [tag[1] for tag in p_tags(Captured.made[0])] == [BOB, CAROL]
    finally:
        note_module.PublishJob = real


@pytest.mark.parametrize("text", list(notes()))
def test_a_draft_with_emoji_reads_its_mentions_back_as_names(text):
    from editor import HtmlEditor
    from main_window import MainWindow
    from markdown_writer import document_to_markdown
    names = {BOB: "Bob Writer", CAROL: "Carol"}
    ed = HtmlEditor()
    ed.set_nostr_check(lambda: True)
    ed._mention_label = lambda entity: (names.get(bech32.decode_npub(entity))
                                        if entity.startswith("npub1") else None)
    MainWindow._load_markdown(ed, text)
    assert ed.toPlainText() == text.replace(f"nostr:{BOB_NPUB}", "@Bob Writer").replace(
        f"nostr:{CAROL_NPUB}", "@Carol")
    assert document_to_markdown(ed.document()).rstrip("\n") == text


@pytest.mark.parametrize("before", ["x🌸y ", "👨‍👩‍👧 ", "🇩🇪 (", "Grüße "])
def test_at_after_emoji_asks_whom_to_mention_in_the_note_and_the_editor(tmp_path, before):
    from PySide6.QtTest import QTest
    from editor import HtmlEditor
    from tests.test_publish_sheets import note
    sheet = note(tmp_path, content=before.rstrip(" ("))
    sheet.show()
    edit = sheet._text_edit
    edit.setPlainText(before)
    cursor = edit.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    edit.setTextCursor(cursor)
    asked = []
    edit.mention_requested.connect(asked.append)
    QTest.keyClicks(edit, "@")
    assert asked == [edit.textCursor().position() - 1]
    sheet.close()
    ed = HtmlEditor()
    ed.set_nostr_check(lambda: True)
    ed.setPlainText(before)
    cursor = ed.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    ed.setTextCursor(cursor)
    asked = []
    ed.mention_requested.connect(asked.append)
    QTest.keyClicks(ed, "@")
    assert asked == [ed.textCursor().position() - 1]


@pytest.mark.parametrize("word", ["a🌸b", "👍🏽x"])
def test_at_inside_a_word_after_emoji_stays_an_at(tmp_path, word):
    from PySide6.QtTest import QTest
    from editor import HtmlEditor
    ed = HtmlEditor()
    ed.set_nostr_check(lambda: True)
    ed.setPlainText(f"{word} me")
    cursor = ed.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    ed.setTextCursor(cursor)
    asked = []
    ed.mention_requested.connect(asked.append)
    QTest.keyClicks(ed, "@")                                   # "me@", an email address
    assert asked == []


@pytest.mark.parametrize("before", ["🌸 ", "👨‍👩‍👧 x ", "🇩🇪 "])
def test_a_slash_after_emoji_opens_the_slash_menu(before):
    from tests.test_slash_menu import Setup, type_text
    s = Setup()
    type_text(s.editor, before + "/he")
    assert s.titles()[:1] == ["Heading 1"]


@pytest.mark.parametrize("text, markdown", [
    ("🌸 a **bold** word", "🌸 a **bold** word\n"),
    ("👨‍👩‍👧 an _italic_ 🇩🇪 word", "👨‍👩‍👧 an *italic* 🇩🇪 word\n"),
    ("👍🏽 run `ls 🌸` now", "👍🏽 run `ls 🌸` now\n"),
    ("a **🌸 bold** word", "a **🌸 bold** word\n"),
    ("an _italic_👍🏽 x", "an *italic*👍🏽 x\n"),
])
def test_markdown_shortcuts_after_emoji_style_the_words_typed(text, markdown):
    from tests.rich_text_helpers import assert_round_trip
    from tests.test_markdown_shortcuts import typed
    ed = typed(text)
    assert assert_round_trip(ed.document()) == markdown
