# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins what the window says about the document in front, and where.

What must hold:

  The window's title names the document in front (a Mac shows unsaved
  changes in the close button, Windows and Linux add "- MyEditor"), so the
  title bar, the taskbar, Alt+Tab and a screen reader have a name to give.
  It used to stay empty.

  The tab's tooltip says where the file is. The status bar no longer
  repeats it (with "(Untitled)" for a new document), nor the PDF's page
  number, which the reader's toolbar shows.

  The status bar's lasting part is the word count, and, in code and plain
  text, the language and the line; a writing document has no line count.
  There is no Paper switch in it: View > Paper Mode is the one.

  A tab's close button is the tab's quiet color, not red, and is named
  "Close Tab" for a screen reader (it used to be the glyph "×").
"""

import sys

from tests.app_process import run_window_script

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication, QTabBar, QToolButton
app = QApplication(sys.argv[:1])
import main_window

home = os.environ["HOME"]
note = os.path.join(home, "note.md")
with open(note, "w", encoding="utf-8") as f:
    f.write("Some words to count.\n")
code = os.path.join(home, "tool.py")
with open(code, "w", encoding="utf-8") as f:
    f.write("print('hi')\nprint('there')\n")

w = main_window.MainWindow()
w.show()
app.processEvents()
r = {}
ed = w.open_path(note)
app.processEvents()
index = w.tabs.currentIndex()
r["title"] = w.windowTitle()
r["modified_before"] = w.isWindowModified()
ed.insertPlainText("More ")
app.processEvents()
r["modified_after"] = w.isWindowModified()
r["tooltip"] = w.tabs.tabToolTip(index)
r["message"] = w.statusBar().currentMessage()
r["line_label"] = w._line_label.text()
r["words"] = w._words_label.text()
close = w.tabs.tabBar().tabButton(index, QTabBar.RightSide)
r["close_name"] = close.accessibleName()
r["close_object"] = close.objectName()
r["red_in_window_css"] = "#A84444" in w.styleSheet() or "#A84444" in close.styleSheet()
# What the status bar shows: the backup's Try Again only while a backup fails.
r["status_buttons"] = [b.text() for b in w.statusBar().findChildren(QToolButton)
                      if b.isVisibleTo(w)]
source = w.open_path(code)
app.processEvents()
r["source_line_label"] = w._line_label.text()
for i in range(w.tabs.count()):
    e = w._editor_from_widget(w.tabs.widget(i))
    if e is not None:
        e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_the_window_names_the_document_and_the_status_bar_repeats_nothing(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    expected = "note.md[*]" if sys.platform == "darwin" else "note.md[*] - MyEditor"
    assert r["title"] == expected
    assert r["modified_before"] is False and r["modified_after"] is True
    assert r["tooltip"] == str(tmp_path / "note.md")
    assert str(tmp_path) not in r["message"] and "Untitled" not in r["message"]
    assert r["line_label"] == ""                        # a writing document: no lines
    assert "word" in r["words"]
    assert r["close_name"] == "Close Tab" and r["close_object"] == "TabClose"
    assert r["red_in_window_css"] is False
    assert r["status_buttons"] == []                    # no Paper switch
    assert r["source_line_label"].endswith("Ln 1 / 3")
    assert "Python" in r["source_line_label"]


FULL_SCREEN_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window

w = main_window.MainWindow()
w.show()
w.showMaximized()
app.processEvents()
r = {"at_start": w.act_fullscreen.text(), "checkable": w.act_fullscreen.isCheckable()}
w.act_fullscreen.trigger()
app.processEvents()
r["in_full_screen"] = [w.isFullScreen(), w.act_fullscreen.text()]
w.act_fullscreen.trigger()
app.processEvents()
r["back"] = [w.isFullScreen(), w.isMaximized(), w.act_fullscreen.text()]
w.setWindowState(Qt.WindowState.WindowFullScreen)    # the window's own button
app.processEvents()
r["by_the_window"] = w.act_fullscreen.text()
w.close()
print("RESULT " + json.dumps(r))
"""


def test_full_screen_is_named_for_what_it_does_next(tmp_path):
    # Review, decision 4: Notes, Pages and Finder say Enter Full Screen and
    # then Exit Full Screen, not a checkmarked "Full Screen".
    r = run_window_script(FULL_SCREEN_SCRIPT, tmp_path)
    assert r["at_start"] == "Enter Full Screen" and not r["checkable"]
    assert r["in_full_screen"] == [True, "Exit Full Screen"]
    assert r["back"] == [False, True, "Enter Full Screen"]      # maximized again
    assert r["by_the_window"] == "Exit Full Screen"


CLOSE_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window

w = main_window.MainWindow()
w.show()
w.new_tab()
app.processEvents()
r = {}
for name, opener, attr in (("settings", w._show_settings, "_settings_window"),
                           ("shortcuts", w._show_shortcuts, "_shortcuts_window")):
    opener()
    app.processEvents()
    getattr(w, attr).activateWindow()
    app.processEvents()
    tabs = w.tabs.count()
    w.act_close_tab.trigger()          # what a Mac's menu bar does with Command-W
    app.processEvents()
    window = getattr(w, attr, None)
    r[name] = {"closed": window is None or not window.isVisible(),
               "tabs_closed": tabs - w.tabs.count()}
w.activateWindow()
app.processEvents()
tabs = w.tabs.count()
w.act_close_tab.trigger()
app.processEvents()
r["document"] = tabs - w.tabs.count()
for i in range(w.tabs.count()):
    ed = w._editor_from_widget(w.tabs.widget(i))
    if ed is not None:
        ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_command_w_closes_the_window_in_front_not_a_tab_behind_it(tmp_path):
    # Review R1: on a Mac the menu bar's Close Tab takes Command-W before
    # Settings or Keyboard Shortcuts sees it, and closed a document tab
    # behind the window instead.
    r = run_window_script(CLOSE_SCRIPT, tmp_path)
    assert r["settings"] == {"closed": True, "tabs_closed": 0}
    assert r["shortcuts"] == {"closed": True, "tabs_closed": 0}
    assert r["document"] == 1                   # in the document window, a tab
