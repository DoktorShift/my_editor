# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the format toolbar (format_toolbar.py).

What must hold:

  The toolbar's buttons are the window's own commands: the same actions,
  so state, keys and availability are the menu's. Each says its key in
  the platform's notation. Underline has no button.

  Undo and Redo come first, before the style pop-up, each with the list
  of its steps beside it; their tips name the step ("Undo Typing (⌘Z)").
  After the lists and the quote, one Insert button opens the window's own
  Insert menu.

  The buttons show the style at the caret, and the pop-up names the
  paragraph style.

  View > Show Toolbar hides and shows it, and is remembered; a PDF tab
  has no toolbar.

  When the window is narrow, the buttons that do not fit move to the
  toolbar's extension menu instead of being cut off.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QToolButton  # noqa: E402

import commands  # noqa: E402
from commands import Command, CommandRegistry  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402
from format_toolbar import HISTORY, LAYOUT, FormatToolbar, tooltip_for  # noqa: E402
from tests.app_process import run_window_script  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def toolbar():
    window = QMainWindow()
    registry = CommandRegistry(window)
    keys = {"bold": "Ctrl+B", "italic": "Ctrl+I", "link": "Ctrl+K"}
    actions = {name: registry.add(Command(f"format.{name}", name.capitalize(),
                                          commands.FORMAT, keys.get(name), checkable=True))
               for name in (*HISTORY, *LAYOUT) if name}
    style_menu = QMenu()
    for title in ("Body", "Heading 1", "Heading 2", "Heading 3"):
        style_menu.addAction(title)
    insert_menu = QMenu()
    insert_menu.addAction("Table…")
    bar = FormatToolbar(actions, style_menu, insert_menu, dark=False, parent=window)
    bar.window_ = window                     # keep the window alive with the bar
    bar.actions_ = actions
    bar.insert_menu_ = insert_menu
    return bar


def test_the_buttons_are_the_window_commands(toolbar):
    assert [a for a in toolbar.actions() if a in toolbar.actions_.values()] == [
        toolbar.actions_[name] for name in (*HISTORY, *LAYOUT) if name]
    assert "underline" not in toolbar.buttons
    toolbar.actions_["bold"].setChecked(True)
    assert toolbar.buttons["bold"].isChecked()
    toolbar.actions_["quote"].setEnabled(False)
    assert not toolbar.buttons["quote"].isEnabled()


def test_a_screen_reader_can_name_every_button(toolbar):
    # The buttons show icons only: each is announced by its command's name.
    assert unnamed_controls(toolbar) == []


def test_each_button_says_its_key_the_platform_way(toolbar):
    native = QKeySequence("Ctrl+B").toString(QKeySequence.SequenceFormat.NativeText)
    assert toolbar.buttons["bold"].toolTip() == f"Bold ({native})"
    assert toolbar.buttons["quote"].toolTip() == "Quote"          # no key, no brackets
    toolbar.actions_["link"].setText("Edit Link…")
    assert toolbar.buttons["link"].toolTip().startswith("Edit Link (")


def test_the_style_popup_names_the_style(toolbar):
    toolbar.set_style_name("Heading 2")
    assert toolbar.style_button.text() == "Heading 2"
    assert toolbar.style_button.menu() is not None


def test_undo_and_redo_come_first_with_their_lists(toolbar):
    from PySide6.QtWidgets import QWidgetAction
    leading = toolbar.actions()[:4]
    assert leading[:2] == [toolbar.actions_["undo"], toolbar.actions_["redo"]]
    assert leading[2].isSeparator()
    assert isinstance(leading[3], QWidgetAction) and \
        leading[3].defaultWidget() is toolbar.style_button
    for name, words, menu in (("undo", "Undo", toolbar.undo_menu),
                              ("redo", "Redo", toolbar.redo_menu)):
        button = toolbar.buttons[name]
        assert button.defaultAction() is toolbar.actions_[name]
        assert button.menu() is menu
        assert button.popupMode() == QToolButton.ToolButtonPopupMode.MenuButtonPopup
        assert button.accessibleName() == words


def test_undo_and_redo_tips_name_the_step(toolbar):
    toolbar.actions_["undo"].setShortcut(QKeySequence("Ctrl+Z"))
    native = QKeySequence("Ctrl+Z").toString(QKeySequence.SequenceFormat.NativeText)
    toolbar.describe_history("Undo Typing", "Redo")
    assert toolbar.buttons["undo"].toolTip() == f"Undo Typing ({native})"
    assert toolbar.buttons["redo"].toolTip() == "Redo"
    toolbar.actions_["undo"].setEnabled(False)          # Qt's own tip comes back
    assert toolbar.buttons["undo"].toolTip() == f"Undo Typing ({native})"


def test_insert_opens_the_window_insert_menu(toolbar):
    from PySide6.QtWidgets import QWidgetAction
    trailing = toolbar.actions()[-2:]
    assert toolbar.actions()[-3] is toolbar.actions_["quote"]
    assert trailing[0].isSeparator()
    assert isinstance(trailing[1], QWidgetAction)
    button = trailing[1].defaultWidget()
    assert button is toolbar.insert_button
    assert button.menu() is toolbar.insert_menu_
    assert button.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
    assert button.accessibleName() == "Insert" and button.toolTip() == "Insert"
    for dark in (True, False):
        toolbar.set_dark(dark)
        assert not button.icon().isNull()


def test_both_themes_draw_every_icon(toolbar):
    for dark in (True, False):
        toolbar.set_dark(dark)
        for name in (*HISTORY, *LAYOUT):
            if name:
                assert not toolbar.actions_[name].icon().isNull()
                assert not toolbar.actions_[name].isIconVisibleInMenu()


def test_a_narrow_toolbar_moves_buttons_into_its_extension(toolbar):
    toolbar.window_.setCentralWidget(toolbar)
    toolbar.window_.resize(140, 60)
    toolbar.window_.show()
    QApplication.processEvents()
    extension = toolbar.findChild(QToolButton, "qt_toolbar_ext_button")
    assert extension is not None and extension.isVisible()
    toolbar.window_.hide()


def test_tooltip_without_a_key_is_the_title():
    from PySide6.QtGui import QAction
    action = QAction("Add Link…")
    assert tooltip_for(action) == "Add Link"


WINDOW_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window, settings
from export_pdf import export_pdf
from markdown_writer import READ_FEATURES

w = main_window.MainWindow()
w.show()
ed = w.new_tab()
ed.document().setMarkdown("# Title\n\nSome **bold** text\n", READ_FEATURES)
app.processEvents()
r = {}
tb = w.format_toolbar
r["shown"] = tb.isVisible()
r["insert_menu"] = tb.insert_button.menu() is w.m_insert
cursor = ed.document().find("bold")
ed.setTextCursor(cursor)
app.processEvents()
r["bold_checked"] = w.act_bold.isChecked() and tb.buttons["bold"].isChecked()
r["style_body"] = tb.style_button.text()
ed.setTextCursor(QTextCursor(ed.document().begin()))
app.processEvents()
r["style_heading"] = tb.style_button.text()
w.act_show_toolbar.trigger()
r["hidden"] = tb.isHidden()
r["remembered"] = settings.load_settings().get("show_toolbar")
w.act_show_toolbar.trigger()
r["back"] = tb.isVisible()
doc = QTextDocument()
doc.setPlainText("a page")
pdf = os.path.join(os.environ["HOME"], "page.pdf")
export_pdf(doc, pdf, title="Page")
w.open_path(pdf)
app.processEvents()
r["pdf_hidden"] = tb.isHidden()
w.tabs.setCurrentIndex(w._editor_widget_index(ed))
app.processEvents()
r["text_again"] = tb.isVisible()
txt = os.path.join(os.environ["HOME"], "plain.txt")
with open(txt, "w", encoding="utf-8") as f:
    f.write("plain text\n")
w.open_path(txt)
app.processEvents()
r["txt_style"] = tb.style_button.isEnabled()
r["txt_bold"] = tb.buttons["bold"].isEnabled()
r["txt_list"] = tb.buttons["bullets"].isEnabled()
ed.document().setModified(False)
for i in range(w.tabs.count()):
    e = w._editor_from_widget(w.tabs.widget(i))
    if e is not None:
        e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


NARROW = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import i18n
i18n.install("de")
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window

w = main_window.MainWindow()
w.resize(620, 600)
w.show()
ed = w.new_tab()
app.processEvents()
switch = w.view_switch
right = switch.mapTo(w, QPoint(switch.width(), 0)).x()
r = {"shown": switch.isVisible(), "inside": right <= w.width(),
     "outside_toolbar": not w.format_toolbar.isAncestorOf(switch)}
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_a_narrow_german_window_keeps_the_view_switch_in_sight(tmp_path):
    # Review L1: at 620 px the German toolbar moved the switch into its
    # chevron, where a narrow Split (Q-N) needs it.
    r = run_window_script(NARROW, tmp_path)
    assert r == {"shown": True, "inside": True, "outside_toolbar": True}


def test_the_toolbar_in_the_window(tmp_path):
    r = run_window_script(WINDOW_SCRIPT, tmp_path)
    assert r["shown"]
    assert r["insert_menu"]
    assert r["bold_checked"]
    assert r["style_body"] == "Body" and r["style_heading"] == "Heading 1"
    assert r["hidden"] and r["remembered"] is False and r["back"]
    assert r["pdf_hidden"] and r["text_again"]
    # Plain text keeps bold and italic, but has no paragraph styles or lists.
    assert r["txt_bold"] and not r["txt_style"] and not r["txt_list"]



def _ink(widget, dark: bool) -> int:
    """The strongest ink in a rendered control, on the toolbar behind it:
    the brightest pixel on a dark toolbar, the darkest on a light one (as
    0 to 255)."""
    image = widget.parentWidget().grab(widget.geometry()).toImage()
    values = [sum(image.pixelColor(x, y).getRgb()[:3]) // 3
              for x in range(image.width()) for y in range(image.height())]
    return max(values) if dark else min(values)


@pytest.mark.parametrize("dark", [True, False])
def test_a_control_that_cannot_act_looks_it(dark):
    # Review M7: in a plain-text tab the style pop-up kept its full text
    # color, and in dark mode the dimmed icons were barely dimmer.
    window = QMainWindow()
    registry = CommandRegistry(window)
    actions = {name: registry.add(Command(f"format.{name}", name.capitalize(),
                                          commands.FORMAT, checkable=True))
               for name in (*HISTORY, *LAYOUT) if name}
    menu = QMenu()
    menu.addAction("Body")
    bar = FormatToolbar(actions, menu, QMenu(), dark=dark, parent=window)
    window.addToolBar(bar)
    window.show()
    QApplication.processEvents()
    for widget, disable in ((bar.buttons["quote"], lambda: actions["quote"].setEnabled(False)),
                            (bar.style_button, lambda: bar.style_button.setEnabled(False))):
        live = _ink(widget, dark)
        disable()
        QApplication.processEvents()
        dimmed = _ink(widget, dark)
        assert abs(live - dimmed) >= 60, (widget, live, dimmed)
    window.close()


@pytest.mark.parametrize("dark", [True, False])
def test_a_button_that_is_on_has_a_visible_edge(dark):
    # Review L8: the fill alone was 1.3:1 (light) and 1.65:1 (dark).
    from constants import DARK_MUTED_FG, LIGHT_MUTED_FG
    window = QMainWindow()
    registry = CommandRegistry(window)
    actions = {name: registry.add(Command(f"format.{name}", name.capitalize(),
                                          commands.FORMAT, checkable=True))
               for name in (*HISTORY, *LAYOUT) if name}
    bar = FormatToolbar(actions, QMenu(), QMenu(), dark=dark, parent=window)
    edge = DARK_MUTED_FG if dark else LIGHT_MUTED_FG
    assert "QToolButton:checked" in bar.styleSheet() and edge in bar.styleSheet()


NARROWEST = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import i18n
i18n.install(sys.argv[2])
from PySide6.QtWidgets import QApplication, QToolButton
app = QApplication(sys.argv[:1])
import main_window

w = main_window.MainWindow()
w.show()
ed = w.new_tab()
w.resize(w.minimumSizeHint())
app.processEvents()
tb = w.format_toolbar
more = tb.findChild(QToolButton, "qt_toolbar_ext_button")
image = tb.grab(more.geometry()).toImage()
ground = image.pixelColor(0, 0)
drawn = sum(1 for x in range(image.width()) for y in range(image.height())
            if abs(image.pixelColor(x, y).lightness() - ground.lightness()) > 60)
r = {"undo": tb.buttons["undo"].isVisible(), "redo": tb.buttons["redo"].isVisible(),
     "more": more.isVisible() and drawn > 4, "switch": w.view_switch.isVisible()}
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


@pytest.mark.parametrize("language", ["en", "de"])
def test_undo_and_redo_stay_in_sight_in_the_narrowest_window(tmp_path, language):
    # The rest of the buttons go into the chevron first, which shows its »
    # (a pop-up button's room for its arrow once hid it).
    r = run_window_script(NARROWEST, tmp_path, language)
    assert r == {"undo": True, "redo": True, "more": True, "switch": True}


OVERFLOW = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication, QToolButton
app = QApplication(sys.argv[:1])
import main_window

w = main_window.MainWindow()
w.show()
ed = w.new_tab()
w.resize(w.minimumSizeHint())
app.processEvents()
tb = w.format_toolbar
menu = tb.findChild(QToolButton, "qt_toolbar_ext_button").menu()
r = {}
for _ in range(2):                                   # opened twice: no item twice
    menu.aboutToShow.emit()
    app.processEvents()
    entries = [a for a in menu.actions() if not a.isSeparator()]
    r.setdefault("menus", []).append([a.menu() is w.m_style for a in entries].count(True))
    r["insert"] = [a.menu() is w.m_insert for a in entries].count(True)
    r["first"] = entries[0].menu() is w.m_style
    r["last"] = entries[-1].menu() is w.m_insert
    r["bold"] = w.act_bold in entries
w.resize(1200, 600)
app.processEvents()
menu.aboutToShow.emit()
app.processEvents()
r["wide"] = [a.menu() is not None for a in menu.actions()].count(True)
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_the_chevron_holds_the_style_and_insert_popups_it_hides(tmp_path):
    r = run_window_script(OVERFLOW, tmp_path)
    assert r == {"menus": [1, 1], "insert": 1, "first": True, "last": True, "bold": True,
                 "wide": 0}


@pytest.mark.parametrize("dark", [True, False])
def test_every_popup_has_the_same_small_disclosure_arrow(toolbar, dark):
    """Qt's own arrows drew a dot (the style pop-up) or a heavy chevron
    (Undo, Redo, Insert) on a Mac: each pop-up shows one drawn arrow
    instead, of one size, in the theme's quiet ink, dimmed with it."""
    from format_toolbar import Disclosure, PopupButton
    toolbar.set_dark(dark)
    toolbar.window_.setCentralWidget(toolbar)
    toolbar.window_.resize(900, 60)
    toolbar.window_.show()
    QApplication.processEvents()
    buttons = [toolbar.buttons["undo"], toolbar.buttons["redo"], toolbar.insert_button,
               toolbar.style_button]
    sizes = set()
    for button in buttons:
        arrows = [c for c in button.children() if isinstance(c, Disclosure)]
        assert len(arrows) == 1, button
        arrow = arrows[0]
        assert arrow.isVisible() and button.rect().contains(arrow.geometry())
        assert arrow.geometry().right() >= button.width() - 24      # at the end
        sizes.add(arrow.size().width())
    assert len(sizes) == 1
    sheet = toolbar.styleSheet()
    assert "::menu-arrow { image: none; }" in sheet
    assert "#InsertButton::menu-indicator { image: none;" in sheet
    assert isinstance(toolbar.style_button, PopupButton)          # no Qt dot
    toolbar.actions_["undo"].setEnabled(False)
    undo_arrow = next(c for c in toolbar.buttons["undo"].children() if isinstance(c, Disclosure))
    assert undo_arrow.ink().alphaF() < 0.5
    toolbar.window_.hide()


def test_the_style_popup_follows_the_style_menu(toolbar):
    """One state for Format > Style, wherever it is offered: the menu's own
    action says whether it can be used, and the pop-up follows it."""
    from PySide6.QtWidgets import QMenu as Menu
    style_menu = toolbar.style_button.menu()
    assert isinstance(style_menu, Menu)
    style_menu.menuAction().setEnabled(False)
    assert not toolbar.style_button.isEnabled()
    style_menu.menuAction().setEnabled(True)
    assert toolbar.style_button.isEnabled()


SHOWN_WHOLE = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QToolButton
app = QApplication(sys.argv[:1])
import main_window

w = main_window.MainWindow()
w.show()
ed = w.new_tab()
app.processEvents()
w.resize(w.minimumSizeHint())          # its layout still to be done when it opens
tb = w.format_toolbar
menu = tb.findChild(QToolButton, "qt_toolbar_ext_button").menu()
seen = []

class Count(QObject):
    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.Paint):
            seen.append(len([a for a in menu.actions() if a.menu() is not None]))
        return False

count = Count()
menu.installEventFilter(count)
menu.popup(tb.mapToGlobal(tb.rect().bottomLeft()))
app.processEvents()
app.processEvents()
menu.hide()
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps({"seen": seen}))
"""


def test_the_chevron_menu_is_whole_from_its_first_paint(tmp_path):
    # Review H3: Style and Insert went in after it showed, so it painted,
    # then grew by two rows.
    r = run_window_script(SHOWN_WHOLE, tmp_path)
    assert r["seen"] and all(count == 2 for count in r["seen"]), r
