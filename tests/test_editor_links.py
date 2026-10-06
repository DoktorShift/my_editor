# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins links in the editor: adding, editing, removing, opening.

What must hold:

  Add Link makes the selected words a link and keeps their bold or
  italic; with nothing selected, it inserts the address itself, which
  is written bare. Edit Link changes the address of the whole link,
  Remove Link leaves the words, plain. All of them are one step on the
  undo stack, and every case reads back the same from Markdown.

  Typing right after a link is not part of it.

  A web address pasted over words makes them a link to it.

  The popover says why an address cannot be a link (javascript:, file:)
  and only then refuses it; what it accepts is the address readers get
  (https:// added).

  Command-click (Ctrl-click) opens a link through the window.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

import rich_text  # noqa: E402
from editor import HtmlEditor  # noqa: E402
from link_popover import LinkPopover  # noqa: E402
from markdown_writer import READ_FEATURES, document_to_markdown  # noqa: E402
from tests.rich_text_helpers import assert_round_trip, press, select, type_text  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def editor(markdown: str) -> HtmlEditor:
    ed = HtmlEditor()
    ed.document().setMarkdown(markdown, READ_FEATURES)
    return ed


def selecting(ed, text):
    ed.setTextCursor(select(ed.document(), text))


def test_add_link_keeps_the_style_of_the_words():
    ed = editor("Read **the docs** today\n")
    selecting(ed, "the docs")
    ed.apply_link("the docs", "https://example.com/docs")
    assert assert_round_trip(ed.document()) == "Read [**the docs**](https://example.com/docs) today\n"


def test_add_link_with_nothing_selected_inserts_the_address():
    ed = editor("See\n")
    ed.moveCursor(QTextCursor.MoveOperation.End)
    type_text(ed, " ")
    ed.apply_link("", "https://example.com/a")
    type_text(ed, " now")
    assert assert_round_trip(ed.document()) == "See https://example.com/a now\n"


def test_edit_link_changes_the_whole_link():
    ed = editor("A [site](https://old.example) here\n")
    cursor = select(ed.document(), "si")
    cursor.setPosition(cursor.selectionEnd())       # the caret inside the link
    ed.setTextCursor(cursor)
    start, end, href = ed.link_at_caret()
    assert href == "https://old.example"
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    ed.setTextCursor(cursor)
    ed.apply_link("site", "https://new.example")
    assert document_to_markdown(ed.document()) == "A [site](https://new.example) here\n"


def test_remove_link_leaves_plain_words():
    ed = editor("A [site](https://x.example) here\n")
    selecting(ed, "site")
    ed.remove_link()
    assert document_to_markdown(ed.document()) == "A site here\n"
    assert not rich_text.link_range(select(ed.document(), "site"))
    from markdown_writer import has_local_only_formatting
    assert not has_local_only_formatting(ed.document())   # no link color left behind


def test_adding_a_link_is_one_undo_step():
    ed = editor("one two\n")
    selecting(ed, "two")
    ed.apply_link("two", "https://x.example")
    ed.document().undo()
    assert document_to_markdown(ed.document()) == "one two\n"


def test_typing_after_a_link_is_not_part_of_it():
    ed = editor("A [site](https://x.example)\n")
    ed.moveCursor(QTextCursor.MoveOperation.End)
    type_text(ed, " more")
    assert document_to_markdown(ed.document()) == "A [site](https://x.example) more\n"


def test_typing_before_a_link_at_a_paragraph_start_is_not_part_of_it():
    ed = editor("[site](https://x.example) here\n")
    ed.setTextCursor(QTextCursor(ed.document().begin()))
    type_text(ed, "My ")
    assert document_to_markdown(ed.document()) == "My [site](https://x.example) here\n"


def test_typing_inside_a_link_stays_in_it():
    ed = editor("A [site](https://x.example)\n")
    cursor = select(ed.document(), "si")
    cursor.setPosition(cursor.selectionEnd())
    ed.setTextCursor(cursor)
    type_text(ed, "X")
    assert document_to_markdown(ed.document()) == "A [siXte](https://x.example)\n"


def test_a_web_address_pasted_over_words_links_them():
    ed = editor("read this page\n")
    selecting(ed, "this page")
    QApplication.clipboard().setText("https://example.com/page")
    ed.paste_from_clipboard()
    assert document_to_markdown(ed.document()) == "read [this page](https://example.com/page)\n"


def test_plain_text_pasted_over_words_replaces_them():
    ed = editor("read this page\n")
    selecting(ed, "this page")
    QApplication.clipboard().setText("that book")
    ed.paste_from_clipboard()
    assert document_to_markdown(ed.document()) == "read that book\n"


# -- the popover ----------------------------------------------------------------------

def test_the_popover_refuses_what_cannot_be_a_link_and_says_why():
    popover = LinkPopover(text="x", href="javascript:alert(1)")
    assert not popover.add_button.isEnabled()
    assert popover.problem.text() and not popover.problem.isHidden()
    popover.address_edit.setText("example.com/page")
    assert popover.add_button.isEnabled() and popover.problem.isHidden()
    got = []
    popover.applied.connect(lambda text, href: got.append((text, href)))
    popover.add_button.click()
    assert got == [("x", "https://example.com/page")]


def test_an_empty_address_is_not_a_complaint_yet():
    popover = LinkPopover()
    assert not popover.add_button.isEnabled() and popover.problem.isHidden()


def test_a_screen_reader_can_name_every_field_and_button():
    from tests.accessibility import unnamed_controls
    assert unnamed_controls(LinkPopover(text="site", href="https://x.example",
                                        editing=True)) == []


def test_editing_offers_remove_link():
    popover = LinkPopover(text="site", href="https://x.example", editing=True)
    assert popover.remove_button is not None
    assert popover.add_button.text() == "Update Link"
    removed = []
    popover.removed.connect(lambda: removed.append(True))
    popover.remove_button.click()
    assert removed == [True]


# -- opening --------------------------------------------------------------------------

def test_command_click_opens_the_link():
    ed = editor("A [site](https://x.example) here\n")
    ed.resize(500, 200)
    ed.show()
    QApplication.processEvents()
    opened = []
    ed.set_link_opener(opened.append)
    cursor = select(ed.document(), "site")
    cursor.setPosition(cursor.selectionStart() + 1)
    point = QPointF(ed.cursorRect(cursor).center())
    ed.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, point, point,
                                   Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                   Qt.KeyboardModifier.ControlModifier))
    assert opened == ["https://x.example"]
    ed.hide()


def test_the_context_menu_on_a_link_offers_the_link_commands():
    from main_window import MainWindow
    from tests.test_commands import StandIn
    win = StandIn()
    MainWindow._build_actions(win)
    MainWindow._build_menu(win)
    ed = editor("A [site](https://x.example) here\n")
    ed.resize(500, 200)
    win.current_editor = lambda: ed
    win._editor_kind = lambda e: "rich"
    win._copy_link = MainWindow._copy_link
    cursor = select(ed.document(), "site")
    cursor.setPosition(cursor.selectionStart() + 1)
    ed.setTextCursor(cursor)
    MainWindow._update_style_checks(win, ed)
    assert win.act_link.text() == "Edit Link…"
    menu = QMenu()
    MainWindow._fill_editor_context_menu(win, menu, ed, ed.cursorRect(cursor).center())
    titles = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert titles[:4] == ["Open Link", "Edit Link\u2026", "Copy Link", "Remove Link"]
    copy = next(a for a in menu.actions() if a.text() == "Copy Link")
    copy.trigger()
    assert QApplication.clipboard().text() == "https://x.example"
    remove = next(a for a in menu.actions() if a.text() == "Remove Link")
    remove.trigger()
    assert document_to_markdown(ed.document()) == "A site here\n"


def test_the_context_menu_elsewhere_has_no_link_commands():
    from main_window import MainWindow
    from tests.test_commands import StandIn
    win = StandIn()
    MainWindow._build_actions(win)
    MainWindow._build_menu(win)
    ed = editor("Plain words\n")
    win._editor_kind = lambda e: "rich"
    menu = QMenu()
    MainWindow._fill_editor_context_menu(win, menu, ed, ed.cursorRect().center())
    assert "Open Link" not in [a.text() for a in menu.actions()]
