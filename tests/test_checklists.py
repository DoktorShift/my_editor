# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins checklists (rich_text.toggle_checklist and the editor's keys).

What must hold:

  Format > Checklist makes the paragraphs under the cursor checklist
  items: a paragraph outside a list joins a bulleted list first (a
  heading becomes Body), an item keeps its tick; when all of them are
  checklist items, the boxes go and they are plain items. One step to
  undo. Markdown: "- [ ] open" and "- [x] done".

  Return in a checklist item starts an unticked one; Return on an empty
  one leaves the list. A click on the box ticks and unticks it.

  With Markdown Shortcuts, "[ ] " and "[x] " at the start of a paragraph
  or of a list item make a checklist item.

  The command is Shift-Command-L on a Mac and Ctrl+Shift+9 elsewhere, in
  the Format menu, on the toolbar and in the slash menu.
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
from tests.rich_text_helpers import (  # noqa: E402
    assert_round_trip, block_named, from_markdown, select, type_text,
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def whole(doc) -> QTextCursor:
    cursor = QTextCursor(doc)
    cursor.select(QTextCursor.SelectionType.Document)
    return cursor


def test_paragraphs_become_a_checklist_and_back():
    doc = from_markdown("# Shopping\n\nbread\n\nmilk\n")
    rich_text.toggle_checklist(whole(doc))
    assert assert_round_trip(doc) == "- [ ] Shopping\n- [ ] bread\n- [ ] milk\n"
    rich_text.toggle_checklist(whole(doc))
    assert assert_round_trip(doc) == "- Shopping\n- bread\n- milk\n"


def test_a_mixed_selection_keeps_ticks_and_list_kinds():
    doc = from_markdown("- [x] done\n- open\n\n1. first\n\nloose\n")
    rich_text.toggle_checklist(whole(doc))
    markdown = assert_round_trip(doc)
    assert markdown.startswith("- [x] done\n- [ ] open\n")
    assert "1. [ ] first" in markdown and "[ ] loose" in markdown


def test_a_checklist_is_one_step_to_undo():
    doc = from_markdown("one\n\ntwo\n")
    rich_text.toggle_checklist(whole(doc))
    doc.undo()
    assert document_to_markdown(doc) == "one\n\ntwo\n"


def test_code_and_dividers_are_left_alone():
    doc = from_markdown("text\n\n---\n\n```\ncode\n```\n")
    rich_text.toggle_checklist(whole(doc))
    assert assert_round_trip(doc) == "- [ ] text\n\n---\n\n```\ncode\n```\n"


def test_bulleted_list_on_a_checklist_makes_it_bullets():
    doc = from_markdown("- [ ] a\n- [x] b\n")
    rich_text.toggle_list(whole(doc), rich_text.BULLET)
    assert assert_round_trip(doc) == "- a\n- b\n"
    doc.undo()
    assert document_to_markdown(doc) == "- [ ] a\n- [x] b\n"

def test_the_state_says_when_all_are_checklist_items():
    doc = from_markdown("- [ ] a\n- [x] b\n- c\n")
    cursor = select(doc, "a")
    assert rich_text.paragraph_state(cursor).tasks
    cursor.setPosition(doc.toPlainText().index("c") + 1, QTextCursor.MoveMode.KeepAnchor)
    assert not rich_text.paragraph_state(cursor).tasks


def editor(markdown: str) -> HtmlEditor:
    ed = HtmlEditor()
    read_markdown(ed.document(), markdown)
    ed.moveCursor(QTextCursor.MoveOperation.End)
    return ed


def test_return_in_a_ticked_item_starts_an_unticked_one():
    ed = editor("- [x] done\n")
    type_text(ed, "\nnext\n\nafter")
    assert assert_round_trip(ed.document()) == "- [x] done\n- [ ] next\n\nafter\n"


def test_markdown_shortcuts_make_checklist_items():
    ed = HtmlEditor()
    ed.set_markdown_shortcuts_check(lambda: True)
    type_text(ed, "[ ] milk\n[x] bread")
    assert document_to_markdown(ed.document()) == "- [ ] milk\n- [x] bread\n"
    other = HtmlEditor()
    other.set_markdown_shortcuts_check(lambda: True)
    type_text(other, "- [x] typed")
    assert document_to_markdown(other.document()) == "- [x] typed\n"
    for _letter in "typed":                       # one step per letter typed
        other.document().undo()
    other.document().undo()                       # the box, as typed
    assert other.toPlainText() == "[x] "
    assert not rich_text.is_task(other.document().begin())


def test_a_click_on_the_box_ticks_it():
    ed = editor("- [ ] open\n")
    ed.resize(500, 200)
    ed.show()
    QApplication.processEvents()
    block = block_named(ed.document(), "open")
    layout = ed.document().documentLayout()
    rect = layout.blockBoundingRect(block)
    y = rect.top() + rect.height() / 2
    spot = None
    for x in range(0, int(rect.left()) + 120):
        if layout.blockWithMarkerAt(QPointF(x, y)).isValid():
            spot = QPointF(x - ed.horizontalScrollBar().value(), y - ed.verticalScrollBar().value())
            break
    assert spot is not None, "the box is drawn"
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        event = QMouseEvent(kind, spot, ed.viewport().mapToGlobal(spot),
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton
                            if kind == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(ed.viewport(), event)
    assert document_to_markdown(ed.document()) == "- [x] open\n"
    ed.hide()


@pytest.mark.parametrize("platform, keys", [("darwin", "Ctrl+Shift+L"),
                                            ("win32", "Ctrl+Shift+9"),
                                            ("linux", "Ctrl+Shift+9")])
def test_the_checklist_key_follows_the_platform(platform, keys):
    from commands import platform_keys
    assert platform_keys("Ctrl+Shift+L", "Ctrl+Shift+9", platform=platform) == keys
