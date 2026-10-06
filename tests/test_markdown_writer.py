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

  Typed text is Markdown and is written as typed (footnote marks
  included); only a web address that holds a character a reader would
  take for emphasis is written as <address>, so it survives being read
  back.

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
    READ_FEATURES, document_to, document_to_markdown, document_to_note_text,
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def from_markdown(text: str) -> QTextDocument:
    doc = QTextDocument()
    doc.setMarkdown(text, READ_FEATURES)
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
    "Some *italic* via underscores reads as italic.\n",
    "> - quoted item\n> - another\n",
    "See <https://github.com/psf/requests/blob/main/src/requests/__init__.py> now.\n",
    "A [link](<https://x.example/a (b).png>) with parentheses.\n",
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


# -- typed Markdown -------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "## A heading typed by hand",
    "See [the docs](https://example.com) and **this**.",
    "> quoted by hand",
    "2 * 3 * 4 and snake_case",
    "#nostr #bitcoin",
    "- a list it looks like",
])
def test_typed_markdown_is_written_as_typed(text):
    assert document_to_markdown(typed(text)) == text + "\n"


@pytest.mark.parametrize("address", [
    "https://github.com/psf/requests/blob/main/src/requests/__init__.py",
    "https://example.com/_foo_/bar",
    "wss://relay.example/x*y",
])
def test_an_address_with_markup_characters_is_kept_whole(address):
    written = document_to_markdown(typed(f"see {address} now"))
    assert written == f"see <{address}> now\n"
    # Read back and written again, it is the same address.
    assert document_to_markdown(from_markdown(written)) == written


def test_footnote_marks_are_written_as_typed():
    # A reference and its definition, typed by hand, reach the reader as
    # Markdown footnotes: nothing in them is escaped.
    doc = typed("A claim[^1] and another[^note].\n\n[^1]: The source.\n[^note]: More.")
    assert document_to_markdown(doc) == (
        "A claim[^1] and another[^note].\n\n[^1]: The source.\n\n[^note]: More.\n")
    assert document_to_note_text(doc) == (
        "A claim[^1] and another[^note].\n\n[^1]: The source.\n[^note]: More.")


FOOTNOTES = ("A claim[^1] and another[^2].\n\n[^1]: https://example.com/source\n\n"
             "[^2]: Ibid.\n")


def test_footnotes_read_back_as_the_text_they_are():
    # Review H3: Qt's reader took "[^1]: Ibid." for a link definition,
    # dropped it and made the mark a link.
    from markdown_writer import read_markdown
    doc = QTextDocument()
    read_markdown(doc, FOOTNOTES)
    assert doc.toPlainText() == ("A claim[^1] and another[^2].\n"
                                 "[^1]: https://example.com/source\n[^2]: Ibid.")
    assert document_to_markdown(doc) == FOOTNOTES


@pytest.mark.parametrize("markdown, kept", [
    ("[^1]: Ibid.\n", "\\[^1]: Ibid.\n"),
    ("> [^q]: quoted\n", "> \\[^q]: quoted\n"),
    ("```\n[^1]: code\n```\n", "```\n[^1]: code\n```\n"),
    ("~~~~\n[^1]: code\n~~~\n[^2]: still code\n~~~~\n[^3]: text\n",
     "~~~~\n[^1]: code\n~~~\n[^2]: still code\n~~~~\n\\[^3]: text\n"),
    ("A claim[^1].\n", "A claim[^1].\n"),
])
def test_only_definitions_outside_code_are_marked_literal(markdown, kept):
    from markdown_writer import literal_footnotes
    assert literal_footnotes(markdown) == kept


def test_a_footnote_mark_in_bold_text_stays_one():
    doc = typed(("a claim[^1]", {"bold": True}))
    assert document_to_markdown(doc) == "**a claim[^1]**\n"


@pytest.mark.parametrize("address", ["https://example.com/plain", "nostr:npub1abcdef"])
def test_a_plain_address_stays_bare(address):
    assert document_to_markdown(typed(f"see {address}")) == f"see {address}\n"


def test_underscores_read_back_as_italic_not_underline():
    doc = from_markdown("an _emphasized_ word\n")
    assert document_to_markdown(doc) == "an *emphasized* word\n"


def test_indented_code_stays_code():
    doc = from_markdown("Intro\n\n    x = a*b*c\n    if x_y_z: pass\n")
    assert document_to_markdown(doc) == "Intro\n\n```\nx = a*b*c\nif x_y_z: pass\n```\n"


def test_a_link_that_shows_its_address_is_an_autolink():
    doc = typed(("https://x.example/a_b", {"href": "https://x.example/a_b"}))
    assert document_to_markdown(doc) == "<https://x.example/a_b>\n"


@pytest.mark.parametrize("words, href, written", [
    ("https://example.com/photo.jpg", "https://example.com/photo.jpg",
     "https://example.com/photo.jpg"),
    ("www.example.com", "http://www.example.com", "www.example.com"),
    ("ada@example.com", "mailto:ada@example.com", "ada@example.com"),
])
def test_a_link_that_shows_its_own_address_is_written_bare(words, href, written):
    doc = typed(("see ", {}), (words, {"href": href}), (". Next", {}))
    out = document_to_markdown(doc)
    assert out == f"see {written}. Next\n"
    # Read back, it is the same link, written the same way.
    assert document_to_markdown(from_markdown(out)) == out


@pytest.mark.parametrize("address", [
    "https://de.wikipedia.org/wiki/M%C3%BCnchen",          # a percent sign
    "https://example.com:8080/x",                          # a port
    "https://mastodon.social/@user",                       # an @
    "https://example.com/a,b",                             # a comma
    "https://example.com/wow!",                            # an exclamation mark
    "https://example.com/c++",                             # a plus
    "https://example.com/page_(info)",                     # parentheses
    "https://example.com/cdn-cgi/image/width=80,quality=75/a.jpg",
])
def test_an_own_address_qt_would_not_read_back_whole_is_kept_whole(address):
    # Review F2: written bare, these came back as no link, or cut short.
    from markdown_writer import holds_faithfully
    doc = typed(("see ", {}), (address, {"href": address}), (" now", {}))
    out = document_to_markdown(doc)
    assert out == f"see <{address}> now\n"
    back = from_markdown(out)
    links = [(text, fmt.anchorHref()) for block in [back.begin()]
             for text, fmt in __import__("doc_walk").iter_block_runs(block) if fmt.isAnchor()]
    assert links == [(address, address)]
    assert document_to_markdown(back) == out
    assert holds_faithfully(out)             # an older file with it opens formatted


def test_a_media_address_alone_on_its_line_stays_bare():
    url = "https://cdn.example/clip.mp4"
    doc = typed("Watch this:\n", (url, {"href": url}), "\nThanks")
    assert document_to_markdown(doc) == f"Watch this:\n\n{url}\n\nThanks\n"


@pytest.mark.parametrize("after", ["word", "-x", "/more"])
def test_an_own_address_followed_by_text_is_kept_whole(after):
    url = "https://example.com"
    out = document_to_markdown(typed((url, {"href": url}), after))
    assert out == f"<{url}>{after}\n"


def test_an_own_address_after_a_word_is_kept_whole():
    url = "https://example.com"
    assert document_to_markdown(typed("see:", (url, {"href": url}))) == f"see:<{url}>\n"


def test_a_link_label_with_a_bracket_is_escaped():
    doc = typed(("see [1]", {"href": "https://x.example"}))
    assert document_to_markdown(doc) == "[see [1\\]](https://x.example)\n"


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


def test_a_note_keeps_typed_lines_and_empty_lines_exactly():
    doc = typed("one\ntwo\n\n\nthree")
    assert document_to_note_text(doc) == "one\ntwo\n\n\nthree"


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


# -- what the editor can hold ------------------------------------------------------------

from markdown_writer import holds_faithfully  # noqa: E402


@pytest.mark.parametrize("source", ROUND_TRIPS + [
    "# Imported\n\nSome **bold** and a [link](https://x.example).\n\n- one\n- two\n",
    "* star bullets\n* are fine\n",
    "Setext heading\n==============\n\nText.\n",
])
def test_markdown_the_editor_can_hold(source):
    assert holds_faithfully(source)


@pytest.mark.parametrize("source", [
    "Footnote[^1].\n\n[^1]: The note.\n",
    "<div>raw</div>\n",
    "An image ![](https://x.example/a.png) without alt text.\n",
    'A [link](https://x.example "with a title").\n',
    "[![badge](https://x.example/b.png)](https://x.example)\n",
    "> # a heading in a quote\n",
    "- item\n\n  second paragraph of the item\n- two\n",
    "Line one\nLine two of the same paragraph\n",
])
def test_markdown_the_editor_would_lose(source):
    assert not holds_faithfully(source)


def test_a_draft_the_editor_cannot_hold_opens_as_its_text_and_comes_back_unchanged():
    from editor import HtmlEditor
    from main_window import MainWindow
    from nostr.drafts import INNER_KIND_LONG_FORM

    source = "Footnote[^1] and ![](https://x.example/a.png)\n\n[^1]: The note.\n"
    ed = HtmlEditor()
    MainWindow._load_draft_content(ed, draft(INNER_KIND_LONG_FORM, source))
    assert ed._markdown_source
    assert ed.toPlainText() == source
    stub = type("Stub", (), {"_asset_manager": None})()
    content, _media = MainWindow._publish_payload(stub, ed, "markdown")
    assert content == source.rstrip("\n")


def test_undo_right_after_opening_a_draft_keeps_it():
    from editor import HtmlEditor
    from main_window import MainWindow
    from nostr.drafts import INNER_KIND_LONG_FORM

    ed = HtmlEditor()
    MainWindow._load_draft_content(ed, draft(INNER_KIND_LONG_FORM, "# Title\n\nBody\n"))
    ed.document().undo()
    assert "Body" in ed.toPlainText()
