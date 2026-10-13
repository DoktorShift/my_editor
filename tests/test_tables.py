# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins tables (rich_text's table functions, the editor's keys in a
table, table_picker.TablePicker, Insert > Table and Format > Table).

What must hold:

  Insert > Table asks for the size on a grid at the caret (pointer or
  arrow keys, Return, Escape) and inserts a Markdown table: a header row
  in bold and the rows below it, a block of its own (in place of an
  empty paragraph, else after the paragraph), the caret in its first
  cell. One step to undo.

  Tab goes to the next cell and adds a row after the last one; Shift+Tab
  goes back; Return is a line break in the cell (written as a space,
  since a Markdown cell is one line); Command-Return (Ctrl+Return) leaves
  the table into a paragraph below. A pipe in a cell is escaped.

  Format > Table (and the context menu in a table) adds rows above and
  below, columns before and after, and deletes the row, the column or
  the table; the header row stays the header. Each is one step to undo,
  and only enabled in a table, where paragraph structure is off.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QFont, QKeyEvent, QMouseEvent, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import rich_text  # noqa: E402
from editor import HtmlEditor  # noqa: E402
from markdown_writer import document_to_markdown, read_markdown  # noqa: E402
from table_picker import TablePicker  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402
from tests.app_process import run_window_script  # noqa: E402
from tests.rich_text_helpers import (  # noqa: E402
    assert_round_trip, block_named, from_markdown, press, type_text,
)


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


def table_editor(rows=2, columns=2, markdown="", caret_after="") -> HtmlEditor:
    ed = editor(markdown, caret_after)
    ed.insert_table(rows, columns)
    return ed


def tab(ed, shift=False):
    if shift:
        press(ed, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
    else:
        press(ed, Qt.Key.Key_Tab)


def kinds(doc) -> list:
    """Each block of ``doc``: "T" in a table, "B" outside, and its text."""
    out = []
    block = doc.begin()
    while block.isValid():
        in_table = QTextCursor(block).currentTable() is not None
        out.append(("T" if in_table else "B") + block.text())
        block = block.next()
    return out


# -- inserting ---------------------------------------------------------------------------

def test_a_new_table_is_a_markdown_table_with_a_header():
    doc = from_markdown("")
    cursor = QTextCursor(doc)
    table = rich_text.insert_table(cursor, 2, 3)
    assert table is not None and cursor.currentTable() is table
    assert table.cellAt(cursor).row() == 0 and table.cellAt(cursor).column() == 0
    assert document_to_markdown(doc) == "|   |   |   |\n| --- | --- | --- |\n|   |   |   |\n"
    fmt = table.format()
    assert (fmt.border(), fmt.cellPadding(), fmt.cellSpacing(), fmt.borderCollapse()) == (
        1, 6, 0, True)


def test_typing_fills_the_cells_and_the_header_is_bold_but_written_plain():
    ed = table_editor()
    type_text(ed, "Name")
    assert ed.currentCharFormat().fontWeight() == QFont.Weight.Bold
    tab(ed)
    type_text(ed, "Amount")
    tab(ed)
    assert ed.currentCharFormat().fontWeight() != QFont.Weight.Bold
    type_text(ed, "Bread")
    tab(ed)
    type_text(ed, "2")
    assert assert_round_trip(ed.document()) == (
        "| Name | Amount |\n| --- | --- |\n| Bread | 2 |\n")


def test_tab_after_the_last_cell_adds_a_row_and_shift_tab_goes_back():
    ed = table_editor()
    type_text(ed, "a")
    for _i in range(3):
        tab(ed)
    type_text(ed, "d")
    tab(ed)                                            # past the last cell: a new row
    type_text(ed, "e")
    tab(ed, shift=True)
    tab(ed, shift=True)
    type_text(ed, "c")
    assert assert_round_trip(ed.document()) == (
        "| a |   |\n| --- | --- |\n| c | d |\n| e |   |\n")


def test_return_in_a_cell_goes_to_the_cell_below():
    # Review M4 (CP3): a line break in a cell was shown but saved as a
    # space. A Markdown cell holds one line, so Return moves on instead.
    ed = table_editor()
    type_text(ed, "one\ntwo\nthree")
    assert assert_round_trip(ed.document()) == (
        "| one |   |\n| --- | --- |\n| two |   |\n| three |   |\n")
    assert "\u2028" not in ed.toPlainText()


def test_code_with_a_pipe_in_a_cell_keeps_exactly_through_save_and_reopen():
    # Review M5 (CP3): the escape GFM asks for came back as a backslash,
    # one more at every save, and the file then opened as its text.
    from markdown_writer import holds_faithfully
    from nostr.preview import body_document
    ed = table_editor()
    type_text(ed, "`a|b`")
    first = assert_round_trip(ed.document())
    assert first.startswith("| `a\\|b` |")
    reopened = from_markdown(first)
    assert document_to_markdown(reopened) == first
    assert holds_faithfully(first)
    shown = body_document(first).toPlainText()
    assert "a|b" in shown and "a\\|b" not in shown


def test_command_return_leaves_the_table_into_a_paragraph_below():
    ed = table_editor(markdown="before", caret_after="before")
    type_text(ed, "x")
    press(ed, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert ed.textCursor().currentTable() is None
    type_text(ed, "after")
    assert assert_round_trip(ed.document()) == (
        "before\n\n| x |   |\n| --- | --- |\n|   |   |\n\nafter\n")


def test_a_pipe_in_a_cell_is_escaped():
    ed = table_editor()
    type_text(ed, "a|b")
    assert assert_round_trip(ed.document()).startswith("| a\\|b |")


def test_a_table_goes_after_a_paragraph_and_takes_the_place_of_an_empty_one():
    ed = table_editor(1, 1, markdown="# Title\n\nwords", caret_after="Ti")
    assert kinds(ed.document())[:3] == ["BTitle", "T", "B"]
    assert not block_named(ed.document(), "words").blockFormat().headingLevel()
    after = ed.document().findBlockByNumber(2)
    assert not after.blockFormat().headingLevel()       # the paragraph after it is Body
    empty = editor("x\n\ny")
    cursor = empty.textCursor()
    cursor.setPosition(1)
    cursor.insertBlock()
    empty.setTextCursor(cursor)
    empty.insert_table(1, 1)
    assert [k for k in kinds(empty.document()) if k != "B"] == ["Bx", "T", "By"]
    assert kinds(empty.document())[:2] == ["Bx", "T"]   # no empty paragraph before it


@pytest.mark.parametrize("markdown", ["```\ncode\n```\n", "text\n\n---\n"])
def test_no_table_inside_code_or_on_a_divider(markdown):
    doc = from_markdown(markdown)
    block = doc.begin()
    while not (rich_text.is_code_block(block) or rich_text.is_divider(block)):
        block = block.next()
    assert rich_text.insert_table(QTextCursor(block), 2, 2) is None


def test_inserting_a_table_is_one_step_to_undo():
    ed = editor("x")
    ed.insert_table(2, 2)
    ed.document().undo()
    assert document_to_markdown(ed.document()) == "x\n"


def test_a_table_opened_from_markdown_gets_the_same_grid():
    doc = from_markdown("| a | b |\n| --- | --- |\n| 1 | 2 |\n")
    table = rich_text.tables_of(doc)[0]
    assert table.format().cellPadding() == 6 and table.format().width().rawValue() == 100


# -- rows and columns ---------------------------------------------------------------------

def filled() -> HtmlEditor:
    return editor("| h1 | h2 |\n| --- | --- |\n| a | b |\n| c | d |\n", caret_after="a")


@pytest.mark.parametrize("command, markdown", [
    ("add_row_above", "| h1 | h2 |\n| --- | --- |\n|   |   |\n| a | b |\n| c | d |\n"),
    ("add_row_below", "| h1 | h2 |\n| --- | --- |\n| a | b |\n|   |   |\n| c | d |\n"),
    ("add_column_before", "|   | h1 | h2 |\n| --- | --- | --- |\n|   | a | b |\n|   | c | d |\n"),
    ("add_column_after", "| h1 |   | h2 |\n| --- | --- | --- |\n| a |   | b |\n| c |   | d |\n"),
    ("delete_row", "| h1 | h2 |\n| --- | --- |\n| c | d |\n"),
    ("delete_column", "| h2 |\n| --- |\n| b |\n| d |\n"),
    ("delete_table", ""),
])
def test_rows_and_columns_change_as_one_undo_step(command, markdown):
    ed = filled()
    before = document_to_markdown(ed.document())
    ed.table_command(command)
    assert assert_round_trip(ed.document()) == markdown
    ed.document().undo()
    assert document_to_markdown(ed.document()) == before


def test_moving_through_a_table_tells_where_the_caret_is(monkeypatch):
    # Review M7 (CP3): a screen reader heard nothing about the cell.
    import announce
    heard = []
    monkeypatch.setattr(announce, "announce", lambda source, text: heard.append(text))
    ed = editor("| Name | Price |\n| --- | --- |\n| Bread | 3 |\n", caret_after="")
    ed.setTextCursor(QTextCursor(ed.document()))
    cursor = ed.textCursor()
    cursor.setPosition(ed.toPlainText().index("Name"))
    ed.setTextCursor(cursor)
    assert heard[-1] == "Row 1 of 2, column 1"
    tab(ed)
    assert heard[-1] == "Row 1 of 2, column 2"
    tab(ed)
    assert heard[-1] == "Row 2 of 2, column 1, Name"
    count = len(heard)
    type_text(ed, "s")                                # in the same cell: nothing new
    assert len(heard) == count


def test_a_new_row_starts_at_its_first_cell():
    ed = editor("| h1 | h2 |\n| --- | --- |\n| a | b |\n", caret_after="b")
    ed.table_command("add_row_below")
    type_text(ed, "x")
    assert assert_round_trip(ed.document()) == (
        "| h1 | h2 |\n| --- | --- |\n| a | b |\n| x |   |\n")

def test_a_row_added_above_the_header_is_the_new_header():
    ed = editor("| h1 | h2 |\n| --- | --- |\n| a | b |\n", caret_after="h1")
    ed.table_command("add_row_above")
    type_text(ed, "top")
    assert assert_round_trip(ed.document()) == (
        "| top |   |\n| --- | --- |\n| h1 | h2 |\n| a | b |\n")


def test_deleting_the_header_makes_the_next_row_the_header():
    ed = editor("| h1 | h2 |\n| --- | --- |\n| a | b |\n", caret_after="h1")
    ed.table_command("delete_row")
    assert assert_round_trip(ed.document()) == "| a | b |\n| --- | --- |\n"
    table = rich_text.tables_of(ed.document())[0]
    first = QTextCursor(table.cellAt(0, 0).firstCursorPosition())
    first.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor)
    assert first.charFormat().fontWeight() == QFont.Weight.Bold


def test_deleting_the_last_row_or_column_deletes_the_table():
    ed = editor("x\n\n| h |\n| --- |\n", caret_after="h")
    ed.table_command("delete_row")
    assert rich_text.tables_of(ed.document()) == []
    assert document_to_markdown(ed.document()) == "x\n"


# -- the size picker ---------------------------------------------------------------------

def key(widget, which):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, which,
                                             Qt.KeyboardModifier.NoModifier))


def test_the_picker_chooses_with_the_keys(monkeypatch):
    import announce
    heard = []
    monkeypatch.setattr(announce, "announce", lambda source, text: heard.append(text))
    picker = TablePicker()
    got = []
    picker.picked.connect(lambda rows, columns: got.append((rows, columns)))
    assert picker.choice() == (3, 3)
    key(picker.grid, Qt.Key.Key_Right)
    key(picker.grid, Qt.Key.Key_Down)
    key(picker.grid, Qt.Key.Key_Down)
    assert picker.size_label.text() == "4 × 5"
    assert heard[-1] == "4 columns, 5 rows"
    for _i in range(10):
        key(picker.grid, Qt.Key.Key_Up)                 # never fewer than one row
    assert picker.choice() == (1, 4)
    key(picker.grid, Qt.Key.Key_Return)
    assert got == [(1, 4)]
    assert unnamed_controls(picker) == []


def test_escape_closes_the_picker_and_inserts_nothing():
    picker = TablePicker()
    picker.show()
    got = []
    picker.picked.connect(lambda rows, columns: got.append((rows, columns)))
    key(picker.grid, Qt.Key.Key_Escape)
    assert got == [] and not picker.isVisible()


def test_the_pointer_chooses_and_a_click_inserts():
    picker = TablePicker()
    picker.show()
    got = []
    picker.picked.connect(lambda rows, columns: got.append((rows, columns)))
    grid = picker.grid
    spot = QPointF(5 * 23 + 5, 1 * 23 + 5)            # column 6, row 2
    for kind, buttons in ((QEvent.Type.MouseMove, Qt.MouseButton.NoButton),
                          (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)):
        QApplication.sendEvent(grid, QMouseEvent(
            kind, spot, grid.mapToGlobal(spot), Qt.MouseButton.LeftButton
            if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton, buttons,
            Qt.KeyboardModifier.NoModifier))
    assert got == [(2, 6)]


# -- in the window -------------------------------------------------------------------------

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QMenu
app = QApplication(sys.argv[:1])
import main_window
from markdown_writer import document_to_markdown
from table_picker import TablePicker
from tests.rich_text_helpers import press, type_text

w = main_window.MainWindow()
w.show()
ed = w.new_tab()
ed.setFocus()
app.processEvents()
type_text(ed, "Prices")
r = {"commands_outside": [a.isEnabled() for a in w.act_table_commands],
     "insert_enabled": w.act_table.isEnabled()}
w.act_table.trigger()
app.processEvents()
pickers = [p for p in w.findChildren(TablePicker) if p.isVisible()]
r["picker"] = bool(pickers) and pickers[0].grid.hasFocus()
grid = pickers[0].grid
for key in (Qt.Key.Key_Left, Qt.Key.Key_Return):
    QApplication.sendEvent(grid, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
app.processEvents()
r["focus_back"] = ed.hasFocus()
type_text(ed, "Item")
r["inside"] = {"commands": [a.isEnabled() for a in w.act_table_commands],
               "heading": w.act_styles[1].isEnabled(), "bullets": w.act_bullets.isEnabled(),
               "insert": w.act_table.isEnabled(), "bold": w.act_bold.isEnabled(),
               "style": w.format_toolbar.style_button.isEnabled()}
menu = QMenu()
w._fill_editor_context_menu(menu, ed, ed.cursorRect().center())
r["context"] = [a.text() for a in menu.actions() if a.menu() is not None]
w.act_table_commands[1].trigger()                # Add Row Below
type_text(ed, "Bread")
r["markdown"] = document_to_markdown(ed.document())
type_text(ed, "\t")
press(ed, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
r["left"] = ed.textCursor().currentTable() is None
r["commands_after"] = [a.isEnabled() for a in w.act_table_commands]
type_text(ed, "/table")
menu = ed.slash_menu()
r["slash"] = [menu.list.item(i).text() for i in range(menu.list.count())
              if menu.list.item(i).flags()][:1]
press(ed, Qt.Key.Key_Escape)
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_insert_table_and_its_commands_in_the_window(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert r["commands_outside"] == [False] * 7 and r["insert_enabled"]
    assert r["picker"]                                  # at the caret, with the keys
    assert r["focus_back"]
    assert r["inside"] == {"commands": [True] * 7, "heading": False, "bullets": False,
                           "insert": False, "bold": True, "style": False}
    assert "Table" in r["context"]
    # Three rows (the header and two) of two columns, and a row added below the header.
    assert r["markdown"] == (
        "Prices\n\n| Item |   |\n| --- | --- |\n| Bread |   |\n|   |   |\n|   |   |\n")
    assert r["left"] and r["commands_after"] == [False] * 7
    assert r["slash"] == ["Table…"]
