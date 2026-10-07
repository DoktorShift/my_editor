# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins footnotes (rich_text's footnote functions, the editor's Insert >
Footnote and Command-click, the preview's check).

What must hold:

  Insert > Footnote puts the next number's mark ("[^2]": one more than
  the highest used, so a deleted one is not reused) at the caret, and its
  note ("[^2]: ") at the end of the document, after an empty paragraph
  unless the last paragraph is a note already; the caret goes there to
  write it. One step to undo. Not in code.

  Footnotes are the text Markdown writes for them, so a document and
  its Markdown keep them exactly. Command-click (Ctrl-click) on a mark
  goes to its note, on a note's label back to the first mark; the
  context menu offers the same for the keyboard. Marks in code are none.

  While Nostr is in use, the preview says that some apps show footnotes
  as typed.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import rich_text  # noqa: E402
from editor import HtmlEditor  # noqa: E402
from markdown_writer import document_to_markdown, read_markdown  # noqa: E402
from tests.app_process import run_window_script  # noqa: E402
from tests.rich_text_helpers import assert_round_trip, block_named, type_text  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def editor(markdown: str = "", caret_after: str = "") -> HtmlEditor:
    ed = HtmlEditor()
    if markdown:
        read_markdown(ed.document(), markdown)
    cursor = ed.textCursor()
    if caret_after:
        cursor.setPosition(ed.toPlainText().index(caret_after) + len(caret_after))
    else:
        cursor.movePosition(QTextCursor.MoveOperation.End)
    ed.setTextCursor(cursor)
    return ed


def test_a_footnote_is_a_mark_here_and_its_note_at_the_end():
    ed = editor("A claim, and more words.\n", caret_after="A claim")
    ed.insert_footnote()
    type_text(ed, "The source.")
    assert assert_round_trip(ed.document()) == (
        "A claim[^1], and more words.\n\n[^1]: The source.\n")


def test_footnotes_are_numbered_on_and_a_deleted_number_is_not_reused():
    ed = editor("One, two.\n", caret_after="One")
    ed.insert_footnote()
    type_text(ed, "First.")
    cursor = ed.textCursor()
    cursor.setPosition(ed.toPlainText().index("two") + 3)
    ed.setTextCursor(cursor)
    ed.insert_footnote()
    type_text(ed, "Second.")
    assert assert_round_trip(ed.document()) == (
        "One[^1], two[^2].\n\n[^1]: First.\n\n[^2]: Second.\n")
    # The first footnote goes, mark and note: the next one is 3, not 1.
    doc = ed.document()
    for text in ("[^1]: First.", "[^1]"):
        cursor = QTextCursor(doc)
        start = doc.toPlainText().index(text)
        cursor.setPosition(start)
        cursor.setPosition(start + len(text), QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
    assert rich_text.next_footnote_label(doc) == "3"


def test_inserting_a_footnote_is_one_step_to_undo():
    ed = editor("Words.\n", caret_after="Words")
    ed.insert_footnote()
    ed.document().undo()
    assert document_to_markdown(ed.document()) == "Words.\n"


def test_no_footnote_inside_a_code_block():
    ed = editor("```\ncode\n```\n", caret_after="co")
    ed.insert_footnote()
    assert document_to_markdown(ed.document()) == "```\ncode\n```\n"


MARKED = "A claim[^1] and[^1] again.\n\n`[^1]` in code.\n\n[^1]: The source.\n"


def test_a_mark_leads_to_its_note_and_the_label_back_to_the_first_mark():
    ed = editor(MARKED)
    doc = ed.document()
    mark = QTextCursor(doc)
    mark.setPosition(doc.toPlainText().index("[^1]") + 2)
    note = block_named(doc, "[^1]: The source.")
    assert rich_text.footnote_jump(mark) == note.position() + len("[^1]: ")
    label = QTextCursor(note)
    label.setPosition(note.position() + 1)
    assert rich_text.footnote_jump(label) == doc.toPlainText().index("[^1]")
    words = QTextCursor(note)
    words.setPosition(note.position() + 10)
    assert rich_text.footnote_jump(words) is None            # in the note's words
    assert len(rich_text.footnote_marks(doc, "1")) == 2       # not the one in code
    assert rich_text.footnote_text(doc, "1") == "The source."


def test_command_click_on_a_mark_goes_to_its_note():
    ed = editor(MARKED)
    ed.resize(500, 300)
    ed.show()
    QApplication.processEvents()
    mark = QTextCursor(ed.document())
    mark.setPosition(ed.toPlainText().index("[^1]") + 2)
    spot = QPointF(ed.cursorRect(mark).center())
    event = QMouseEvent(QEvent.Type.MouseButtonPress, spot, ed.viewport().mapToGlobal(spot),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.ControlModifier)
    ed.mousePressEvent(event)
    assert ed.textCursor().block().text() == "[^1]: The source."
    ed.hide()


SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QMenu
app = QApplication(sys.argv[:1])
import main_window
from preview_pane import ARTICLE
from tests.rich_text_helpers import press, type_text

w = main_window.MainWindow()
w.show()
ed = w.new_tab()
ed.setFocus()
app.processEvents()
type_text(ed, "A claim")
r = {"in_insert": [a.objectName() for a in w.m_insert.actions()].count("insert.footnote")}
w.act_footnote.trigger()
type_text(ed, "The source.")
cursor = ed.textCursor()
cursor.setPosition(ed.toPlainText().index("[^1]") + 1)
ed.setTextCursor(cursor)
menu = QMenu()
w._fill_editor_context_menu(menu, ed, ed.cursorRect().center())
go = [a for a in menu.actions() if a.text() == "Go to Note"]
r["go"] = bool(go)
if go:
    go[0].trigger()
r["at_note"] = ed.textCursor().block().text()
ed.moveCursor(QTextCursor.MoveOperation.End)
type_text(ed, "\n/foot")
slash = ed.slash_menu()
r["slash"] = [slash.list.item(i).text() for i in range(slash.list.count())
              if slash.list.item(i).flags()][:1]
press(ed, Qt.Key.Key_Escape)
w.nostr_state._active = True
r["check"] = list(w._preview_content(ed, ARTICLE).checks)
w.nostr_state._active = False
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_footnotes_in_the_window(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert r["in_insert"] == 1
    assert r["go"] and r["at_note"] == "[^1]: The source."
    assert r["slash"] == ["Footnote"]
    assert any("show footnotes as typed" in check for check in r["check"])
