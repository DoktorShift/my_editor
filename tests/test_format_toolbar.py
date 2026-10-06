# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the format toolbar (format_toolbar.py).

What must hold:

  The toolbar's buttons are the window's own commands: the same actions,
  so state, keys and availability are the menu's. Each says its key in
  the platform's notation. Underline has no button.

  The buttons show the style at the caret, and the pop-up names the
  paragraph style.

  View > Show Toolbar hides and shows it, and is remembered; a PDF tab
  has no toolbar.

  When the window is narrow, the buttons that do not fit move to the
  toolbar's extension menu instead of being cut off.
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QToolButton  # noqa: E402

import commands  # noqa: E402
from commands import Command, CommandRegistry  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402
from format_toolbar import LAYOUT, FormatToolbar, tooltip_for  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
               for name in LAYOUT if name}
    style_menu = QMenu()
    for title in ("Body", "Heading 1", "Heading 2", "Heading 3"):
        style_menu.addAction(title)
    bar = FormatToolbar(actions, style_menu, dark=False, parent=window)
    bar.window_ = window                     # keep the window alive with the bar
    bar.actions_ = actions
    return bar


def test_the_buttons_are_the_window_commands(toolbar):
    assert [a for a in toolbar.actions() if a in toolbar.actions_.values()] == [
        toolbar.actions_[name] for name in LAYOUT if name]
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


def test_both_themes_draw_every_icon(toolbar):
    for dark in (True, False):
        toolbar.set_dark(dark)
        for name in LAYOUT:
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
w.tabs.setCurrentIndex(w.tabs.indexOf(ed.parent().parent()))
app.processEvents()
r["text_again"] = tb.isVisible()
txt = os.path.join(os.environ["HOME"], "plain.txt")
with open(txt, "w") as f:
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


def test_the_toolbar_in_the_window(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", WINDOW_SCRIPT, REPO], env=env,
                          capture_output=True, text=True, timeout=120)
    line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT ")), None)
    assert line, f"child failed:\n{proc.stdout}\n{proc.stderr}"
    r = json.loads(line[len("RESULT "):])
    assert r["shown"]
    assert r["bold_checked"]
    assert r["style_body"] == "Body" and r["style_heading"] == "Heading 1"
    assert r["hidden"] and r["remembered"] is False and r["back"]
    assert r["pdf_hidden"] and r["text_again"]
    # Plain text keeps bold and italic, but has no paragraph styles or lists.
    assert r["txt_bold"] and not r["txt_style"] and not r["txt_list"]
