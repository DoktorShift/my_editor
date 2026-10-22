# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The main window's layout: Paper Mode from the toolbar, and side panels
that sit outside the text (side_panels.py, panel_layout.py)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import settings  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402
from tests.window_fixture import close_window, make_window  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch):
    """A main window with settings, recent files, the last session and
    Nostr accounts of its own."""
    QApplication.instance() or QApplication([])
    w = make_window(tmp_path, monkeypatch)
    yield w
    close_window(w)


def test_the_paper_button_turns_paper_mode_on_and_off(window):
    button = window._paper_toggle
    ed = window.current_editor()
    assert button.defaultAction() is window.act_paper_mode
    assert button.accessibleName() == "Paper Mode"
    assert window.format_bar.isAncestorOf(button)
    assert not window.format_toolbar.isAncestorOf(button)   # never in the chevron
    was = window.paper_mode
    button.click()
    assert window.paper_mode is (not was)
    assert window.act_paper_mode.isChecked() is (not was) and button.isChecked() is (not was)
    assert ed._paper_mode is (not was)
    assert settings.load_settings()["paper_mode"] is (not was)
    window.act_paper_mode.trigger()                       # View > Paper Mode
    assert window.paper_mode is was and button.isChecked() is was


def test_the_toolbar_names_every_control(window):
    window.show()
    QApplication.processEvents()
    assert unnamed_controls(window.format_bar) == []


PANELS_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window, panel_layout
from settings import load_settings
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None
screen = {"room": panel_layout.Rect(0, 0, 2000, 1200)}
main_window.MainWindow._screen_room = lambda self: screen["room"]

def settle():
    for _ in range(5):
        app.processEvents()

r = {}
w = main_window.MainWindow()
# The panels at the right alone (the sidebar stays hidden, also next time).
w._library_window.set_pane_shown("sidebar", False)
w.setGeometry(100, 100, 1000, 700)
w.show(); settle()
w.nostr_state._active = True
panels = w._side_panels
r["before"] = [w.geometry().getRect(), w._editor_side.width()]
panels.open("drafts"); settle()
r["drafts_open"] = [w.geometry().getRect(), w._editor_side.width(),
                    w._drafts_panel.width(), list(panels.growth("drafts"))]
handle = w._central_splitter.handle(w._central_splitter.indexOf(w._drafts_panel))
r["handle"] = [handle.geometry().width(), handle.mask().boundingRect().width()]
# Dragged narrower: kept within its narrowest and widest, remembered by name,
# and the window stays where it is.
sizes = w._central_splitter.sizes()
index = w._central_splitter.indexOf(w._drafts_panel)
sizes[w._central_splitter.indexOf(w._editor_side)] += 40; sizes[index] -= 40
w._central_splitter.setSizes(sizes)
w._central_splitter.splitterMoved.emit(0, index)
panels.flush(); settle()
r["dragged"] = [w.geometry().getRect(), w._drafts_panel.width(),
                load_settings().get("panel_widths", {}).get("drafts")]
panels.close("drafts"); settle()
r["drafts_closed"] = [w.geometry().getRect(), w._editor_side.width()]

# A full screen: the text gives way down to its line, then the panel open
# longest folds away, and comes back when the one that folded it closes.
w.setGeometry(0, 50, 1100, 700); settle()
r["full_before"] = w.geometry().getRect()
screen["room"] = panel_layout.Rect(w.geometry().x(), 0, 1100, 800)
w._show_drafts_panel(); settle()
w.act_article_details.setChecked(True); settle()
r["full"] = [w.geometry().getRect(), w._drafts_panel.isVisible(),
             w._article_details_panel.isVisible(), w.act_nostr_drafts.isChecked(),
             w._drafts_toggle.isChecked(), w._editor_side.width() >= w._min_text_width(),
             list(panels.folded_by("article_details"))]
w.act_article_details.setChecked(False); settle()
r["unfolded"] = [w._drafts_panel.isVisible(), w.act_nostr_drafts.isChecked(),
                 w._drafts_toggle.isChecked()]

# Remembered without the room it grew for its panels.
screen["room"] = panel_layout.Rect(0, 0, 2000, 1200)
w._hide_drafts_panel(); settle()
w.setGeometry(50, 60, 700, 400); settle()
w._show_drafts_panel(); settle()
r["grown_at_close"] = w.geometry().getRect()
w.close(); settle()
second = main_window.MainWindow()
r["restored"] = [second.geometry().getRect(), second._side_panels.width_of("drafts"),
                 second._side_panels.width_of("article_details")]
second.close()
print("RESULT " + json.dumps(r))
"""


@pytest.fixture(scope="module")
def panels(tmp_path_factory):
    from tests.app_process import run_window_script
    return run_window_script(PANELS_SCRIPT, tmp_path_factory.mktemp("panels"))


def test_opening_a_side_panel_grows_the_window_outward(panels):
    (x, y, width, height), text = panels["before"]
    (nx, ny, nwidth, nheight), ntext, panel, grew = panels["drafts_open"]
    assert (nx, ny, nheight) == (x, y, height)          # it grows to the right
    # By the panel's width and its divider's.
    assert nwidth == width + panel + 1 and grew == [0, panel + 1]
    assert ntext == text                                # the text keeps its width
    assert panel == 320


def test_closing_a_side_panel_gives_back_what_the_window_grew(panels):
    (x, y, width, height), text = panels["before"]
    rect, closed_text = panels["drafts_closed"]
    # It gives back the panel's width as dragged (40 narrower than it grew).
    assert rect == [x, y, width + 40, height]
    assert closed_text == text + 40


def test_a_divider_is_thin_to_see_and_easy_to_grab(panels):
    grab, drawn = panels["handle"]
    assert drawn == 1 and grab >= 5


def test_a_dragged_panel_width_is_remembered_and_the_window_stays(panels):
    rect, width, kept = panels["dragged"]
    assert rect == panels["drafts_open"][0]
    assert width == 280 and kept == 280
    assert panels["restored"][1] == 280                 # the next launch too
    assert panels["restored"][2] == 300                 # never dragged: its default


def test_on_a_full_screen_the_panel_open_longest_folds_away_and_comes_back(panels):
    rect, drafts, details, drafts_command, drafts_button, readable, folded = panels["full"]
    assert rect == panels["full_before"]
    assert details and not drafts and folded == ["drafts"]
    assert not drafts_command and not drafts_button
    assert readable
    assert panels["unfolded"] == [True, True, True]


def test_the_window_is_remembered_without_the_room_it_grew_for_panels(panels):
    # (Small enough for the offscreen screen the window is restored on.)
    assert panels["grown_at_close"] == [50, 60, 700 + 281, 400]
    assert panels["restored"][0] == [50, 60, 700, 400]


class _Window:
    """A top-level window holding a PanelSplitter: the text and panels."""

    def __init__(self, tmp_path, monkeypatch, *, maximized=False):
        from PySide6.QtWidgets import QVBoxLayout, QWidget
        import panel_layout
        import side_panels
        monkeypatch.setattr(side_panels, "load_settings", lambda: {})
        monkeypatch.setattr(side_panels, "save_setting", lambda key, value: None)

        class Top(QWidget):
            def isMaximized(self):  # noqa: N802
                return maximized

        self.top = Top()
        self.splitter = side_panels.PanelSplitter()
        self.splitter.setHandleWidth(1)
        self.text = QWidget()
        self.splitter.addWidget(self.text)
        QVBoxLayout(self.top).addWidget(self.splitter)
        self.top.layout().setContentsMargins(0, 0, 0, 0)
        self.panels = side_panels.SidePanels(
            self.top, self.splitter, self.text,
            screen_room=lambda: panel_layout.Rect(0, 0, 3000, 1500), text_floor=lambda: 400)
        self.made = {}
        for name, side in (("sidebar", side_panels.LEFT), ("file_list", side_panels.LEFT),
                           ("details", side_panels.RIGHT)):
            widget = QWidget()
            widget.setMinimumWidth(150)
            widget.setMaximumWidth(400)
            self.made[name] = widget
            self.panels.register(name, widget, side=side, collapse_rank=0 if name == "file_list"
                                 else 1, default_width=200)
        self.top.setGeometry(500, 100, 1000, 600)
        self.top.show()
        settle()


def settle():
    for _ in range(5):
        QApplication.processEvents()


def test_left_panels_go_before_the_text_and_grow_the_window_to_the_left(tmp_path, monkeypatch):
    import panel_layout
    import side_panels
    w = _Window(tmp_path, monkeypatch)
    # The screen this runs on, as the window's own sees it: a real one
    # (Windows' own platform) keeps a window and its frame within it, so
    # the window stands where the panel's width fits beside it.
    screen = QApplication.primaryScreen().availableGeometry()
    w.panels._screen_room = lambda: side_panels.inner_room(w.top, panel_layout.Rect(
        screen.x(), screen.y(), screen.width(), screen.height()))
    room = w.panels._screen_room()
    w.top.setGeometry(room.x + 250, room.y + 40, 500, 400)
    settle()
    order = [w.splitter.widget(i) for i in range(w.splitter.count())]
    assert order == [w.made["sidebar"], w.made["file_list"], w.text, w.made["details"]]
    x, width, text = w.top.geometry().x(), w.top.width(), w.text.width()
    w.panels.open("sidebar")
    settle()
    assert w.top.geometry().x() == x - 201 and w.top.width() == width + 201
    assert w.text.width() == text and w.panels.growth("sidebar") == (201, 0)
    w.panels.close("sidebar")
    settle()
    assert (w.top.geometry().x(), w.top.width(), w.text.width()) == (x, width, text)
    w.top.close()


def test_a_maximized_window_keeps_its_size_and_the_text_gives_way(tmp_path, monkeypatch):
    w = _Window(tmp_path, monkeypatch, maximized=True)
    rect, text = w.top.geometry(), w.text.width()
    w.panels.open("details")
    settle()
    assert w.top.geometry() == rect
    assert w.text.width() == text - 201 and w.panels.growth("details") == (0, 0)
    w.panels.close("details")
    settle()
    assert w.top.geometry() == rect and w.text.width() == text
    w.top.close()


def test_a_panel_opens_within_its_narrowest_and_widest(tmp_path, monkeypatch):
    w = _Window(tmp_path, monkeypatch)
    w.panels._widths["details"] = 900
    assert w.panels.width_of("details") == 400
    w.panels._widths["details"] = 20
    assert w.panels.width_of("details") == 150
    w.top.close()


def test_a_window_reaching_past_its_screen_is_left_as_it_is(tmp_path, monkeypatch):
    import panel_layout
    w = _Window(tmp_path, monkeypatch)
    w.panels._screen_room = lambda: panel_layout.Rect(0, 0, 800, 600)
    rect, text = w.top.geometry(), w.text.width()
    w.panels.open("details")
    settle()
    assert w.top.geometry() == rect and w.text.width() == text - 201
    w.top.close()


LAUNCHES_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
screen = os.path.join(os.path.expanduser("~"), "screen.json")
with open(screen, "w") as f:
    json.dump({"synchronousWindowSystemEvents": False, "windowFrameMargins": False,
               "screens": [{"name": "big", "x": 0, "y": 0, "width": 2400, "height": 1300,
                            "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1}]}, f)
# Qt splits the platform's options at every colon, a Windows drive's
# too (the platform then ends the process without a word): the file is
# named from its own folder.
os.chdir(os.path.dirname(screen))
os.environ["QT_QPA_PLATFORM"] = "offscreen:configfile=" + os.path.basename(screen)
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None

def settle():
    for _ in range(10):
        app.processEvents()

def look(w):
    return [w.geometry().getRect(), w._editor_side.width()]

folder = os.path.join(os.path.expanduser("~"), "Notes")
os.makedirs(folder)
r = {}
w = main_window.MainWindow()
w.setGeometry(400, 100, 1100, 720)
w.show(); settle()
w._library_window.add_root(folder); settle()
r["first"] = look(w)
w.close(); settle()
for n in range(2):
    w = main_window.MainWindow(); w.show(); settle()
    r["launch%d" % n] = look(w)
    w._library_window.toggle_pane("sidebar"); settle()
    r["hidden%d" % n] = look(w)
    w._library_window.toggle_pane("sidebar"); settle()
    r["shown%d" % n] = look(w)
    w.close(); settle()
print("RESULT " + json.dumps(r))
"""


def test_the_window_keeps_its_size_from_launch_to_launch(tmp_path):
    """Panes open when the window was last closed open with it again; the
    room they take is the room the window grew for them, so hiding one
    gives back exactly its width, and launches never widen the window."""
    from tests.app_process import run_window_script
    r = run_window_script(LAUNCHES_SCRIPT, tmp_path)
    for n in range(2):
        assert r["launch%d" % n] == r["first"]
        (x, y, width, height), text = r["launch%d" % n]
        (hx, hy, hwidth, hheight), htext = r["hidden%d" % n]
        assert htext == text and hwidth < width and hx > x       # the room goes back
        assert r["shown%d" % n] == r["first"]
