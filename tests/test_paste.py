# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins what a paste brings in (paste.py, rich_text.insert_document and
HtmlEditor.insertFromMimeData).

What must hold:

  HTML from Google Docs, Word, web pages and VS Code arrives as what
  Markdown can say of it: bold without its color, lists as lists, code
  blocks with their language, checklists, tables; links that could not
  work where the article is read lose their link, not their words;
  pictures not on the web are left out and the person is told.

  Plain text that clearly is Markdown is read as Markdown; other plain
  text arrives as it is. Inside a code block and in plain text files a
  paste is always plain text.

  Text copied here carries its Markdown, so a paste here keeps every
  structure exactly.

  A paste goes in the way typing it would: into an empty paragraph the
  first pasted paragraph keeps its style, inside a sentence it becomes
  words of that sentence, pasted items join the list they land next to,
  a table cell gets one line. It is one step to undo.

  Paste and Match Style inserts the text in the style where it goes.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDropEvent, QTextBlockFormat, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import paste  # noqa: E402
import rich_text  # noqa: E402
from editor import HtmlEditor  # noqa: E402
from markdown_writer import READ_FEATURES, document_to_markdown, image_markdown  # noqa: E402
from tests.rich_text_helpers import assert_round_trip, block_named, select  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def editor(markdown: str = "") -> HtmlEditor:
    ed = HtmlEditor()
    ed.document().setMarkdown(markdown, READ_FEATURES)
    rich_text.normalize_after_markdown_load(ed.document())
    return ed


def caret_at(ed, text: str, offset: int = 0) -> None:
    """The caret ``offset`` characters into the first ``text``."""
    cursor = select(ed.document(), text)
    cursor.setPosition(cursor.selectionStart() + offset)
    ed.setTextCursor(cursor)


def caret_in_empty_paragraph(ed, index: int) -> None:
    block = ed.document().findBlockByNumber(index)
    assert block.text() == ""
    ed.setTextCursor(QTextCursor(block))


def html(markup: str) -> QMimeData:
    mime = QMimeData()
    mime.setHtml(markup)
    return mime


def text(plain: str) -> QMimeData:
    mime = QMimeData()
    mime.setText(plain)
    return mime


def with_pictures(doc) -> str:
    return document_to_markdown(doc, lambda fmt: image_markdown(
        str(fmt.property(fmt.Property.ImageAltText) or ""), fmt.name()))


def pasted(mime: QMimeData) -> str:
    """The Markdown a paste of ``mime`` brings in on its own."""
    found = paste.from_mime(mime)
    assert found is not None
    return with_pictures(found.document)


# -- what comes in -------------------------------------------------------------------

GOOGLE_DOCS = (
    '<meta charset="utf-8"><b style="font-weight:normal;" id="docs-internal-guid-1a2b">'
    '<p dir="ltr" style="line-height:1.38;margin-top:0pt;margin-bottom:0pt;">'
    '<span style="font-size:11pt;font-family:Arial,sans-serif;color:#ff0000;'
    'font-weight:700;">Bold red</span><span style="font-size:11pt;font-family:Arial,'
    'sans-serif;color:#000000;font-weight:400;font-style:italic;"> and italic</span>'
    '<span style="font-size:11pt;font-family:\'Courier New\',monospace;"> code</span></p>'
    '<ul><li dir="ltr" style="list-style-type:disc;"><p dir="ltr"><span>one</span></p></li>'
    '<li dir="ltr"><p dir="ltr"><span>two</span></p></li></ul></b>')


def test_google_docs_arrives_as_markdown_without_its_colors_and_fonts():
    assert pasted(html(GOOGLE_DOCS)) == "**Bold red** *and italic* `code`\n\n- one\n- two\n"


WORD = (
    "<html xmlns:o='urn:schemas-microsoft-com:office:office'><body lang=DE>"
    "<p class=MsoNormal>Shopping:</p>"
    "<p class=MsoListParagraphCxSpFirst style='text-indent:-18.0pt;mso-list:l0 level1 lfo1'>"
    "<![if !supportLists]><span style='font-family:Symbol'><span style='mso-list:Ignore'>"
    "·<span style='font:7.0pt \"Times New Roman\"'>&nbsp;&nbsp;&nbsp; </span></span>"
    "</span><![endif]>Apples<o:p></o:p></p>\r\n"
    "<p class=MsoListParagraphCxSpMiddle style='mso-list:l0 level2 lfo1'>"
    "<![if !supportLists]><span style='font-family:\"Courier New\"'><span "
    "style='mso-list:Ignore'>o<span>&nbsp;&nbsp; </span></span></span><![endif]>Green</p>\r\n"
    "<p class=MsoListParagraphCxSpLast style='mso-list:l0 level1 lfo1'>"
    "<!--[if !supportLists]--><span><span style='mso-list:Ignore'>·<span>&nbsp; </span>"
    "</span></span><!--[endif]-->Pears</p>\r\n"
    "<p class=MsoNormal>Steps:</p>"
    "<p class=MsoListParagraph style='mso-list:l1 level1 lfo2'><![if !supportLists]><span>"
    "<span style='mso-list:Ignore'>1.<span>&nbsp;&nbsp; </span></span></span><![endif]>"
    "Wash</p></body></html>")


def test_word_list_paragraphs_become_lists():
    assert pasted(html(WORD)) == (
        "Shopping:\n\n- Apples\n    - Green\n- Pears\n\nSteps:\n\n1. Wash\n")
    found = paste.from_mime(html(WORD)).document
    assert block_named(found, "Apples").textList() is not None
    assert block_named(found, "Wash").textList() is not None


def test_html_lists_quotes_and_dividers_arrive():
    assert pasted(html("<ol start='3'><li>three</li><li>four</li></ol>"
                       "<blockquote><p>said <em>so</em></p></blockquote><hr><h2>Next</h2>")) == (
        "3. three\n4. four\n\n> said *so*\n\n---\n\n## Next\n")


@pytest.mark.parametrize("markup, language", [
    # Stack Overflow
    ('<pre class="lang-py s-code-block"><code class="hljs language-python">'
     '<span class="hljs-keyword">def</span> f():\n    return 1\n</code></pre>', "python"),
    # GitHub
    ('<div class="highlight highlight-source-js notranslate"><pre>let x = 1;\n'
     'x += 1;</pre></div>', "js"),
    # a page that names no language
    ('<pre>\nplain\n  indented\n</pre>', ""),
])
def test_a_pre_block_is_a_code_block_with_its_language(markup, language):
    markdown = pasted(html(markup))
    assert markdown.startswith(f"```{language}\n") and markdown.endswith("\n```\n")
    body = markdown.split("\n", 1)[1].rsplit("\n```", 1)[0]
    assert body in ("def f():\n    return 1", "let x = 1;\nx += 1;", "plain\n  indented")


def test_vs_code_says_which_language_it_copied():
    mime = QMimeData()
    mime.setText("def f():\n    return 1\n")
    mime.setData(paste.VSCODE_MIME, json.dumps({"version": 1, "mode": "python"}).encode())
    assert pasted(mime) == "```python\ndef f():\n    return 1\n```\n"
    one_line = QMimeData()
    one_line.setText("len(items)")
    one_line.setData(paste.VSCODE_MIME, b'{"mode": "python"}')
    assert pasted(one_line) == "`len(items)`\n"
    markdown = QMimeData()
    markdown.setText("# Title\n\nWords\n")
    markdown.setData(paste.VSCODE_MIME, b'{"mode": "markdown"}')
    assert pasted(markdown) == "# Title\n\nWords\n"


def test_checkboxes_make_a_checklist():
    markup = ('<ul><li class="task-list-item"><input type="checkbox" disabled checked> done</li>'
              '<li class="task-list-item"><input type="checkbox" disabled> open</li></ul>')
    assert pasted(html(markup)) == "- [x] done\n- [ ] open\n"


def test_inline_code_and_tables_arrive():
    assert pasted(html("<p>run <code>git log</code> or <kbd>q</kbd></p>"
                       "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr>"
                       "</table>")) == (
        "run `git log` or `q`\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n")


def test_links_that_cannot_work_where_it_is_read_keep_only_their_words():
    markup = ('<p><a href="https://example.com/a">web</a> <a href="/about">about</a> '
              '<a href="#top">top</a> <a href="javascript:alert(1)">bad</a> '
              '<a href="mailto:me@example.com">mail</a></p>')
    assert pasted(html(markup)) == (
        "[web](https://example.com/a) about top bad [mail](mailto:me@example.com)\n")


def test_pictures_not_on_the_web_are_left_out_and_counted():
    markup = ('<p>a <img src="https://example.com/cat.png" alt="A cat"> b '
              '<img src="file:///tmp/clip_image001.png"> '
              '<img src="data:image/png;base64,iVBORw0KGgo="></p>')
    found = paste.from_mime(html(markup))
    assert with_pictures(found.document) == "a ![A cat](https://example.com/cat.png) b\n"
    assert found.left_out == 2


@pytest.mark.parametrize("plain, markdown", [
    ("# Title\n\n- one\n- two\n", True),
    ("Some **bold** words and a [link](https://x.example)\n", True),
    ("| a | b |\n|---|---|\n| 1 | 2 |\n", True),
    ("Just a sentence.\n", False),
    ("- one item only\n", False),
    ("#hashtag and **one** mark\n", False),
])
def test_plain_text_is_markdown_only_when_it_clearly_is(plain, markdown):
    assert paste.looks_like_markdown(plain) is markdown


def test_a_markdown_table_pasted_as_text_becomes_a_table():
    ed = editor()
    ed.setTextCursor(QTextCursor(ed.document()))
    ed.insertFromMimeData(text("| a | b |\n|---|---|\n| 1 | 2 |\n"))
    cursor = QTextCursor(ed.document().findBlockByNumber(1))
    assert cursor.currentTable() is not None
    assert assert_round_trip(ed.document()) == "| a | b |\n| --- | --- |\n| 1 | 2 |\n"


# -- where it goes -------------------------------------------------------------------

def test_into_an_empty_paragraph_the_first_pasted_paragraph_keeps_its_style():
    ed = editor("Before\n\n- keep\n")
    cursor = QTextCursor(ed.document())
    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    cursor.insertBlock(QTextBlockFormat())
    ed.setTextCursor(cursor)
    ed.insertFromMimeData(text("## Heading\n\n- a\n- b\n"))
    assert assert_round_trip(ed.document()) == "Before\n\n## Heading\n\n- a\n- b\n- keep\n"
    assert ed.textCursor().block().text() == "b"


def test_inside_a_sentence_it_becomes_words_of_that_sentence():
    ed = editor("Hello world\n")
    caret_at(ed, "world")
    ed.insertFromMimeData(text("# Big\n\n- a\n- b"))
    markdown = assert_round_trip(ed.document())
    assert markdown == "Hello Big\n\n- a\n- bworld\n"
    first = ed.document().begin()
    assert not first.blockFormat().headingLevel()
    assert all(not rich_text.has_style(fmt, rich_text.BOLD)
               for _t, fmt in __import__("doc_walk").iter_block_runs(first))


def test_code_pasted_inside_a_sentence_gets_lines_of_its_own():
    ed = editor("Hello world\n")
    caret_at(ed, "world")
    mime = QMimeData()
    mime.setText("def f():\n    return 1\n")
    mime.setData(paste.VSCODE_MIME, b'{"mode": "python"}')
    ed.insertFromMimeData(mime)
    assert assert_round_trip(ed.document()) == (
        "Hello\n\n```python\ndef f():\n    return 1\n```\n\nworld\n")
    assert ed.textCursor().block().text() == "    return 1"


def test_pasted_items_join_the_numbered_list_they_land_in():
    ed = editor("1. one\n2. two\n")
    cursor = QTextCursor(block_named(ed.document(), "two"))
    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    ed.setTextCursor(cursor)
    ed.insertFromMimeData(text("1. three\n2. four\n"))
    assert assert_round_trip(ed.document()) == "1. one\n2. twothree\n3. four\n"


def test_into_a_heading_the_words_take_the_heading_style():
    ed = editor("# Title\n")
    caret_at(ed, "Title", 5)
    ed.insertFromMimeData(html("<p>, <b>bold</b> part</p>"))
    assert assert_round_trip(ed.document()) == "# Title, bold part\n"


def test_a_table_cell_gets_one_line():
    ed = editor("| a | b |\n| --- | --- |\n| 1 | 2 |\n")
    caret_at(ed, "1", 1)
    ed.insertFromMimeData(text("# x\n\n- y\n- z\n"))
    assert document_to_markdown(ed.document()) == "| a | b |\n| --- | --- |\n| 1x y z | 2 |\n"
    caret_at(ed, "2", 1)
    QApplication.clipboard().setText("p\nq")
    ed.paste_normalized()
    assert document_to_markdown(ed.document()) == "| a | b |\n| --- | --- |\n| 1x y z | 2p q |\n"


def test_a_paste_is_one_step_to_undo():
    ed = editor("Start\n")
    caret_at(ed, "Start", 5)
    ed.insertFromMimeData(html("<h2>Head</h2><ul><li>a</li></ul><p>end</p>"))
    assert "Head" in ed.toPlainText()
    ed.document().undo()
    assert document_to_markdown(ed.document()) == "Start\n"


def test_a_paste_on_a_divider_goes_below_it():
    ed = editor("Above\n\n---\n")
    divider = ed.document().findBlockByNumber(1)
    assert rich_text.is_divider(divider)
    ed.setTextCursor(QTextCursor(divider))
    ed.insertFromMimeData(text("## Below\n\n- item\n"))
    assert assert_round_trip(ed.document()) == "Above\n\n---\n\n## Below\n\n- item\n"


def test_in_a_code_block_a_paste_is_plain_text():
    ed = editor("```\ncode\n```\n")
    caret_at(ed, "code", 4)
    ed.insertFromMimeData(html("<p><b>**not**</b> bold</p>"))
    assert document_to_markdown(ed.document()) == "```\ncode**not** bold\n```\n"


def test_in_a_plain_text_file_a_paste_is_plain_text():
    ed = editor()
    ed.set_structure_check(lambda: False)
    ed.insertFromMimeData(text("# not a heading\n\n- nor a list\n"))
    assert ed.toPlainText() == "# not a heading\n\n- nor a list\n"
    assert not ed.document().begin().blockFormat().headingLevel()


def test_text_copied_here_pastes_back_exactly():
    source = editor("# Title\n\n- [ ] task\n- [x] done\n\n> quote\n\n```python\nx = 1\n```\n\n"
                    "Some **bold** and [a link](https://x.example).\n")
    source.selectAll()
    mime = source.createMimeDataFromSelection()
    assert mime.hasFormat(paste.MARKDOWN_MIME)
    target = editor()
    target.insertFromMimeData(mime)
    assert document_to_markdown(target.document()) == document_to_markdown(source.document())


def test_words_copied_from_a_heading_are_not_bold():
    source = editor("# A Long Title\n\nBody\n")
    source.setTextCursor(select(source.document(), "Long"))
    mime = source.createMimeDataFromSelection()
    assert bytes(mime.data(paste.MARKDOWN_MIME)).decode() == "Long\n"
    source.setTextCursor(select(source.document(), "A Long Title"))
    mime = source.createMimeDataFromSelection()
    assert bytes(mime.data(paste.MARKDOWN_MIME)).decode() == "# A Long Title\n"


def test_markdown_opened_as_its_text_gets_the_markdown():
    source = editor("## Part\n\n- item\n")
    source.selectAll()
    mime = source.createMimeDataFromSelection()
    target = HtmlEditor()
    target.set_structure_check(lambda: False)
    target._markdown_source = True
    target.insertFromMimeData(mime)
    assert target.toPlainText() == "## Part\n\n- item\n"


def test_a_paste_that_left_pictures_out_says_so():
    ed = editor()
    notices = []
    ed.notice.connect(notices.append)
    ed.insertFromMimeData(html('<p>text <img src="file:///tmp/a.png"></p>'))
    assert ed.toPlainText() == "text"
    assert len(notices) == 1 and "picture" in notices[0]


def test_text_dropped_arrives_like_a_paste():
    ed = editor("One\n")
    ed.resize(400, 200)
    ed.show()
    QApplication.processEvents()
    end = ed.cursorRect(QTextCursor(ed.document().lastBlock())).center()
    mime = html("<p><b>two</b></p>")         # the event does not keep it alive
    event = QDropEvent(QPointF(end.x() + 40, end.y()), Qt.DropAction.CopyAction,
                       mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    ed.dropEvent(event)
    assert document_to_markdown(ed.document()) == "One**two**\n"
    ed.hide()


def test_image_files_copied_in_a_file_manager_go_to_the_media_handler(tmp_path):
    ed = editor()
    seen = []
    ed.urls_dropped.connect(seen.append)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / "cat.png"))])
    mime.setText("cat.png")
    ed.insertFromMimeData(mime)
    assert len(seen) == 1 and ed.toPlainText() == ""
    other = QMimeData()
    other.setUrls([QUrl.fromLocalFile(str(tmp_path / "notes.pdf"))])
    other.setText("notes.pdf")
    ed.insertFromMimeData(other)
    assert len(seen) == 1 and ed.toPlainText() == "notes.pdf"


# -- Paste and Match Style ------------------------------------------------------------

def test_paste_and_match_style_takes_the_style_where_it_goes():
    ed = editor("# Title\n\nSome **bold** text and [a link](https://x.example)\n")
    QApplication.clipboard().setText("more")
    caret_at(ed, "Title", 5)
    ed.paste_normalized()
    caret_at(ed, "bold", 2)
    ed.paste_normalized()
    caret_at(ed, "a link", 6)
    ed.paste_normalized()
    assert document_to_markdown(ed.document()) == (
        "# Titlemore\n\nSome **bomoreld** text and [a link](https://x.example)more\n")


def test_paste_and_match_style_never_reads_markdown_or_html():
    ed = editor()
    mime = QMimeData()
    mime.setHtml("<h1>Big</h1>")
    mime.setText("# Big")
    QApplication.clipboard().setMimeData(mime)
    ed.paste_normalized()
    assert ed.toPlainText() == "# Big"
    assert not ed.document().begin().blockFormat().headingLevel()
