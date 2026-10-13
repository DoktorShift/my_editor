# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins how code looks (highlighter.CodeRules, RichTextLook).

What must hold:

  One set of rules per language serves a code file and a code block, so
  Python looks alike in both. A code block is colored by the language
  its fence names, by any of the usual names ("py", "sh", "c++"); text
  outside code blocks is never colored as code, and a construct that
  runs over lines (a docstring) goes on only within its block.

  Footnote marks are raised in the link color and their definitions
  quieter. Nothing of it is in the document: the Markdown is the same.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QTextCharFormat, QTextDocument  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from highlighter import (  # noqa: E402
    RichTextLook, SyntaxHighlighter, canonical_language, code_rules,
)
from markdown_writer import document_to_markdown  # noqa: E402
from tests.rich_text_helpers import block_named, from_markdown  # noqa: E402

KEYWORD = {False: "#0070c1", True: "#569cd6"}
STRING = {False: "#a31515", True: "#ce9178"}


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def looks(block):
    """(text, format) for every piece of ``block`` the highlighter styled."""
    text = block.text()
    ranges = block.layout().formats()
    # Copies: a range's format goes with the list Qt handed out.
    return [(text[r.start:r.start + r.length], QTextCharFormat(r.format)) for r in ranges]


def colored(block, color: str):
    return [piece for piece, fmt in looks(block)
            if fmt.foreground().color().name() == color]


@pytest.mark.parametrize("name, language", [
    ("py", "python"), ("Python", "python"), ("python3", "python"), ("JS", "javascript"),
    ("tsx", "typescript"), ("sh", "bash"), ("zsh", "bash"), ("yml", "yaml"), ("rs", "rust"),
    ("golang", "go"), ("c++", "cpp"), ("xml", "html"), ("jsonc", "json"), ("toml", "toml"),
])
def test_a_fence_names_a_language_by_its_usual_names(name, language):
    assert canonical_language(name) == language


@pytest.mark.parametrize("name", ["", None, "brainfuck", "text"])
def test_unknown_names_are_no_language(name):
    assert canonical_language(name) is None


def test_the_rules_are_made_once_per_language_and_theme():
    assert code_rules("python", True) is code_rules("python", True)
    assert code_rules("python", True) is not code_rules("python", False)


@pytest.mark.parametrize("dark", [False, True])
def test_a_python_block_is_colored_and_the_text_around_it_is_not(dark):
    doc = from_markdown("def outside\n\n```py\ndef inside():\n    return 'x'\n```\n")
    look = RichTextLook(doc, is_dark=dark)
    look.rehighlight()
    assert colored(block_named(doc, "def inside():"), KEYWORD[dark]) == ["def"]
    assert colored(block_named(doc, "    return 'x'"), STRING[dark]) == ["'x'"]
    assert colored(block_named(doc, "def outside"), KEYWORD[dark]) == []
    code_line = looks(block_named(doc, "def inside():"))[0][1]
    assert code_line.fontFixedPitch() or code_line.fontFamilies()


def test_a_code_file_and_a_code_block_look_alike():
    source = QTextDocument()
    source.setPlainText("def f():\n    pass\n")
    file_look = SyntaxHighlighter(source, "python", is_dark=False)
    file_look.rehighlight()
    block_doc = from_markdown("```python\ndef f():\n    pass\n```\n")
    block_look = RichTextLook(block_doc, is_dark=False)
    block_look.rehighlight()
    in_file = {piece: fmt.foreground().color().name()
               for piece, fmt in looks(source.begin()) if fmt.hasProperty(
                   QTextCharFormat.Property.ForegroundBrush)}
    in_block = {piece: fmt.foreground().color().name()
                for piece, fmt in looks(block_doc.begin()) if fmt.hasProperty(
                    QTextCharFormat.Property.ForegroundBrush)}
    assert in_file and in_file == in_block


def test_a_docstring_runs_on_within_its_block_only():
    doc = from_markdown('```python\n"""Start\nstill text\n```\n\nprose\n\n```python\nx = 1\n```\n')
    look = RichTextLook(doc, is_dark=False)
    look.rehighlight()
    assert colored(block_named(doc, "still text"), STRING[False]) == ["still text"]
    assert colored(block_named(doc, "x = 1"), STRING[False]) == []
    assert colored(block_named(doc, "prose"), STRING[False]) == []


def test_a_block_in_no_known_language_is_code_font_only():
    doc = from_markdown("```mermaid\ngraph TD; if then\n```\n")
    look = RichTextLook(doc, is_dark=False)
    look.rehighlight()
    pieces = looks(doc.begin())
    assert [piece for piece, _fmt in pieces] == ["graph TD; if then"]


def test_footnote_marks_are_raised_and_definitions_quieter():
    doc = from_markdown("A claim.[^1]\n\n[^1]: The source.\n")
    look = RichTextLook(doc, is_dark=False)
    look.rehighlight()
    mark = [fmt for piece, fmt in looks(block_named(doc, "A claim.[^1]")) if piece == "[^1]"]
    assert mark and mark[0].verticalAlignment() == QTextCharFormat.VerticalAlignment.AlignSuperScript
    definition = looks(block_named(doc, "[^1]: The source."))
    quiet = [piece for piece, fmt in definition if fmt.foreground().color() == QColor("#6B6B6B")]
    assert quiet == [": The source."]
    label = [fmt for piece, fmt in definition if piece == "[^1]"]
    assert label and label[0].foreground().color() == QColor("#0A66C2")
    assert label[0].verticalAlignment() != QTextCharFormat.VerticalAlignment.AlignSuperScript


def test_the_look_is_not_in_the_document():
    markdown = "Text with `code`.[^1]\n\n```python\ndef f():\n    pass\n```\n\n[^1]: Note.\n"
    doc = from_markdown(markdown)
    before = document_to_markdown(doc)
    look = RichTextLook(doc, is_dark=True)
    look.rehighlight()
    assert document_to_markdown(doc) == before

