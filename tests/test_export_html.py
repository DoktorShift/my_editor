"""Pins the handrolled HTML exporter and its load-time normalization.

The defect classes this file guards against:
- bullet nesting drift (ragged indents, depth jumps, 0-space bullets),
- marker formatting leaking into <li> content when "• " straddles
  fragments,
- round-trip damage: Qt re-parsing our own output must reproduce the
  document (the ONLY accepted change is 0-space bullets normalizing to
  4 spaces),
- images silently losing their bytes instead of embedding as data URIs.
"""

import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import (
    QColor,
    QImage,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)

from export_html import (
    document_to_html,
    normalize_lists_after_set_html,
    sniff_image_ext,
    sniff_image_mime,
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


PLAIN = QTextCharFormat()


def _bold():
    fmt = QTextCharFormat()
    fmt.setFontWeight(700)
    return fmt


def _red():
    fmt = QTextCharFormat()
    fmt.setForeground(QColor("#E53935"))
    return fmt


def _doc(lines):
    """Build a document from (text, fmt) lines; fmt defaults to plain."""
    doc = QTextDocument()
    cur = QTextCursor(doc)
    first = True
    for entry in lines:
        if not first:
            cur.insertBlock()
        first = False
        if isinstance(entry, str):
            cur.insertText(entry, PLAIN)
        else:
            for text, fmt in entry:
                cur.insertText(text, fmt)
    return doc


# --------------------------------------------------------------------------- #
# Semantic structure
# --------------------------------------------------------------------------- #

def test_paragraphs_and_inline_formatting():
    doc = _doc([
        [("plain with ", PLAIN), ("bold", _bold()), (" and ", PLAIN),
         ("red", _red())],
    ])
    out = document_to_html(doc, "T")
    assert "<p>plain with <strong>bold</strong> and " in out
    assert '<span style="color:#e53935">red</span>' in out
    assert "<!doctype html>" in out
    assert "<title>T</title>" in out


def test_escaping():
    doc = _doc(['a < b & c > "d"'])
    out = document_to_html(doc, "<Esc> & Co")
    assert "a &lt; b &amp; c &gt; &quot;d&quot;" in out or \
           "a &lt; b &amp; c &gt; \"d\"" in out
    assert "<title>&lt;Esc&gt; &amp; Co</title>" in out


def test_bullets_nest_by_indent():
    doc = _doc([
        "    • one",
        "        • two",
        "    • three",
    ])
    out = document_to_html(doc, "T")
    assert "<ul><li>one<ul><li>two</li></ul></li><li>three</li></ul>" in out


def test_zero_space_bullet_is_depth_one():
    doc = _doc(["• zero", "    • four"])
    out = document_to_html(doc, "T")
    # Both land at depth 1 in one list.
    assert "<ul><li>zero</li><li>four</li></ul>" in out


def test_ragged_depth_jump_opens_and_closes_levels():
    doc = _doc([
        "• a",
        "            • deep",   # 12 spaces = depth 3, jumping from 1
        "• b",
    ])
    out = document_to_html(doc, "T")
    assert ("<ul><li>a<ul><li><ul><li>deep</li></ul>"
            "</li></ul></li><li>b</li></ul>") in out


def test_marker_formatting_never_leaks():
    # The "    • " prefix carries bold, the content does not; slicing the
    # straddling fragment must drop the marker's formatting.
    doc = _doc([[("    • plain content", PLAIN)]])
    bold_marker = _doc([[("    • ", _bold()), ("content", PLAIN)]])
    out = document_to_html(bold_marker, "T")
    assert "<li>content</li>" in out
    assert "<strong>" not in out
    out2 = document_to_html(doc, "T")
    assert "<li>plain content</li>" in out2


def test_empty_paragraph_and_pre_wrap():
    doc = _doc(["a", "", "  indented", "b"])
    out = document_to_html(doc, "T")
    assert "<p>&nbsp;</p>" in out
    assert '<p style="white-space:pre-wrap">  indented</p>' in out


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #

def _png_at(tmp_path, name="a" * 64):
    img = QImage(4, 4, QImage.Format_RGB32)
    img.fill(QColor("teal"))
    path = str(tmp_path / name)  # bare sha-style name, no extension
    img.save(path, "PNG")
    return path


def test_image_embeds_as_data_uri_with_provenance(tmp_path):
    path = _png_at(tmp_path)
    doc = QTextDocument()
    QTextCursor(doc).insertImage(path)
    out = document_to_html(doc, "T",
                           source_url_for=lambda p: "https://x.example/i.png")
    assert 'src="data:image/png;base64,' in out
    assert 'data-source-url="https://x.example/i.png"' in out
    # The payload must actually decode back to the file bytes.
    b64 = out.split("base64,")[1].split('"')[0]
    assert base64.b64decode(b64) == open(path, "rb").read()


def test_missing_image_falls_back_to_url_then_placeholder(tmp_path):
    gone = str(tmp_path / ("f" * 64))
    doc = QTextDocument()
    QTextCursor(doc).insertImage(gone)
    with_url = document_to_html(doc, "T",
                                source_url_for=lambda p: "https://x.example/i.png")
    assert 'src="https://x.example/i.png"' in with_url
    without = document_to_html(doc, "T")
    assert "[image unavailable]" in without


def test_mime_and_ext_sniffing():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    jpg = b"\xff\xd8\xff\xe0" + b"\x00" * 8
    assert sniff_image_mime(png) == "image/png"
    assert sniff_image_mime(jpg) == "image/jpeg"
    assert sniff_image_ext(png) == ".png"
    assert sniff_image_ext(jpg) == ".jpg"
    assert sniff_image_mime(b"not an image") is None
    assert sniff_image_ext(b"not an image") is None


# --------------------------------------------------------------------------- #
# Round-trip: export -> setHtml -> normalize
# --------------------------------------------------------------------------- #

def _roundtrip(doc, title="T"):
    out = document_to_html(doc, title)
    doc2 = QTextDocument()
    doc2.setHtml(out)
    normalize_lists_after_set_html(doc2)
    return doc2


def test_roundtrip_preserves_text_and_structure():
    doc = _doc([
        "Title line",
        [("mixed ", PLAIN), ("bold", _bold()), (" tail", PLAIN)],
        "",
        "    • one",
        "        • two",
        "    • three",
        "  indented plain",
        "last",
    ])
    doc2 = _roundtrip(doc)
    assert doc2.toPlainText() == doc.toPlainText()


def test_roundtrip_normalizes_zero_space_bullet_to_four():
    doc = _doc(["• zero"])
    doc2 = _roundtrip(doc)
    assert doc2.toPlainText() == "    • zero"


def test_roundtrip_preserves_inline_formats():
    doc = _doc([[("b", _bold()), ("r", _red())]])
    doc2 = _roundtrip(doc)
    block = doc2.begin()
    runs = []
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        runs.append((frag.text(), frag.charFormat()))
        it += 1
    joined = "".join(t for t, _f in runs)
    assert joined == "br"
    bold_run = next(f for t, f in runs if "b" in t)
    red_run = next(f for t, f in runs if "r" in t)
    assert bold_run.fontWeight() > 400
    assert red_run.foreground().color().name() == "#e53935"


def test_roundtrip_keeps_embedded_image(tmp_path):
    # After reopening, the image must still be an inline object (loaded
    # from the data URI, not from the original cache path).
    img = QImage(4, 4, QImage.Format_RGB32)
    img.fill(QColor("teal"))
    path = str(tmp_path / ("b" * 64))
    img.save(path, "PNG")
    doc = QTextDocument()
    cur = QTextCursor(doc)
    cur.insertText("x ", PLAIN)
    cur.insertImage(path)
    doc2 = _roundtrip(doc)
    assert "\ufffc" in doc2.toPlainText()


def test_normalization_converts_foreign_lists():
    # Foreign HTML with real lists (pretty-printed whitespace included)
    # becomes literal bullet lines with a neutral marker format.
    doc = QTextDocument()
    doc.setHtml("<ul>\n  <li>alpha</li>\n  <li>beta\n    <ul><li>gamma</li></ul>\n  </li>\n</ul>")
    normalize_lists_after_set_html(doc)
    text = doc.toPlainText()
    assert "    • alpha" in text
    assert "    • beta" in text
    assert "        • gamma" in text
