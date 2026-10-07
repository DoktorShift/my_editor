# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins code blocks (rich_text.make_code_block and the editor's keys).

What must hold:

  Insert > Code Block makes the paragraphs under the cursor the lines of
  one code block (a heading becomes Body, a list item leaves its list,
  words lose their styles), as one step to undo. Its language is the
  fence's ("```python"); Format > Code Language changes it for the whole
  block. Markdown writes one fenced block with the language.

  In a code block: Tab indents to the next multiple of four spaces,
  Shift+Tab takes up to four away (on every line selected); Return
  starts the next line as far in as this one; Return on the empty last
  line, Down on the document's last line and Backspace at the start of
  the block are the keyboard's ways out. A paragraph style (Body)
  chosen on a line of code makes it text again.

  With Markdown Shortcuts, "```python" (or "~~~") and Return start a
  code block. A paste into code is plain text, never a link. The editor
  paints a box behind each code block.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, Qt  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import rich_text  # noqa: E402
from editor import HtmlEditor  # noqa: E402
from markdown_writer import document_to_markdown, read_markdown  # noqa: E402
from tests.rich_text_helpers import (  # noqa: E402
    assert_round_trip, block_named, from_markdown, press, select, type_text,
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def whole(doc) -> QTextCursor:
    cursor = QTextCursor(doc)
    cursor.select(QTextCursor.SelectionType.Document)
    return cursor


def at(doc, text: str, offset: int = 0) -> QTextCursor:
    """A caret ``offset`` characters into the first ``text``."""
    cursor = QTextCursor(doc)
    cursor.setPosition(doc.toPlainText().index(text) + offset)
    return cursor


# -- the structure ---------------------------------------------------------------------------

def test_paragraphs_become_one_code_block_in_a_language():
    doc = from_markdown("# Title\n\nx = 1\n\ny = 2\n")
    cursor = select(doc, "x = 1")
    cursor.setPosition(doc.toPlainText().index("y = 2") + 2, QTextCursor.MoveMode.KeepAnchor)
    rich_text.make_code_block(cursor, "python")
    assert assert_round_trip(doc) == "# Title\n\n```python\nx = 1\ny = 2\n```\n"


def test_a_heading_a_list_item_and_styled_words_become_plain_code():
    doc = from_markdown("## Setup\n\n- **pip** install\n")
    rich_text.make_code_block(whole(doc), "")
    assert assert_round_trip(doc) == "```\nSetup\npip install\n```\n"


def test_a_code_block_is_one_step_to_undo():
    doc = from_markdown("one\n\ntwo\n")
    rich_text.make_code_block(whole(doc), "sh")
    doc.undo()
    assert document_to_markdown(doc) == "one\n\ntwo\n"


def test_the_language_changes_for_the_whole_block():
    doc = from_markdown("```\nfn main() {\n    x();\n}\n```\n\ntext\n")
    rich_text.set_code_language(at(doc, "x();"), "rust")
    assert assert_round_trip(doc) == "```rust\nfn main() {\n    x();\n}\n```\n\ntext\n"
    doc.undo()
    assert document_to_markdown(doc).startswith("```\nfn main()")


def test_body_takes_a_line_out_of_its_block():
    doc = from_markdown("```\na\nb\nc\n```\n")
    rich_text.set_heading(at(doc, "b"), rich_text.BODY)
    assert assert_round_trip(doc) == "```\na\n```\n\nb\n\n```\nc\n```\n"


def test_lists_and_quotes_leave_code_alone():
    doc = from_markdown("```\ncode\n```\n")
    rich_text.toggle_list(whole(doc), rich_text.BULLET)
    assert document_to_markdown(doc) == "```\ncode\n```\n"


def test_the_state_names_the_language():
    doc = from_markdown("```py\na\n```\n\ntext\n\n```js\nb\n```\n")
    assert rich_text.paragraph_state(at(doc, "a")).code == "py"
    assert rich_text.paragraph_state(at(doc, "text")).code is None
    mixed = at(doc, "a")
    mixed.setPosition(doc.toPlainText().index("b"), QTextCursor.MoveMode.KeepAnchor)
    assert rich_text.paragraph_state(mixed).code is None          # text in between
    both = from_markdown("```py\na\n```\n```js\nb\n```\n")
    cursor = at(both, "a")
    cursor.setPosition(both.toPlainText().index("b"), QTextCursor.MoveMode.KeepAnchor)
    assert rich_text.paragraph_state(cursor).code == "mixed"


def test_a_block_is_the_lines_markdown_writes_as_one():
    doc = from_markdown("```py\na\nb\n```\n```js\nc\n```\n")
    first, last = rich_text.code_run(block_named(doc, "b"))
    assert (first.text(), last.text()) == ("a", "b")
    assert rich_text.same_code_block(block_named(doc, "a"), block_named(doc, "b"))
    assert not rich_text.same_code_block(block_named(doc, "b"), block_named(doc, "c"))


# -- the keys --------------------------------------------------------------------------------

def editor(markdown: str, caret_after: str = "") -> HtmlEditor:
    ed = HtmlEditor()
    read_markdown(ed.document(), markdown)
    if caret_after:
        cursor = at(ed.document(), caret_after, len(caret_after))
        ed.setTextCursor(cursor)
    else:
        ed.moveCursor(QTextCursor.MoveOperation.End)
    return ed


def test_return_keeps_the_indent():
    ed = editor("```python\ndef f():\n    x = 1\n```\n\nafter\n", caret_after="x = 1")
    type_text(ed, "\ny = 2")
    assert document_to_markdown(ed.document()).startswith(
        "```python\ndef f():\n    x = 1\n    y = 2\n```\n")


def test_tab_and_shift_tab_indent_by_four_spaces():
    ed = editor("```\nab\n```\n\nafter\n")
    cursor = at(ed.document(), "ab", 1)
    ed.setTextCursor(cursor)
    press(ed, Qt.Key.Key_Tab)
    assert block_named(ed.document(), "a   b")                   # to column 4
    press(ed, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
    assert block_named(ed.document(), "a   b")                   # no spaces in front


def test_tab_on_several_lines_indents_each_as_one_step():
    ed = editor("```\none\ntwo\n```\n\nafter\n")
    cursor = select(ed.document(), "one")
    cursor.setPosition(ed.document().toPlainText().index("two") + 1,
                       QTextCursor.MoveMode.KeepAnchor)
    ed.setTextCursor(cursor)
    press(ed, Qt.Key.Key_Tab)
    assert document_to_markdown(ed.document()).startswith("```\n    one\n    two\n```")
    press(ed, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
    assert document_to_markdown(ed.document()).startswith("```\none\ntwo\n```")
    ed.document().undo()
    assert document_to_markdown(ed.document()).startswith("```\n    one\n    two\n```")


def test_return_always_adds_a_line_so_blank_lines_stay_code():
    # Review M3 (CP3): a blank line between two functions ended the block.
    ed = HtmlEditor()
    ed.make_code_block("python")
    type_text(ed, "def a():\n    return 1\n")
    press(ed, Qt.Key.Key_Backspace)          # the carried indent, to the last stop of four
    type_text(ed, "\ndef b():\n    return 2")
    assert assert_round_trip(ed.document()) == (
        "```python\ndef a():\n    return 1\n\ndef b():\n    return 2\n```\n")


def test_blank_lines_after_the_code_are_not_saved():
    ed = editor("```sh\nls\n```\n", caret_after="ls")
    type_text(ed, "\n\n")
    assert rich_text.is_code_block(ed.textCursor().block())        # still in the block
    assert assert_round_trip(ed.document()) == "```sh\nls\n```\n"


def test_return_takes_the_indent_of_the_caret_s_line():
    # Review L6 (CP3): a block line holding a line break took the indent
    # of its first line.
    ed = editor("```python\nx\n```\n", caret_after="x")
    cursor = ed.textCursor()
    cursor.insertText("\u2028        y")
    ed.setTextCursor(cursor)
    type_text(ed, "\nz")
    assert ed.textCursor().block().text() == "        z"


def test_command_return_leaves_the_block_from_any_line():
    ed = editor("```sh\nls\npwd\n```\n\nlast\n", caret_after="l")
    press(ed, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    type_text(ed, "between")
    assert assert_round_trip(ed.document()) == "```sh\nls\npwd\n```\n\nbetween\n\nlast\n"

def test_shift_return_adds_a_line_as_return_does():
    ed = editor("```\na\n```\n", caret_after="a")
    press(ed, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    press(ed, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    type_text(ed, "b")
    assert assert_round_trip(ed.document()) == "```\na\n\nb\n```\n"

def test_down_on_the_last_line_of_the_document_leaves_the_block():
    ed = editor("```sh\nls\n```\n")
    press(ed, Qt.Key.Key_Down)
    type_text(ed, "after")
    assert assert_round_trip(ed.document()) == "```sh\nls\n```\n\nafter\n"


def test_backspace_at_the_start_of_the_block_makes_it_text():
    ed = editor("before\n\n```\ncode\n```\n")
    ed.setTextCursor(at(ed.document(), "code"))
    press(ed, Qt.Key.Key_Backspace)
    assert assert_round_trip(ed.document()) == "before\n\ncode\n"


def test_the_editor_types_into_a_new_block_as_code():
    ed = HtmlEditor()
    ed.make_code_block("python")
    type_text(ed, "x = 1\ny = 2")
    assert assert_round_trip(ed.document()) == "```python\nx = 1\ny = 2\n```\n"


@pytest.mark.parametrize("typed", ["```python", "``` python", "~~~python"])
def test_a_typed_fence_starts_a_code_block(typed):
    ed = HtmlEditor()
    ed.set_markdown_shortcuts_check(lambda: True)
    type_text(ed, typed + "\nx = 1")
    assert assert_round_trip(ed.document()) == "```python\nx = 1\n```\n"


def test_a_typed_fence_is_text_without_markdown_shortcuts():
    ed = HtmlEditor()
    ed.set_markdown_shortcuts_check(lambda: False)
    type_text(ed, "```python\nx")
    assert not rich_text.is_code_block(ed.document().begin())


def test_a_paste_into_code_is_plain_text():
    ed = editor("```\ncode\n```\n", caret_after="code")
    mime = QMimeData()
    mime.setHtml("<b>bold</b> and <a href='https://example.com'>link</a>")
    mime.setText("bold and link")
    ed.insertFromMimeData(mime)
    assert assert_round_trip(ed.document()) == "```\ncodebold and link\n```\n"
    over = editor("```\nname\n```\n")
    over.setTextCursor(select(over.document(), "name"))
    address = QMimeData()
    address.setText("https://example.com")
    over.insertFromMimeData(address)
    assert assert_round_trip(over.document()) == "```\nhttps://example.com\n```\n"


def test_the_editor_paints_a_box_behind_a_code_block():
    ed = HtmlEditor()
    read_markdown(ed.document(), "text\n\n```\ncode\n```\n\nmore text\n")
    ed.resize(400, 300)
    ed.show()
    QApplication.processEvents()
    image = ed.viewport().grab().toImage()
    layout = ed.document().documentLayout()
    code = layout.blockBoundingRect(block_named(ed.document(), "code"))
    text = layout.blockBoundingRect(block_named(ed.document(), "text"))
    x = ed.viewport().width() - 12              # right of every word
    inside = image.pixelColor(x, int(code.center().y()))
    outside = image.pixelColor(x, int(text.center().y()))
    assert inside != outside
    ed.hide()


# -- in the window ---------------------------------------------------------------------------

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from markdown_writer import document_to_markdown
from tests.accessibility import unnamed_controls
from tests.rich_text_helpers import press, type_text

w = main_window.MainWindow()
w.show()
ed = w.new_tab()
ed.setFocus()
app.processEvents()
type_text(ed, "print(1)")
w.act_code_block.trigger()
app.processEvents()
r = {"asked": w.m_code_language.isVisible(),
     "titles": [a.text() for a in w.m_code_language.actions()][:3]}
w.m_code_language.hide()
python = next(a for a in w.act_code_languages if a.data() == "python")
python.trigger()
app.processEvents()
r["style"] = w.format_toolbar.style_button.text()
r["checked"] = [a.data() for a in w.act_code_languages if a.isChecked()]
r["in_code"] = {"bold": w.act_bold.isEnabled(), "link": w.act_link.isEnabled(),
                "language": python.isEnabled(), "quote": w.act_quote.isEnabled()}
r["markdown"] = document_to_markdown(ed.document())
w.act_styles[0].trigger()                      # Body: text again
app.processEvents()
r["body"] = document_to_markdown(ed.document())
r["style_after"] = w.format_toolbar.style_button.text()
r["after"] = {"bold": w.act_bold.isEnabled(), "language": python.isEnabled()}
type_text(ed, "\n/code b")
menu = ed.slash_menu()
r["slash"] = [menu.list.item(i).text() for i in range(menu.list.count())
              if menu.list.item(i).flags()]
press(ed, Qt.Key.Key_Escape)
r["names"] = unnamed_controls(w.format_toolbar)
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_insert_code_block_in_the_window(tmp_path):
    from tests.app_process import run_window_script
    r = run_window_script(SCRIPT, tmp_path)
    assert r["asked"]                                  # the language, at the caret
    assert r["titles"][0] == "Plain Text"
    assert r["style"] == "Code: Python"
    assert r["checked"] == ["python"]
    assert r["in_code"] == {"bold": False, "link": False, "language": True, "quote": True}
    assert r["markdown"] == "```python\nprint(1)\n```\n"
    assert r["body"] == "print(1)\n"
    assert r["style_after"] == "Body"
    assert r["after"] == {"bold": True, "language": False}
    assert r["slash"] and r["slash"][0] == "Code Block"
    assert r["names"] == []
