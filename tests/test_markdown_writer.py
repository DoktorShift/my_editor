# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the one Markdown writer behind .md files, articles and drafts.

What must hold:

  What the editor shows is what is written: bold, italic, strikethrough,
  inline code, links, headings, lists (Qt's and the editor's typed
  bullets), quotes, code blocks, rules, tables and task lists.

  A .md file read and written again comes back the same.

  Lines typed one under the other stay separate, as paragraphs: the one
  break every Nostr reader shows alike.

  Typed characters that Markdown would read as markup are escaped, and
  web addresses and nostr: links are never touched.

  A short note is plain text: no markup, list markers and link addresses
  kept.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import (  # noqa: E402
    QFont, QTextCharFormat, QTextCursor, QTextDocument, QTextImageFormat,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

from markdown_writer import (  # noqa: E402
    NBSP, document_to, document_to_markdown, document_to_note_text,
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def from_markdown(text: str) -> QTextDocument:
    doc = QTextDocument()
    doc.setMarkdown(text)
    return doc


def typed(*pieces) -> QTextDocument:
    """A document typed in the editor: ``(text, {format})`` pieces, with
    "\\n" starting a new block as Enter does."""
    doc = QTextDocument()
    cursor = QTextCursor(doc)
    for piece in pieces:
        text, fmt = (piece, {}) if isinstance(piece, str) else piece
        char = QTextCharFormat()
        if fmt.get("bold"):
            char.setFontWeight(QFont.Weight.Bold)
        if fmt.get("italic"):
            char.setFontItalic(True)
        if fmt.get("strike"):
            char.setFontStrikeOut(True)
        if fmt.get("underline"):
            char.setFontUnderline(True)
        if fmt.get("code"):
            char.setFontFixedPitch(True)
        if fmt.get("href"):
            char.setAnchor(True)
            char.setAnchorHref(fmt["href"])
        lines = text.split("\n")
        for index, line in enumerate(lines):
            if index:
                cursor.insertBlock()
            cursor.insertText(line, char)
    return doc


# -- a .md file comes back the same --------------------------------------------------

ROUND_TRIPS = [
    "# Title\n\nSome text.\n",
    "Some **bold** and *italic* and ~~strike~~ and `code`.\n",
    "A [link](https://example.com/a_b) in text.\n",
    "## Second\n\n### Third\n",
    "- one\n- two\n    - nested\n- three\n",
    "1. first\n2. second\n3. third\n",
    "- [ ] todo\n- [x] done\n",
    "> quoted text\n",
    "```python\nprint(1)\n\nprint(2)\n```\n",
    "Before\n\n---\n\nAfter\n",
    "| a | b |\n| --- | --- |\n| 1 | 2 |\n",
    "Para one\n\nPara two\n",
]


@pytest.mark.parametrize("source", ROUND_TRIPS)
def test_a_markdown_file_comes_back_the_same(source):
    once = document_to_markdown(from_markdown(source))
    assert once == source
    # And it is stable: writing what was written changes nothing.
    assert document_to_markdown(from_markdown(once)) == once


def test_a_quote_with_two_paragraphs_stays_one_quote():
    out = document_to_markdown(from_markdown("> one\n>\n> two\n"))
    assert out == "> one\n>\n> two\n"


# -- what is typed in the editor ------------------------------------------------------

def test_typed_formatting_reaches_markdown():
    doc = typed("Plain ", ("bold", {"bold": True}), " and ", ("it", {"italic": True}),
                " and ", ("gone", {"strike": True}), " and ", ("x = 1", {"code": True}), ".")
    assert document_to_markdown(doc) == (
        "Plain **bold** and *it* and ~~gone~~ and `x = 1`.\n")


def test_spaces_stay_outside_the_markers():
    doc = typed("a", (" bold ", {"bold": True}), "b")
    assert document_to_markdown(doc) == "a **bold** b\n"


def test_underline_is_for_local_files_only():
    doc = typed("an ", ("underlined", {"underline": True}), " word")
    assert document_to_markdown(doc) == "an underlined word\n"


def test_a_link_keeps_its_words_and_address():
    doc = typed("see ", ("the site", {"href": "https://example.com"}), " now")
    assert document_to_markdown(doc) == "see [the site](https://example.com) now\n"


def test_lines_typed_one_under_the_other_stay_separate():
    doc = typed("Roses are red\nViolets are blue\n\nNew stanza")
    assert document_to_markdown(doc) == (
        "Roses are red\n\nViolets are blue\n\nNew stanza\n")


def test_several_empty_lines_are_one_paragraph_break():
    doc = typed("one\n\n\n\ntwo")
    assert document_to_markdown(doc) == "one\n\ntwo\n"


def test_typed_bullets_become_a_markdown_list():
    doc = typed("Shopping:\n    • milk\n    • bread\n        • rye\n\nDone")
    assert document_to_markdown(doc) == (
        "Shopping:\n\n- milk\n- bread\n    - rye\n\nDone\n")


def test_shift_enter_is_a_hard_line_break():
    doc = typed("first second")
    assert document_to_markdown(doc) == "first  \nsecond\n"


def test_a_typed_indent_shows_instead_of_becoming_code():
    doc = typed("        indented line")
    assert document_to_markdown(doc) == NBSP * 8 + "indented line\n"


# -- escaping -----------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("2 * 3 * 4", "2 \\* 3 \\* 4"),
    ("snake_case stays", "snake_case stays"),
    ("_not italic_", "\\_not italic\\_"),
    ("a `tick`", "a \\`tick\\`"),
    ("[not a link](x)", "\\[not a link\\](x)"),
    ("<b>html</b>", "\\<b>html\\</b>"),
    ("# not a heading", "\\# not a heading"),
    ("> not a quote", "\\> not a quote"),
    ("- a list it looks like", "- a list it looks like"),
    ("1. a numbered line", "1. a numbered line"),
    ("~tilde~", "\\~tilde\\~"),
    ("back\\slash", "back\\\\slash"),
])
def test_typed_markup_characters_show_as_typed(text, expected):
    assert document_to_markdown(typed(text)) == expected + "\n"


@pytest.mark.parametrize("address", [
    "https://example.com/a_b*c_d",
    "nostr:npub1abc_def",
    "wss://relay.example/x_y",
])
def test_addresses_are_never_escaped(address):
    assert document_to_markdown(typed(f"see {address} now")) == f"see {address} now\n"


def test_backticks_inside_code_get_a_longer_fence():
    doc = typed(("a `b` c", {"code": True}))
    assert document_to_markdown(doc) == "``a `b` c``\n"


# -- images ---------------------------------------------------------------------------

def image_doc():
    doc = QTextDocument()
    cursor = QTextCursor(doc)
    cursor.insertText("before ")
    image = QTextImageFormat()
    image.setName("myeditor-asset://abc")
    cursor.insertImage(image)
    cursor.insertText(" after")
    return doc


def test_images_are_written_where_the_caller_says():
    out = document_to_markdown(image_doc(), lambda fmt: f"![pic](https://cdn/{fmt.name()[-3:]})")
    assert out == "before ![pic](https://cdn/abc) after\n"


def test_an_image_without_a_destination_is_left_out():
    assert document_to_markdown(image_doc(), lambda fmt: None) == "before  after\n"
    assert document_to_markdown(image_doc()) == "before  after\n"


# -- a short note ------------------------------------------------------------------------

def test_a_note_carries_no_markup():
    doc = typed(("Bold", {"bold": True}), " and ", ("code", {"code": True}), " and 2 * 3")
    assert document_to_note_text(doc) == "Bold and code and 2 * 3"


def test_a_note_keeps_list_markers_and_link_addresses():
    doc = from_markdown("# Heading\n\n- one\n- two\n\n1. first\n\nA [site](https://x.example).\n")
    assert document_to_note_text(doc) == (
        "Heading\n\n• one\n• two\n\n1. first\n\nA site (https://x.example).")


def test_a_note_keeps_typed_bullets_exactly():
    doc = typed("List:\n    • a\n        • b")
    assert document_to_note_text(doc) == "List:\n    • a\n        • b"


def test_a_link_that_is_its_own_address_is_written_once():
    doc = typed(("https://x.example", {"href": "https://x.example"}))
    assert document_to_note_text(doc) == "https://x.example"


def test_a_note_keeps_typed_lines_and_paragraphs():
    doc = typed("one\ntwo\n\n\nthree")
    assert document_to_note_text(doc) == "one\ntwo\n\nthree"


def test_flavors_by_name():
    doc = typed(("b", {"bold": True}))
    assert document_to(doc, "markdown") == "**b**\n"
    assert document_to(doc, "note") == "b"


# -- drafts open the way they were written ------------------------------------------------

def draft(kind, content):
    from types import SimpleNamespace
    return SimpleNamespace(inner_kind=kind, content=content)


def test_an_article_draft_opens_formatted_and_saves_back_unescaped():
    from editor import HtmlEditor
    from main_window import MainWindow
    from nostr.drafts import INNER_KIND_LONG_FORM

    ed = HtmlEditor()
    source = "# Imported\n\nSome **bold** and a [link](https://x.example).\n\n- one\n- two\n"
    MainWindow._load_draft_content(ed, draft(INNER_KIND_LONG_FORM, source))
    assert "**" not in ed.toPlainText()          # shown as formatting, not markup
    assert document_to_markdown(ed.document()) == source


def test_a_note_draft_opens_as_typed():
    from editor import HtmlEditor
    from main_window import MainWindow
    from nostr.drafts import INNER_KIND_SHORT_NOTE

    ed = HtmlEditor()
    MainWindow._load_draft_content(ed, draft(INNER_KIND_SHORT_NOTE, "line one\nline *two*"))
    assert ed.toPlainText() == "line one\nline *two*"
    assert document_to_note_text(ed.document()) == "line one\nline *two*"
