# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins that a menu built for one click never takes the window's own menus
with it.

Seen in the app after a few minutes of writing: Format > Style and Color
were gone ("Internal C++ object (QMenu) already deleted"), and with them
the toolbar's state, the right-click menu and every new tab. PySide makes
a menu that ``addMenu`` (or ``insertMenu``) is given that menu's child, so
when the right-click menu or the toolbar's » menu was destroyed, Format's
submenus could go with it. They are offered there by their menu action
instead, which Qt treats the same, and which ties nothing to the menu.
"""

from tests.app_process import run_window_script

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import shiboken6
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QMenu, QToolButton
app = QApplication(sys.argv[:1])
import main_window
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None

w = main_window.MainWindow()
w.show()
ed = w.new_tab()
ed.setPlainText("words")
app.processEvents()


def alive():
    return [shiboken6.isValid(menu) for menu in (w.m_style, w.m_color, w.m_table, w.m_insert)]


r = {}
# The right-click menu: built for one click, then gone.
menu = QMenu()
w._fill_editor_context_menu(menu, ed, QPoint(1, 1))
r["offered"] = [a.text() for a in menu.actions()]
shiboken6.delete(menu)
r["after_right_click"] = alive()

# The toolbar's » menu, in a window too narrow for the style pop-up.
w.resize(420, 500)
app.processEvents()
toolbar = w.format_toolbar
chevron, toolbar._more_menu = toolbar._more_menu, QMenu()     # one to throw away
toolbar._hold_hidden_popups()
r["in_chevron"] = [a.text() for a in toolbar._more_menu.actions()]
shiboken6.delete(toolbar._more_menu)
toolbar._more_menu, toolbar._popup_entries = chevron, []
r["after_chevron"] = alive()
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_menus_built_for_one_click_never_take_the_windows_menus_with_them(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert "Color" in r["offered"] and "Style" in r["offered"]     # still offered there
    assert r["after_right_click"] == [True, True, True, True]
    assert "Style" in r["in_chevron"]
    assert r["after_chevron"] == [True, True, True, True]
