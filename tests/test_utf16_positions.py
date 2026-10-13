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


FOOTNOTED = "🌸 A claim[^1] 👨‍👩‍👧 and[^1] again.\n\n[^1]: 🇩🇪 The source.\n"


def test_footnote_marks_and_jumps_after_emoji_land_on_the_marks():
    from PySide6.QtGui import QTextCursor
    import rich_text
    from editor import HtmlEditor
    from markdown_writer import read_markdown
    ed = HtmlEditor()
    read_markdown(ed.document(), FOOTNOTED)
    doc = ed.document()
    for position, label in rich_text.footnote_marks(doc):
        probe = QTextCursor(doc)
        probe.setPosition(position)
        probe.setPosition(position + 4, QTextCursor.MoveMode.KeepAnchor)
        assert probe.selectedText() == "[^1]" and label == "1"
    first = rich_text.footnote_marks(doc)[0][0]
    mark = QTextCursor(doc)
    mark.setPosition(first + 2)
    to = rich_text.footnote_jump(mark)
    words = QTextCursor(doc)
    words.setPosition(to)
    words.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
    assert words.selectedText() == "🇩🇪 The source."
    back = QTextCursor(doc)
    back.setPosition(to + 3)
    assert rich_text.footnote_jump(back) == first
    ed.resize(600, 200)
    ed.show()
    QApplication.processEvents()
    second = QTextCursor(doc)
    second.setPosition(rich_text.footnote_marks(doc)[1][0] + 2)
    rect = ed.cursorRect(second)
    assert ed._footnote_tip(rect.center()) == "🇩🇪 The source."
    ed.close()


def test_the_preview_raises_footnote_marks_after_emoji_in_their_place():
    import nostr.preview as pv
    doc = pv.article_document(FOOTNOTED, pv.Article(title="T"))
    assert "🌸 A claim1 👨‍👩‍👧 and1 again." in doc.toPlainText()
    assert "🇩🇪 The source." in doc.toPlainText()


def _styled(block):
    """(words, format) of every piece the highlighter styled in ``block``,
    the pieces' document places taken back to the text's."""
    from PySide6.QtGui import QTextCharFormat
    from doc_walk import text_index
    text = block.text()
    return [(text[text_index(text, r.start):text_index(text, r.start + r.length)],
             QTextCharFormat(r.format)) for r in block.layout().formats()]


@pytest.mark.parametrize("dark", [False, True])
def test_code_after_emoji_is_colored_where_it_is(dark):
    from PySide6.QtGui import QTextDocument
    from highlighter import RichTextLook, SyntaxHighlighter
    from tests.rich_text_helpers import block_named, from_markdown
    from tests.test_code_highlighting import KEYWORD, STRING
    line = "x = '🌸👍🏽' if '👨‍👩‍👧' else '🇩🇪'"
    doc = from_markdown(f"```python\n{line}\n```\n")
    look = RichTextLook(doc, is_dark=dark)
    look.rehighlight()
    pieces = _styled(block_named(doc, line))
    strings = [w for w, f in pieces if f.foreground().color().name() == STRING[dark]]
    keywords = [w for w, f in pieces if f.foreground().color().name() == KEYWORD[dark]]
    assert strings == ["'🌸👍🏽'", "'👨‍👩‍👧'", "'🇩🇪'"] and keywords == ["if", "else"]
    assert "".join(w for w, f in pieces) == line             # the code font, all of it
    source = QTextDocument()
    source.setPlainText(line)
    file_look = SyntaxHighlighter(source, "python", is_dark=dark)
    file_look.rehighlight()
    pieces = _styled(source.begin())
    assert [w for w, f in pieces if f.foreground().color().name() == STRING[dark]] == [
        "'🌸👍🏽'", "'👨‍👩‍👧'", "'🇩🇪'"]


def test_a_footnote_mark_after_emoji_is_raised_where_it_is():
    from PySide6.QtGui import QTextCharFormat
    from highlighter import RichTextLook
    from tests.rich_text_helpers import block_named, from_markdown
    doc = from_markdown(FOOTNOTED)
    look = RichTextLook(doc, is_dark=False)
    look.rehighlight()
    raised = [w for w, f in _styled(block_named(doc, "🌸 A claim[^1] 👨‍👩‍👧 and[^1] again."))
              if f.verticalAlignment() == QTextCharFormat.VerticalAlignment.AlignSuperScript]
    assert raised == ["[^1]", "[^1]"]
    note = _styled(block_named(doc, "[^1]: 🇩🇪 The source."))
    assert "".join(w for w, f in note) == "[^1]: 🇩🇪 The source."   # quieter, all of it


EMOJI_STRUCTURED = ("# The 🌸 Cat\n\nA 👍🏽 cat, a CAT and a 👨‍👩‍👧 category.\n\n- 🇩🇪 cat one\n"
                    "    - cat 🌸 two\n\n| cat 🌸 | dog |\n| --- | --- |\n| a 👍🏽 cat | cats |\n\n"
                    "> 🌸 cat\n\n```\n🌸 = cat\n```\n\nLast 👨‍👩‍👧 cat here, my_cat and cat.\n")


@pytest.mark.parametrize("needle", ["cat", "Cat", "a c", "t", "cat one", "🌸"])
def test_find_after_emoji_finds_what_qt_finds(needle):
    from find_replace import FindOptions, find_all
    from tests.test_find_replace import doc_of, qt_find_all
    doc = doc_of(EMOJI_STRUCTURED)
    for options in (FindOptions(), FindOptions(match_case=True), FindOptions(whole_words=True)):
        assert find_all(doc, needle, options) == qt_find_all(doc, needle, options)


def test_an_opened_html_file_keeps_the_last_letters_of_a_list_item_after_emoji():
    from PySide6.QtGui import QTextDocument
    from export_html import normalize_after_set_html
    doc = QTextDocument()
    doc.setHtml("<ul><li>🌸👨‍👩‍👧 an item   </li><li>🇩🇪 two </li></ul><p>👍🏽 end </p>")
    normalize_after_set_html(doc)
    assert doc.toPlainText() == "🌸👨‍👩‍👧 an item\n🇩🇪 two\n👍🏽 end"


def test_return_in_code_after_emoji_keeps_the_lines_indent():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor
    from PySide6.QtTest import QTest
    from tests.rich_text_helpers import from_markdown
    from editor import HtmlEditor
    from markdown_writer import read_markdown, document_to_markdown
    ed = HtmlEditor()
    read_markdown(ed.document(), "```\n  🌸🌸x\n```\n")
    cursor = ed.textCursor()
    cursor.setPosition(ed.document().begin().position())
    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    ed.setTextCursor(cursor)
    QTest.keyClick(ed, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClicks(ed, "      y")
    cursor = ed.textCursor()
    cursor.setPosition(ed.document().begin().position() + 7)   # after "  🌸🌸x"
    ed.setTextCursor(cursor)
    QTest.keyClick(ed, Qt.Key.Key_Return)
    QTest.keyClicks(ed, "z")
    assert "  🌸🌸x\n  z" in document_to_markdown(ed.document())


def test_a_replacement_with_emoji_is_where_it_says_it_is():
    from PySide6.QtGui import QTextCursor
    from find_replace import find_all, replace_all, replace_match
    from tests.test_find_replace import doc_of
    doc = doc_of("🌸 a cat and a cat.\n")
    start, end = replace_match(doc, find_all(doc, "cat")[0], "👨‍👩‍👧 dog")
    cursor = QTextCursor(doc)
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    assert cursor.selectedText() == "👨‍👩‍👧 dog"
    assert replace_all(doc, "a", "🇩🇪") == 4 and doc.toPlainText() == \
        "🌸 🇩🇪 👨‍👩‍👧 dog 🇩🇪nd 🇩🇪 c🇩🇪t."


def test_a_link_whose_words_hold_an_emoji_ends_selected_whole():
    from PySide6.QtGui import QTextCursor, QTextDocument
    import rich_text
    doc = QTextDocument()
    doc.setPlainText("Read: ")
    cursor = QTextCursor(doc)
    cursor.movePosition(QTextCursor.MoveOperation.End)
    rich_text.set_link(cursor, "🌸 the 👨‍👩‍👧 post", "https://example.com/p")
    assert cursor.selectedText() == "🌸 the 👨‍👩‍👧 post"
