# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins that the side panels are panes of the window, not things beside it.

What must hold (Apple's guidelines for split views and toolbars, as Mail,
Notes and Xcode draw them):

  The panes are divided by one visible line, a point wide, in the
  window's separator color, from the top of the toolbar band to the
  bottom; the panels draw no edge of their own beside it.

  A panel's header is the toolbar band's section over that pane: as tall
  as the format toolbar, so its line meets the toolbar's line. The Drafts
  panel's search band below it ends on the line under the document tabs.
  Both hold when the text grows, and when the find bar opens the search
  band lets go of the tab row rather than stretching over the find bar.

  The panel says nothing at rest: no "Drafts for Alice" line. It speaks,
  in a banner over the list, only when there is something to know.
"""

from tests.app_process import run_window_script

SCRIPT = r"""
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLabel, QStackedWidget
app = QApplication(sys.argv[:1])
import main_window
from constants import DARK_BORDER, LIGHT_BORDER
from nostr.profiles import Profile
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None

w = main_window.MainWindow()
w.resize(1300, 760)
w.show()
w.nostr_state._active = True
w._on_nostr_in_use_changed(True)
panel = w._drafts_panel
panel.set_active_profile(Profile(user_pubkey="a" * 64, bunker_pubkey="b" * 64,
                                 bunker_relays=["wss://relay.example"],
                                 local_secret_hex="c" * 64, display_name="Alice"))
w._show_drafts_panel()
w.act_article_details.setChecked(True)
app.processEvents()
app.processEvents()
side = w._central_splitter.widget(0)
splitter = w._central_splitter

def top_of(widget):
    return widget.mapTo(w, QPoint(0, 0)).y()

def measure():
    stack = w.tabs.findChild(QStackedWidget)
    return {
        "toolbar": [top_of(w.format_bar), w.format_bar.height()],
        "band": [top_of(panel._top_band), panel._top_band.height()],
        "details": [top_of(w._article_details_panel._header),
                    w._article_details_panel._header.height()],
        "search_bottom": top_of(panel._search_band) + panel._search_band.height(),
        "tabs_content": top_of(stack),
    }

r = {"at_rest": measure()}
r["handle_width"] = splitter.handleWidth()
dark = w.is_dark_theme
line = (DARK_BORDER if dark else LIGHT_BORDER).lower()
handles = [splitter.handle(i) for i in range(1, splitter.count())]
r["handle_colours"] = [h.grab().toImage().pixelColor(h.width() // 2, h.height() // 2).name()
                       for h in handles if h.isVisible()]
r["line"] = line
r["panel_edges"] = ["border-left" in p.styleSheet()
                    for p in (panel, w._article_details_panel)]
r["resting_line"] = [l.text() for l in panel.findChildren(QLabel)
                     if l.isVisibleTo(panel) and "Alice" in l.text()]
font = QFont(app.font())
font.setPointSizeF(font.pointSizeF() * 1.4)
app.setFont(font)
for _ in range(4):
    app.processEvents()
r["larger_text"] = measure()
w.findbar.show()
for _ in range(4):
    app.processEvents()
r["find_bar"] = measure()
w.close()
print("RESULT " + json.dumps(r))
"""


def test_the_panels_are_sections_of_the_toolbar_band_with_a_visible_divider(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    for when in ("at_rest", "larger_text"):
        m = r[when]
        assert m["band"] == m["toolbar"], when          # same top, same height
        assert m["details"] == m["toolbar"], when
        assert m["search_bottom"] == m["tabs_content"], when
    assert r["find_bar"]["band"] == r["find_bar"]["toolbar"]
    assert r["find_bar"]["search_bottom"] < r["find_bar"]["tabs_content"]
    assert r["handle_width"] == 1
    assert r["handle_colours"] and set(r["handle_colours"]) == {r["line"]}
    assert r["panel_edges"] == [False, False]
    assert r["resting_line"] == []
