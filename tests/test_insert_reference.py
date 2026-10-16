# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins Insert > Note or Article (rich_text.insert_own_paragraph, the
editor's insert_reference, nostr/ui/reference_popover.py, the window's
command).

What must hold:

  A note or an article goes into the text as its reference, written as it
  is (``nostr:nevent1…``, ``nostr:naddr1…``), in a paragraph of its own:
  readers show it as a card only when it stands alone. The paragraph at the
  caret when it is empty, else a new one after it; after a table or a
  block of code, never inside one. An empty paragraph follows, where the
  caret goes. One step to undo. Markdown has it with blank lines around it,
  and the publisher tags it (q).

  The panel asks the way Add Link does: one field, why it can't be a
  reference right under it (read with the field by screen readers), never
  in an alert; Insert only once it can.

  The command is Nostr's (D-1): in the Insert menu and the slash menu only
  while an account is in use.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import rich_text  # noqa: E402
from editor import HtmlEditor  # noqa: E402
from markdown_writer import document_to_markdown, read_markdown  # noqa: E402
from nostr import bech32  # noqa: E402
from nostr.publisher import build_article  # noqa: E402
from nostr.ui.reference_popover import ReferencePopover  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402
from tests.app_process import run_window_script  # noqa: E402

AUTHOR = "b2" * 32
NEVENT = bech32.encode_nevent("c3" * 32, ["wss://relay.example.com"], AUTHOR)
URI = "nostr:" + NEVENT


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def editor_with(markdown: str, at: int) -> HtmlEditor:
    ed = HtmlEditor()
    ed.set_nostr_check(lambda: True)
    read_markdown(ed.document(), markdown)
    cursor = ed.textCursor()
    cursor.setPosition(at)
    ed.setTextCursor(cursor)
    return ed


def blocks(ed) -> list:
    doc, out = ed.document(), []
    block = doc.begin()
    while block.isValid():
        out.append(block.text())
        block = block.next()
    return out


def test_in_a_paragraph_it_goes_after_it_and_the_caret_below():
    ed = editor_with("Read this.\n", 4)
    ed.insert_reference(URI)
    assert blocks(ed) == ["Read this.", URI, ""]
    assert ed.textCursor().block().text() == ""
    assert ed.textCursor().block().previous().text() == URI


def test_an_empty_paragraph_takes_it():
    ed = HtmlEditor()
    ed.insert_reference(URI)
    assert blocks(ed) == [URI, ""]


def test_one_undo_takes_it_back():
    ed = editor_with("Read this.\n", 4)
    ed.insert_reference(URI)
    ed.undo()
    assert blocks(ed) == ["Read this."]


def test_markdown_has_it_bare_with_blank_lines_around_and_the_article_quotes_it():
    ed = editor_with("Read this.\n", 4)
    ed.insert_reference(URI)
    cursor = ed.textCursor()
    cursor.insertText("And more.")
    markdown = document_to_markdown(ed.document())
    assert f"Read this.\n\n{URI}\n\nAnd more." in markdown
    event = build_article(content=markdown, pubkey_hex="a1" * 32, slug="s", title="T")
    assert ["q", "c3" * 32, "wss://relay.example.com", AUTHOR] in event["tags"]


def test_never_inside_a_table_or_a_block_of_code():
    ed = HtmlEditor()
    cursor = ed.textCursor()
    rich_text.insert_table(cursor, 2, 2)
    inside = ed.textCursor()
    inside.setPosition(ed.document().begin().position() + 1)
    ed.setTextCursor(inside)
    assert ed.textCursor().currentTable() is not None
    ed.insert_reference(URI)
    assert ed.textCursor().currentTable() is None
    assert URI in blocks(ed)
    table_cursor = QTextCursor(ed.document())
    table_cursor.setPosition(ed.document().begin().position() + 1)
    table = table_cursor.currentTable()
    assert all(URI not in table.cellAt(r, c).firstCursorPosition().block().text()
               for r in range(2) for c in range(2))

    code = editor_with("```python\nprint(1)\nprint(2)\n```\n", 1)
    assert rich_text.is_code_block(code.textCursor().block())
    code.insert_reference(URI)
    assert blocks(code)[:3] == ["print(1)", "print(2)", URI]
    assert not rich_text.is_code_block(code.document().findBlockByNumber(2))


def test_the_panel_says_why_under_the_field_and_inserts_only_a_reference():
    panel = ReferencePopover()
    assert not panel.insert_button.isEnabled()
    assert panel.problem.isHidden()                     # nothing wrong yet
    panel.field.setText("https://example.com/a-post")
    assert not panel.insert_button.isEnabled()
    assert not panel.problem.isHidden() and panel.problem.text()
    assert panel.field.accessibleDescription() == panel.problem.text()
    chosen = []
    panel.chosen.connect(chosen.append)
    panel.field.setText(f"https://njump.me/{NEVENT}")
    assert panel.insert_button.isEnabled() and panel.problem.isHidden()
    assert panel.field.accessibleDescription() == ""
    panel.insert_button.click()
    assert [reference.uri for reference in chosen] == [URI]
    assert unnamed_controls(panel) == []


SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window

w = main_window.MainWindow()
w.show()
ed = w.new_tab()
ed.setFocus()
app.processEvents()
def offered():
    w._fill_insert_menu()
    return "nostr.insert_reference" in [a.objectName() for a in w.m_insert.actions()]
r = {"menu_without": offered(),
     "slash_without": "Insert Note or Article…" in [c.title for c in w.commands.rank(
         "article", nostr=w.nostr_state.active)]}
w._ask_reference(ed)
r["panel_without"] = any(type(child).__name__ == "ReferencePopover"
                         for child in ed.findChildren(object))
w.nostr_state._active = True
w._update_nostr_commands()
r["menu_with"] = offered()
r["slash_with"] = "Insert Note or Article…" in [c.title for c in w.commands.rank(
    "article", nostr=True)]
w._ask_reference(ed)
app.processEvents()
panels = [c for c in ed.findChildren(object) if type(c).__name__ == "ReferencePopover"]
r["panel_with"] = len(panels) == 1 and panels[0].isVisible()
panels[0].field.setText(sys.argv[2])
panels[0].insert_button.click()
from PySide6.QtCore import QEvent
app.sendPostedEvents(None, QEvent.Type.DeferredDelete)      # the panel closes for good
app.processEvents()
r["text"] = ed.toPlainText()
r["focus_back"] = app.focusWidget() is ed
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_the_command_in_the_window(tmp_path):
    r = run_window_script(SCRIPT, tmp_path, URI)
    assert r["menu_without"] is False and r["slash_without"] is False       # D-1
    assert r["panel_without"] is False
    assert r["menu_with"] is True and r["slash_with"] is True
    assert r["panel_with"] is True
    assert r["text"] == URI + "\n"
    assert r["focus_back"] is True
