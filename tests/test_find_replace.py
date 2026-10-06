# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins Find and Replace (find_replace.py and the find bar).

What must hold:

  Find looks for the text with or without matching case, and as whole
  words only when asked.

  A replacement takes the style of the text it replaces: a bold word
  stays bold, a link stays a link to the same address.

  Replace All replaces every match as one step on the undo stack, and
  says how many it replaced.

  In the window, Find and Replace shows the Replace row, Replace replaces
  the selected match and selects the next one, and the matches are found
  again after the document changes.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QTextDocument  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from find_replace import FindOptions, find_all, replace_all, replace_match  # noqa: E402
from markdown_writer import READ_FEATURES, document_to_markdown  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def doc_of(markdown: str) -> QTextDocument:
    doc = QTextDocument()
    doc.setMarkdown(markdown, READ_FEATURES)
    return doc


def test_find_ignores_case_unless_asked():
    doc = doc_of("Cat cat CAT category\n")
    assert len(find_all(doc, "cat")) == 4
    assert find_all(doc, "cat", FindOptions(match_case=True)) == [(4, 7), (12, 15)]


def test_whole_words_skip_parts_of_words():
    doc = doc_of("cat category cat\n")
    assert find_all(doc, "cat", FindOptions(whole_words=True)) == [(0, 3), (13, 16)]


def test_nothing_to_find_finds_nothing():
    assert find_all(doc_of("text\n"), "") == []


def test_a_replacement_keeps_the_style_it_replaces():
    doc = doc_of("a **bold** word and [a link](https://x.example)\n")
    start = doc.toPlainText().index("bold")
    replace_match(doc, (start, start + 4), "strong")
    start = doc.toPlainText().index("a link")
    replace_match(doc, (start, start + 6), "the site")
    assert document_to_markdown(doc) == (
        "a **strong** word and [the site](https://x.example)\n")


def test_replace_all_is_one_undo_step_and_counts():
    doc = doc_of("one fish, two fish, red fish\n")
    assert replace_all(doc, "fish", "cat") == 3
    assert document_to_markdown(doc) == "one cat, two cat, red cat\n"
    doc.undo()
    assert document_to_markdown(doc) == "one fish, two fish, red fish\n"


def test_replace_all_with_text_that_holds_the_needle():
    doc = doc_of("a a a\n")
    assert replace_all(doc, "a", "aa") == 3
    assert doc.toPlainText() == "aa aa aa"


def test_replace_all_respects_the_options():
    doc = doc_of("Cat cat category\n")
    assert replace_all(doc, "cat", "dog", FindOptions(match_case=True, whole_words=True)) == 1
    assert doc.toPlainText() == "Cat dog category"


# -- in the window ---------------------------------------------------------------------

def test_the_find_bar_offers_replace_and_options():
    from widgets import FindBar
    bar = FindBar(lambda: None, lambda: None, lambda: None, replace=True, options=True)
    assert bar.replace_edit.isHidden()
    bar.show_replace(True)
    assert not bar.replace_edit.isHidden() and bar.replace_shown()
    bar.match_case.setChecked(True)
    assert bar.options() == FindOptions(match_case=True)
    assert bar.options_button.text() == "Match Case"
    asked = []
    bar.replace_requested.connect(lambda: asked.append("one"))
    bar.replace_all_requested.connect(lambda: asked.append("all"))
    bar.replace_btn.click()
    bar.replace_all_btn.click()
    assert asked == ["one", "all"]
    assert bar.edit.nextInFocusChain() is bar.replace_edit     # Tab: find, then replace


WINDOW_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from markdown_writer import document_to_markdown

w = main_window.MainWindow()
w.show()
ed = w.new_tab()
ed.insertPlainText("one fish, two fish, red fish")
r = {}
w._show_find(replace=True)
r["replace_row"] = w.findbar.replace_shown()
w.findbar.edit.setText("fish")
r["count"] = w.findbar.match_info.text()
w._find_next()
r["first"] = [ed.textCursor().selectionStart(), ed.textCursor().selectionEnd()]
w.findbar.replace_edit.setText("cat")
w._replace_current()
r["after_one"] = ed.toPlainText()
r["selected"] = ed.textCursor().selectedText()
r["position"] = w.findbar.match_info.text()
w._replace_all()
r["after_all"] = ed.toPlainText()
r["replaced"] = w.findbar.match_info.text()
ed.document().undo()
r["undone"] = ed.toPlainText()
# Typing changes the document; the count follows.
ed.moveCursor(ed.textCursor().MoveOperation.End)
ed.insertPlainText(" fish")
QTest.qWait(400)
r["refreshed"] = w.findbar.match_info.text()
w._show_find(replace=False)
r["replace_row_kept"] = w.findbar.replace_shown()
QTest.keyClick(w.findbar.edit, Qt.Key.Key_Escape)
r["closed"] = w.findbar.isHidden()
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_find_and_replace_in_the_window(tmp_path):
    from tests.app_process import run_window_script
    r = run_window_script(WINDOW_SCRIPT, tmp_path)
    assert r["replace_row"]
    assert r["count"] == "3 matches"
    assert r["first"] == [4, 8]
    assert r["after_one"] == "one cat, two fish, red fish"
    assert r["selected"] == "fish" and r["position"] == "1 of 2"
    assert r["after_all"] == "one cat, two cat, red cat"
    assert r["replaced"] == "2 replaced"
    assert r["undone"] == "one cat, two fish, red fish"
    assert r["refreshed"] in ("3 matches", "1 of 3")
    assert r["replace_row_kept"]          # Find while the row is open keeps it
    assert r["closed"]
