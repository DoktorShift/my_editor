# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the format bar over text selected with the pointer (selection_bar.py).

What must hold:

  When a selection made with the pointer ends (a drag, a double or a
  triple click), a small bar shows centred above its first line, inside
  the text's view, or below its last line when there is no room above.
  It holds the style pop-up, Bold, Italic, Strikethrough, Code and Link:
  the window's own commands, so their checks and dimming are the menus'.
  A selection made with the keyboard (Shift and the arrows, Select All)
  shows none, as in Word and on the iPad.

  It never takes the keyboard focus: the text keeps it, and a click on a
  button applies the command and keeps the selection and the bar, so Bold
  then Italic works.

  It goes on typing, on Escape (which then does nothing else, the
  selection staying), when the selection collapses or changes, on a
  scroll, a resize, the focus or the window going elsewhere, the text
  being hidden (another tab), or the words changing under it. It never
  shows while a pop-up (a menu, the link popover, a mention), the slash
  menu or an input method's composition is up, or where the window does
  not want it (a plain-text file, the setting off).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QFocusEvent, QInputMethodEvent, QMouseEvent, QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QLineEdit, QMainWindow, QMenu, QToolButton, QVBoxLayout, QWidget,
)

import commands  # noqa: E402
from commands import Command, CommandRegistry  # noqa: E402
from constants import DARK_MENU_BG, LIGHT_MENU_BG  # noqa: E402
from editor import HtmlEditor  # noqa: E402
from selection_bar import BUTTONS, SelectionBar  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402

LEFT = Qt.MouseButton.LeftButton
NONE = Qt.KeyboardModifier.NoModifier

TEXT = ("A first paragraph with some words in it.\n\n" * 3
        + "Another paragraph further down, to select from.\n\n" * 40)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def settle():
    """Let the event loop run until what was posted on the way is done."""
    QTest.qWait(20)


class Window:
    """A window with an editor, a field beside it, and the bar."""

    def __init__(self, wanted=True):
        self.window = QMainWindow()
        registry = CommandRegistry(self.window)
        self.triggered = []
        self.actions = {}
        for name in BUTTONS:
            action = registry.add(Command(f"format.{name}", name.capitalize(), commands.FORMAT,
                                          checkable=name != "link"))
            action.triggered.connect(lambda _c=False, n=name: self.triggered.append(n))
            self.actions[name] = action
        self.style_menu = QMenu()
        for title in ("Body", "Heading 1", "Heading 2", "Heading 3"):
            self.style_menu.addAction(title)
        self.wanted = wanted
        central = QWidget()
        layout = QVBoxLayout(central)
        self.editor = HtmlEditor()
        self.field = QLineEdit()
        layout.addWidget(self.editor)
        layout.addWidget(self.field)
        self.window.setCentralWidget(central)
        self.window.resize(600, 400)
        self.editor.setPlainText(TEXT)
        self.bar = SelectionBar(self.editor, self.actions, self.style_menu,
                                dark=lambda: False, wanted=lambda: self.wanted)
        self.window.show()
        self.window.activateWindow()
        self.editor.setFocus()
        QApplication.processEvents()

    def point_at(self, position):
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(position)
        rect = self.editor.cursorRect(cursor)
        return QPoint(rect.left() + 1, rect.center().y())

    def double_click(self, position):
        """What a double click sends: a click, then the second press as a
        double click, and its release."""
        viewport, point = self.editor.viewport(), self.point_at(position)
        QTest.mouseClick(viewport, LEFT, NONE, point)
        QTest.mouseDClick(viewport, LEFT, NONE, point)
        QTest.mouseRelease(viewport, LEFT, NONE, point)
        QApplication.processEvents()

    def drag(self, start, end):
        viewport = self.editor.viewport()
        QTest.mousePress(viewport, LEFT, NONE, self.point_at(start))
        QTest.mouseMove(viewport, self.point_at(end))
        QTest.mouseRelease(viewport, LEFT, NONE, self.point_at(end))
        QApplication.processEvents()

    def release_over(self, start, end):
        """``start`` to ``end`` selected, and the pointer let go over it:
        the end of a selection made with the pointer, without the click
        that would close a pop-up or end a composition first."""
        cursor = self.editor.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(cursor)
        point = QPointF(self.point_at(end))
        QApplication.sendEvent(self.editor.viewport(), QMouseEvent(
            QEvent.Type.MouseButtonRelease, point, self.editor.viewport().mapToGlobal(point),
            LEFT, Qt.MouseButton.NoButton, NONE))
        QApplication.processEvents()

    def selection(self):
        cursor = self.editor.textCursor()
        return cursor.anchor(), cursor.position()

    def close(self):
        self.window.close()


@pytest.fixture
def w():
    window = Window()
    yield window
    window.close()


def word_at(text, word, nth=0):
    at = -1
    for _ in range(nth + 1):
        at = text.index(word, at + 1)
    return at


def test_a_double_click_shows_the_bar_above_the_word(w):
    at = word_at(TEXT, "select", 5) + 2
    w.double_click(at)
    assert w.editor.textCursor().selectedText() == "select"
    assert w.bar.isVisible() and w.bar.parentWidget() is w.editor.viewport()
    start = QTextCursor(w.editor.document())
    start.setPosition(w.editor.textCursor().selectionStart())
    line = w.editor.cursorRect(start)
    end = QTextCursor(w.editor.document())
    end.setPosition(w.editor.textCursor().selectionEnd())
    word_centre = (line.left() + w.editor.cursorRect(end).left()) / 2
    assert w.bar.geometry().bottom() < line.top()
    assert abs(w.bar.geometry().center().x() - word_centre) <= 2
    assert w.editor.viewport().rect().contains(w.bar.geometry())
    assert w.editor.hasFocus()


def test_a_drag_and_a_triple_click_show_it_too(w):
    start = word_at(TEXT, "Another", 3)
    w.drag(start, start + 12)
    assert w.editor.textCursor().selectedText() == TEXT[start:start + 12]
    assert w.bar.isVisible()
    w.editor.setTextCursor(QTextCursor(w.editor.document()))
    assert not w.bar.isVisible()
    viewport = w.editor.viewport()
    point = w.point_at(start + 3)
    w.double_click(start + 3)
    QTest.mousePress(viewport, LEFT, NONE, point)           # the third click
    QTest.mouseRelease(viewport, LEFT, NONE, point)
    QApplication.processEvents()
    assert w.editor.textCursor().hasSelection() and w.bar.isVisible()


def test_with_no_room_above_it_goes_below_the_last_line(w):
    w.double_click(2)                              # "A" in the first line
    assert w.bar.isVisible()
    end = QTextCursor(w.editor.document())
    end.setPosition(w.editor.textCursor().selectionEnd())
    assert w.bar.geometry().top() > w.editor.cursorRect(end).bottom()
    assert w.editor.viewport().rect().contains(w.bar.geometry())


def test_a_selection_made_with_the_keyboard_shows_no_bar(w):
    QTest.keyClick(w.editor, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    QApplication.processEvents()
    assert w.editor.textCursor().hasSelection() and not w.bar.isVisible()
    w.editor.selectAll()
    QApplication.processEvents()
    assert not w.bar.isVisible()


def test_a_click_without_a_selection_or_a_right_click_shows_none(w):
    viewport = w.editor.viewport()
    QTest.mouseClick(viewport, LEFT, NONE, w.point_at(10))
    QApplication.processEvents()
    assert not w.bar.isVisible()
    cursor = w.editor.textCursor()
    cursor.setPosition(5)
    cursor.setPosition(9, QTextCursor.MoveMode.KeepAnchor)
    w.editor.setTextCursor(cursor)
    QTest.mouseRelease(viewport, Qt.MouseButton.RightButton, NONE, w.point_at(7))
    QApplication.processEvents()
    assert not w.bar.isVisible()


def test_it_holds_the_style_popup_and_the_window_commands(w):
    assert [w.bar.buttons[name].defaultAction() for name in BUTTONS] == [
        w.actions[name] for name in BUTTONS]
    assert w.bar.style_button.menu() is w.style_menu
    w.bar.set_style_name("Heading 2")
    assert w.bar.style_button.text() == "Heading 2"
    w.actions["italic"].setEnabled(False)
    assert not w.bar.buttons["italic"].isEnabled()
    assert unnamed_controls(w.bar) == []
    for widget in [w.bar, *w.bar.findChildren(QWidget)]:
        assert widget.focusPolicy() == Qt.FocusPolicy.NoFocus, widget


def test_a_click_on_a_button_keeps_the_text_the_selection_and_the_bar(w):
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    selection = w.selection()
    for name in ("bold", "italic"):
        button = w.bar.buttons[name]
        QTest.mouseClick(button, LEFT, NONE, button.rect().center())
        QApplication.processEvents()
        assert w.actions[name].isChecked() and button.isChecked()
        assert w.bar.isVisible() and w.editor.hasFocus() and w.selection() == selection
    assert w.triggered == ["bold", "italic"]


def test_a_click_on_the_bar_between_its_buttons_keeps_the_selection(w):
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    selection = w.selection()
    QTest.mouseClick(w.bar, LEFT, NONE, QPoint(1, w.bar.height() // 2))
    QApplication.processEvents()
    assert w.bar.isVisible() and w.selection() == selection


def test_typing_replaces_the_words_and_the_bar_goes(w):
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    QTest.keyClick(w.editor, Qt.Key.Key_Shift)               # a modifier alone: it stays
    assert w.bar.isVisible()
    QTest.keyClicks(w.editor, "x")
    assert not w.bar.isVisible() and not w.editor.textCursor().hasSelection()


def test_escape_closes_it_and_does_nothing_else(w):
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    selection, text = w.selection(), w.editor.toPlainText()
    QTest.keyClick(w.editor, Qt.Key.Key_Escape)
    assert not w.bar.isVisible()
    assert w.selection() == selection and w.editor.toPlainText() == text


def test_a_change_of_selection_by_keyboard_closes_it(w):
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    QTest.keyClick(w.editor, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    QApplication.processEvents()
    assert not w.bar.isVisible()
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    cursor = w.editor.textCursor()
    cursor.clearSelection()
    w.editor.setTextCursor(cursor)
    assert not w.bar.isVisible()


def test_a_scroll_or_a_resize_closes_it(w):
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    w.editor.verticalScrollBar().setValue(w.editor.verticalScrollBar().value() + 20)
    assert not w.bar.isVisible()
    w.editor.verticalScrollBar().setValue(0)
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    w.window.resize(640, 420)
    QApplication.processEvents()
    assert not w.bar.isVisible()


def test_the_focus_or_the_window_going_elsewhere_closes_it(w):
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    w.field.setFocus()
    settle()
    assert not w.bar.isVisible()
    w.editor.setFocus()
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    assert w.bar.isVisible()
    other = QMainWindow()
    other.show()
    other.activateWindow()
    settle()
    assert not w.bar.isVisible()
    other.close()


def test_another_tab_closes_it_for_good(w):
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    w.editor.hide()
    w.editor.show()
    QApplication.processEvents()
    assert not w.bar.isVisible()


def test_words_changing_under_it_close_it_and_a_format_follows(w):
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    cursor = QTextCursor(w.editor.document())
    cursor.insertText("New words. ")
    assert not w.bar.isVisible()
    w.double_click(word_at(w.editor.toPlainText(), "Another", 5) + 2)
    cursor = w.editor.textCursor()
    fmt = cursor.charFormat()
    fmt.setFontItalic(True)
    cursor.mergeCharFormat(fmt)
    QApplication.processEvents()
    assert w.bar.isVisible()


def test_it_never_shows_over_a_popup_or_a_composition(w):
    at = word_at(TEXT, "Another", 5)
    w.release_over(at, at + 7)
    assert w.bar.isVisible()                       # the way it shows, without either
    w.bar.hide()
    menu = QMenu(w.window)
    menu.addAction("Something")
    menu.popup(w.window.mapToGlobal(QPoint(10, 10)))
    QApplication.processEvents()
    w.release_over(at, at + 7)
    assert not w.bar.isVisible()
    menu.close()
    QApplication.processEvents()
    w.editor.setFocus()
    w.release_over(at + 8, at + 8)
    QApplication.sendEvent(w.editor, QInputMethodEvent("ka", []))
    w.release_over(at, at + 7)
    assert not w.bar.isVisible()


def test_it_never_shows_with_the_slash_menu_open():
    window = Window()
    window.editor.set_structure_check(lambda: True)
    window.editor.set_slash_items(lambda query: [])
    window.editor.slash_menu = lambda: object()          # open
    at = word_at(TEXT, "Another", 5)
    window.release_over(at, at + 7)
    assert not window.bar.isVisible()
    window.close()


def test_a_composition_or_a_popup_opening_closes_it(w):
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    QApplication.sendEvent(w.editor, QInputMethodEvent("ka", []))
    assert not w.bar.isVisible()
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    assert w.bar.isVisible()
    menu = QMenu(w.window)
    menu.addAction("Something")
    menu.popup(w.window.mapToGlobal(QPoint(10, 10)))
    settle()
    assert not w.bar.isVisible()
    menu.close()


def test_the_style_menu_keeps_it(w):
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    w.style_menu.popup(w.bar.style_button.mapToGlobal(QPoint(0, w.bar.style_button.height())))
    settle()
    assert w.bar.isVisible()
    w.style_menu.close()


def test_where_the_window_does_not_want_it_none_shows():
    window = Window(wanted=False)
    window.double_click(word_at(TEXT, "Another", 5) + 2)
    assert window.editor.textCursor().hasSelection() and not window.bar.isVisible()
    window.close()


@pytest.mark.parametrize("dark, background", [(True, DARK_MENU_BG), (False, LIGHT_MENU_BG)])
def test_both_themes(w, dark, background):
    w.bar.set_dark(dark)
    assert background in w.bar.styleSheet()
    for name in BUTTONS:
        assert not w.bar.buttons[name].icon().isNull()
    assert isinstance(w.bar.buttons["bold"], QToolButton)


WINDOW_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window, settings
w = main_window.MainWindow()
w.resize(900, 600)
w.show()
w.activateWindow()
home = os.environ["HOME"]
LEFT, NONE = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier

def double_click(ed, word):
    at = ed.toPlainText().index(word) + 1
    cursor = QTextCursor(ed.document())
    cursor.setPosition(at)
    rect = ed.cursorRect(cursor)
    point = rect.center()
    QTest.mouseClick(ed.viewport(), LEFT, NONE, point)
    QTest.mouseDClick(ed.viewport(), LEFT, NONE, point)
    QTest.mouseRelease(ed.viewport(), LEFT, NONE, point)
    QTest.qWait(20)
    return ed.selection_bar.isVisible()

def open_file(name, body):
    path = os.path.join(home, name)
    open(path, "w").write(body)
    w.open_path(path)
    app.processEvents()
    ed = w.current_editor()
    ed.setFocus()
    return ed

r = {}
md = open_file("doc.md", "Some words to select here\n")
r["rich"] = double_click(md, "words")
r["names"] = [md.selection_bar.style_button.text(),
              md.selection_bar.buttons["bold"].defaultAction() is w.act_bold]
w.act_bold.trigger()
app.processEvents()
r["bold_kept"] = [md.selection_bar.isVisible(), md.selection_bar.buttons["bold"].isChecked()]
txt = open_file("doc.txt", "Some words to select here\n")
r["plain"] = double_click(txt, "words")
w.tabs.setCurrentIndex(w._editor_widget_index(md))
app.processEvents()
r["tab_switch_hid"] = not md.selection_bar.isVisible()
md.setFocus()
r["default"] = w.format_bar_on_selection
w._show_settings()
app.processEvents()
w._settings_window.format_bar.click()
r["off"] = [w.format_bar_on_selection, settings.load_settings().get("format_bar_on_selection")]
w._settings_window.close()
md.setFocus()
r["off_shows"] = double_click(md, "select")
for i in range(w.tabs.count()):
    e = w._editor_from_widget(w.tabs.widget(i))
    if e is not None:
        e.document().setModified(False)
w.close()
w2 = main_window.MainWindow()
r["remembered"] = w2.format_bar_on_selection
w2.close()
print("RESULT " + json.dumps(r))
"""


def test_the_bar_in_the_window(tmp_path):
    from tests.app_process import run_window_script
    r = run_window_script(WINDOW_SCRIPT, tmp_path)
    assert r["rich"] and not r["plain"]
    assert r["names"] == ["Body", True]
    assert r["bold_kept"] == [True, True]
    assert r["tab_switch_hid"]
    assert r["default"] is True
    assert r["off"] == [False, False] and r["off_shows"] is False
    assert r["remembered"] is False


def test_the_style_popup_has_the_toolbar_disclosure_arrow(w):
    from format_toolbar import Disclosure, PopupButton
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    arrows = [c for c in w.bar.style_button.children() if isinstance(c, Disclosure)]
    assert len(arrows) == 1 and arrows[0].isVisible()
    assert isinstance(w.bar.style_button, PopupButton)          # no Qt dot


def test_a_popup_taking_the_keys_closes_it_while_the_window_stays_active(w, monkeypatch):
    """A menu or a popover opening sends the text FocusOut for a popup and
    nothing else on Linux, Windows and a Mac (the window stays active, and
    the text keeps its focus underneath): the bar goes then too, unless the
    popup is its own style menu."""
    import selection_bar
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    assert w.bar.isVisible()
    other = QMenu(w.window)
    monkeypatch.setattr(selection_bar.QApplication, "activePopupWidget",
                        staticmethod(lambda: other))
    QApplication.sendEvent(w.editor, QFocusEvent(QEvent.Type.FocusOut,
                                                 Qt.FocusReason.PopupFocusReason))
    settle()
    assert w.editor.hasFocus() and w.window.isActiveWindow()
    assert not w.bar.isVisible()
    monkeypatch.undo()                                 # no popup up
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    assert w.bar.isVisible()
    monkeypatch.setattr(selection_bar.QApplication, "activePopupWidget",
                        staticmethod(lambda: w.style_menu))
    QApplication.sendEvent(w.editor, QFocusEvent(QEvent.Type.FocusOut,
                                                 Qt.FocusReason.PopupFocusReason))
    settle()
    assert w.bar.isVisible()


def test_its_style_popup_follows_the_style_menu(w):
    w.style_menu.menuAction().setEnabled(False)
    assert not w.bar.style_button.isEnabled()
    w.style_menu.menuAction().setEnabled(True)
    assert w.bar.style_button.isEnabled()


TABLE_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
w = main_window.MainWindow()
w.show()
ed = w.new_tab()
ed.insert_table(2, 2)
app.processEvents()
w._update_format_buttons()
r = {"table": [w.format_toolbar.style_button.isEnabled(),
               ed.selection_bar.style_button.isEnabled(),
               w.m_style.menuAction().isEnabled()]}
path = os.path.join(os.environ["HOME"], "plain.txt")
open(path, "w").write("words\n")
w.open_path(path)
app.processEvents()
plain = w.current_editor()
r["plain"] = [w.format_toolbar.style_button.isEnabled(),
              plain.selection_bar.style_button.isEnabled()]
for i in range(w.tabs.count()):
    e = w._editor_from_widget(w.tabs.widget(i))
    if e is not None:
        e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_in_a_table_cell_the_bar_and_the_toolbar_agree(tmp_path):
    from tests.app_process import run_window_script
    r = run_window_script(TABLE_SCRIPT, tmp_path)
    assert r == {"table": [False, False, False], "plain": [False, False]}


def test_the_editor_going_first_leaves_the_bar_nothing_to_ask(capfd):
    """Review L1: taken down with the editor (at the interpreter's end the
    editor's half goes first), the bar's filter still heard its view and
    asked the editor, already gone, for it."""
    import shiboken6
    window = Window()
    window.double_click(word_at(TEXT, "Another", 5) + 2)
    assert window.bar.isVisible()
    shiboken6.delete(window.editor)
    QApplication.processEvents()
    settle()
    assert "RuntimeError" not in capfd.readouterr().err
    window.close()


def test_a_bar_lets_go_of_the_window_commands_when_it_goes():
    """Review L2: each bar tied the window's commands to a function that
    outlived it; a command changing after the bar had gone called it on
    buttons that were gone."""
    import shiboken6
    from PySide6.QtCore import SIGNAL
    window = Window()
    bold = window.actions["bold"]
    listening = bold.receivers(SIGNAL("changed()"))
    second = SelectionBar(window.editor, window.actions, window.style_menu,
                          dark=lambda: False, wanted=lambda: True)
    assert bold.receivers(SIGNAL("changed()")) > listening
    shiboken6.delete(second)
    assert bold.receivers(SIGNAL("changed()")) == listening
    bold.setText("Bold again")                 # nothing left to call
    window.close()


def test_over_right_to_left_words_it_is_centred_on_them():
    """Review L3: measured from the selection's logical start, the bar sat
    off to the side of a Hebrew word."""
    window = Window()
    hebrew = "שלום עולם " * 6
    window.editor.setPlainText(hebrew)
    at = hebrew.index("עולם", 30)
    window.double_click(at + 2)
    cursor = window.editor.textCursor()
    assert cursor.selectedText() == "עולם"
    edges = []
    for position in (cursor.selectionStart(), cursor.selectionEnd()):
        probe = QTextCursor(window.editor.document())
        probe.setPosition(position)
        edges.append(window.editor.cursorRect(probe).left())
    centre = (edges[0] + edges[1]) / 2
    assert window.bar.isVisible()
    assert abs(window.bar.geometry().center().x() - centre) <= 2
    window.close()


def test_where_none_of_its_buttons_can_act_it_does_not_show(w):
    """Review L4: over a code block every button was dimmed."""
    for action in w.actions.values():
        action.setEnabled(False)
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    assert w.editor.textCursor().hasSelection() and not w.bar.isVisible()
    w.actions["link"].setEnabled(True)
    w.double_click(word_at(TEXT, "Another", 5) + 2)
    assert w.bar.isVisible()


CODE_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
w = main_window.MainWindow()
w.resize(900, 600)
w.show()
w.activateWindow()
ed = w.new_tab()
ed.setFocus()
QTest.qWait(50)
QTest.keyClicks(ed, "print(value)")
ed.make_code_block("python")
app.processEvents()

def double_click(at):
    cursor = QTextCursor(ed.document())
    cursor.setPosition(at)
    point = ed.cursorRect(cursor).center()
    for send in (QTest.mouseClick, QTest.mouseDClick, QTest.mouseRelease):
        send(ed.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    QTest.qWait(30)
    return [ed.textCursor().hasSelection(), ed.selection_bar.isVisible()]

r = {"code": double_click(8)}
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_over_a_code_block_it_does_not_show(tmp_path):
    from tests.app_process import run_window_script
    r = run_window_script(CODE_SCRIPT, tmp_path)
    assert r == {"code": [True, False]}
