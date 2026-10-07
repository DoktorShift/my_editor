# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins Markdown Shortcuts (markdown_shortcuts.py, Edit > Substitutions).

What must hold:

  With the setting on, typed Markdown becomes what it means: "# " to
  "### " a heading, "- " "* " "+ " a bulleted list, "3. " a numbered list
  from 3, "> " a quote, "---" and Return a divider; "**bold**",
  "*italic*", "_italic_", "~~struck~~" and "`code`" when the closing mark
  is typed. What is typed next is not in the style just made.

  Undo right after a conversion brings back exactly what was typed.

  Nothing is converted with the setting off, in a code block, in a
  plain-text or code file, across a link, in snake_case or arithmetic,
  or after a backslash; block rules only in Body paragraphs.

  The setting is off until turned on, and remembered.
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QTextCharFormat, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import rich_text  # noqa: E402
from editor import HtmlEditor  # noqa: E402
from markdown_writer import document_to_markdown, read_markdown  # noqa: E402
from tests.rich_text_helpers import assert_round_trip, type_text  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def editor(markdown: str = "", *, on: bool = True) -> HtmlEditor:
    ed = HtmlEditor()
    if markdown:
        read_markdown(ed.document(), markdown)
        ed.moveCursor(QTextCursor.MoveOperation.End)
    ed.set_markdown_shortcuts_check(lambda: on)
    return ed


def typed(text: str, *, start: str = "", on: bool = True) -> HtmlEditor:
    ed = editor(start, on=on)
    type_text(ed, text)
    return ed


# -- paragraphs ------------------------------------------------------------------------

@pytest.mark.parametrize("text, markdown", [
    ("# Title", "# Title\n"),
    ("## Part", "## Part\n"),
    ("### Section", "### Section\n"),
    ("- one", "- one\n"),
    ("* one", "- one\n"),
    ("+ one", "- one\n"),
    ("1. first", "1. first\n"),
    ("3) third", "3. third\n"),
    ("> said so", "> said so\n"),
])
def test_the_start_of_a_paragraph_makes_its_structure(text, markdown):
    ed = typed(text)
    assert assert_round_trip(ed.document()) == markdown


def test_a_list_goes_on_from_item_to_item():
    ed = typed("- one\ntwo\n\nafter")
    assert assert_round_trip(ed.document()) == "- one\n- two\n\nafter\n"


def test_a_numbered_item_right_below_a_list_continues_it():
    ed = typed("1. one\n\n1. next")
    assert document_to_markdown(ed.document()) == "1. one\n2. next\n"


def test_three_dashes_and_return_make_a_divider():
    ed = typed("Above\n---\nBelow")
    assert assert_round_trip(ed.document()) == "Above\n\n---\n\nBelow\n"


def test_a_heading_text_typed_after_the_marks_is_heading_text():
    ed = typed("## Big")
    block = ed.document().begin()
    fmt = block.begin().fragment().charFormat()
    assert block.blockFormat().headingLevel() == 2
    assert fmt.property(fmt.Property.FontSizeAdjustment) == rich_text.HEADING_SIZE[2]


# -- words -----------------------------------------------------------------------------

@pytest.mark.parametrize("text, markdown", [
    ("a **bold** word", "a **bold** word\n"),
    ("an *italic* word", "an *italic* word\n"),
    ("an _italic_ word", "an *italic* word\n"),
    ("a ~~gone~~ word", "a ~~gone~~ word\n"),
    ("run `ls -l` now", "run `ls -l` now\n"),
])
def test_a_closing_mark_makes_its_style_and_what_follows_is_plain(text, markdown):
    ed = typed(text)
    assert assert_round_trip(ed.document()) == markdown


@pytest.mark.parametrize("text", [
    "snake_case_name here", "2*3*4 is 24", r"a \*literal\* star", "**not closed",
    "a * spaced * star", "ends with ** two",
])
def test_what_is_not_markup_stays_as_typed(text):
    ed = typed(text)
    assert ed.toPlainText() == text
    assert not any(rich_text.has_style(f, s) for f in _formats(ed)
                   for s in (rich_text.BOLD, rich_text.ITALIC, rich_text.CODE))


def _formats(ed):
    block = ed.document().begin()
    it = block.begin()
    while not it.atEnd():
        yield it.fragment().charFormat()
        it += 1


# -- undo ------------------------------------------------------------------------------

@pytest.mark.parametrize("text, literal", [
    ("# ", "# "), ("- ", "- "), ("**b**", "**b**"), ("---\n", "---"),
])
def test_undo_right_after_brings_back_what_was_typed(text, literal):
    ed = typed(text)
    ed.document().undo()
    assert ed.toPlainText() == literal
    block = ed.document().begin()
    assert not block.blockFormat().headingLevel() and block.textList() is None
    assert not rich_text.is_divider(block)


# -- where nothing happens ---------------------------------------------------------------

def test_nothing_is_converted_with_the_setting_off():
    ed = typed("# Title and **bold**", on=False)
    assert ed.toPlainText() == "# Title and **bold**"
    assert not ed.document().begin().blockFormat().headingLevel()


def test_nothing_is_converted_in_a_plain_text_file():
    ed = editor()
    ed.set_structure_check(lambda: False)
    type_text(ed, "- item and *word*")
    assert ed.toPlainText() == "- item and *word*"


def test_nothing_is_converted_in_a_code_block():
    ed = editor("```\nx\n```\n")
    type_text(ed, " # **y** `z`")
    assert document_to_markdown(ed.document()) == "```\nx # **y** `z`\n```\n"


def test_nothing_is_converted_across_a_link():
    ed = editor("see ")
    type_text(ed, "*")
    link = QTextCharFormat()
    link.setAnchor(True)
    link.setAnchorHref("https://example.com")
    ed.textCursor().insertText("site", link)
    ed.setCurrentCharFormat(rich_text.without_link(ed.currentCharFormat()))
    type_text(ed, "*")
    assert "*" in ed.toPlainText()


def test_block_marks_work_only_in_body_paragraphs():
    ed = typed("# Title")
    ed.moveCursor(QTextCursor.MoveOperation.StartOfBlock)
    type_text(ed, "- ")
    assert ed.toPlainText() == "- Title"
    assert ed.document().begin().textList() is None


# -- the setting ---------------------------------------------------------------------------

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window, settings
from tests.rich_text_helpers import type_text

w = main_window.MainWindow()
ed = w.new_tab()
r = {"default": w.act_markdown_shortcuts.isChecked(),
     "in_menu": w.act_markdown_shortcuts in w.m_substitutions.actions()}
type_text(ed, "# off")
r["off"] = ed.toPlainText()
w.act_markdown_shortcuts.trigger()
ed2 = w.new_tab()
type_text(ed2, "# on")
r["on_heading"] = ed2.document().begin().blockFormat().headingLevel()
r["remembered"] = settings.load_settings().get("markdown_shortcuts")
for e in (ed, ed2):
    e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_the_setting_is_off_until_turned_on_and_remembered(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", SCRIPT, REPO], env=env,
                          capture_output=True, text=True, timeout=120)
    line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT ")), None)
    assert line, f"child failed:\n{proc.stdout}\n{proc.stderr}"
    r = json.loads(line[len("RESULT "):])
    assert r["default"] is False and r["in_menu"]
    assert r["off"] == "# off"
    assert r["on_heading"] == 1 and r["remembered"] is True
