# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the preview: a note or an article the way Nostr apps show it.

What must hold:

  An article renders its Markdown (headings, emphasis, lists, quotes,
  code, links) under a header of title, summary and byline with the
  reading time, and its tags after the body.

  A note is plain text: ``**`` stays literal, line breaks stay, links,
  hashtags and mentions are colored, and images the note ends with show
  below the text instead of as an address.

  A reference to a person reads "@Name" (or a shortened key), to a note
  or an article as a labelled link; references inside code stay as
  written. Raw HTML in an article shows as text, as in the apps.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont, QTextDocument  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr import bech32  # noqa: E402
from nostr import preview as pv  # noqa: E402

PUBKEY = "ab" * 32
NPUB = bech32.encode_npub(PUBKEY)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def names(pubkey):
    return "Alice" if pubkey == PUBKEY else None


def blocks(doc: QTextDocument):
    block = doc.begin()
    while block.isValid():
        yield block
        block = block.next()


# -- references -----------------------------------------------------------------------

def test_a_person_reads_as_their_name_or_a_short_key():
    assert pv.reference_label(f"nostr:{NPUB}", names) == "@Alice"
    other = bech32.encode_npub("cd" * 32)
    label = pv.reference_label(f"nostr:{other}", names)
    assert label.startswith("@npub1") and "…" in label


def test_notes_and_articles_read_as_labelled_links():
    nevent = bech32.encode_note("ef" * 32)
    assert pv.reference_label(f"nostr:{nevent}", names) == "Quoted note"


def test_references_in_code_stay_as_written():
    md = f"See nostr:{NPUB}\n\n```\nnostr:{NPUB}\n```\n\nand `nostr:{NPUB}`"
    out = pv.markdown_for_preview(md, names)
    assert out.startswith(f"See [@Alice](nostr:{NPUB})")
    assert f"```\nnostr:{NPUB}\n```" in out
    assert f"`nostr:{NPUB}`" in out


# -- articles ----------------------------------------------------------------------------

ARTICLE = pv.Article(title="Why self-custody matters", summary="Keys, not promises.",
                     tags=("bitcoin", "selfcustody"), author="Alice")


def test_an_article_has_its_header_body_and_tags():
    md = "## Start small\n\nSome **bold** words.\n\n- one\n- two\n\n> quoted\n"
    doc = pv.article_document(md, ARTICLE, names=names)
    text = doc.toPlainText()
    assert text.index("Why self-custody matters") < text.index("Keys, not promises.")
    assert "Alice · " in text and "1 min read" in text
    assert text.index("Start small") < text.index("#bitcoin   #selfcustody")
    heading = next(b for b in blocks(doc) if b.text() == "Start small")
    assert heading.blockFormat().headingLevel() == 2
    bold = [f for b in blocks(doc) for f in _fragments(b) if f.text() == "bold"]
    assert bold and bold[0].charFormat().fontWeight() >= QFont.Weight.Bold


def _fragments(block):
    it = block.begin()
    while not it.atEnd():
        yield it.fragment()
        it += 1


def test_a_table_after_code_keeps_its_header():
    md = "```\ncode\n```\n\n| Step | Time |\n| --- | --- |\n| Backup | 10 min |\n"
    doc = pv.article_document(md, pv.Article(title="T"))
    step = next(b for b in blocks(doc) if b.text() == "Step")
    assert step.previous().text() != ""                  # no stray empty line
    assert next(_fragments(step)).charFormat().fontWeight() >= QFont.Weight.Bold


def test_reading_time_follows_the_words():
    assert pv.reading_minutes("word " * 225) == 1
    assert pv.reading_minutes("word " * 226) == 2


def test_raw_html_shows_as_text_in_an_article():
    doc = pv.article_document("<b>not bold</b> text\n", pv.Article(title="T"))
    assert "<b>not bold</b>" in doc.toPlainText()


def test_an_untitled_article_says_so():
    doc = pv.article_document("Body\n", pv.Article())
    assert doc.toPlainText().startswith("Untitled")


# -- notes ----------------------------------------------------------------------------

def test_a_note_is_plain_text_with_its_line_breaks():
    doc = pv.note_document("Hello **world**\nsecond line", author="Alice")
    text = doc.toPlainText()
    assert "Hello **world**\nsecond line" in text
    assert text.startswith("Alice")


def test_images_that_end_a_note_show_below_it():
    text, media = pv.split_media("Look at this https://x.example/a.jpg")
    assert text == "Look at this" and media == ["https://x.example/a.jpg"]
    html = pv.note_html("Look https://x.example/a.jpg")
    assert '<img src="https://x.example/a.jpg"' in html


def test_an_image_in_the_middle_of_a_note_keeps_its_place_too():
    text, media = pv.split_media("A https://x.example/a.png B")
    assert text == "A https://x.example/a.png B" and media == ["https://x.example/a.png"]


def test_links_hashtags_and_mentions_are_colored_and_text_is_escaped():
    html = pv.note_html(f"<i>hi</i> #nostr https://x.example nostr:{NPUB}", names=names)
    assert "&lt;i&gt;hi&lt;/i&gt;" in html
    assert '<a href="https://x.example"' in html
    assert ">#nostr</span>" in html and ">@Alice</span>" in html


def test_many_empty_lines_show_as_one():
    html = pv.note_html("a\n\n\n\nb")
    assert "a\n\nb" in html


# -- the widget ----------------------------------------------------------------------------

def test_the_widget_uses_only_the_images_it_is_given():
    seen = []
    view = pv.NostrPreview(images=lambda url: seen.append(url) or None)
    view.show_note("Pic https://x.example/a.png")
    view.resize(900, 600)
    view.show()
    QApplication.processEvents()
    assert seen == ["https://x.example/a.png"]
    view.close()
