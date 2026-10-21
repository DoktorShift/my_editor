# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the rule for side panels: they go outside, the text keeps its width.

What must hold (the approved mockup, "Text width and side panels"):

  Opening a panel grows the window outward, a left panel to the left and a
  right one to the right, so the text keeps its place and its width;
  closing it gives back what the window grew, and a window opened and
  closed through any sequence of panels ends where it started.

  On a full screen (the window at its edges, maximized, full screen) the
  text gives way, never below a readable line; failing that, panels fold
  away by themselves: the file list first, then the panel open longest,
  never the one being opened.

The four screens of the mockup: a free window on a 1512 px MacBook Air, the
same maximized, a 2560 px Studio Display and a 1280 px small screen. A line
of 72 characters is 620 px there, the border 48 px at each side.
"""

import pytest

from panel_layout import (
    PAGE_GROUND, LayoutPlan, Panel, Rect, min_text_width, plan_closing, plan_opening,
)

CHAR = 620 / 72                      # a line of 72 characters is 620 px
INSET = 48
FULL_MIN = min_text_width(False, CHAR, INSET)
HEIGHT = 900


def screen(width, x=0):
    return Rect(x, 0, width, HEIGHT)


class Desk:
    """A window on a screen, opening and closing panels as MyEditor will:
    each opening's growth kept with its panel, panels folded for it shown
    again when it closes."""

    def __init__(self, scr, window, maximized=False, full_screen=False, min_text=FULL_MIN):
        self.screen, self.window = scr, window
        self.maximized, self.full_screen = maximized, full_screen
        self.min_text = min_text
        self.text = window.w            # no panels yet; no chrome in these tests
        self.open = []                   # panels shown, in the order opened
        self.grew = {}                   # name -> (left, right)
        self.folded_for = {}             # name -> panels folded when it opened
        self.clock = 0

    def opening(self, name, side, width, rank=1):
        self.clock += 1
        panel = Panel(name, side, width, self.clock, rank)
        plan = plan_opening(self.screen, self.window, self.open + [panel], self.text,
                            self.min_text, self.maximized, self.full_screen)
        folded = [p for p in self.open if p.name in plan.collapsed]
        self.open = [p for p in self.open if p.name not in plan.collapsed] + [panel]
        self.window, self.text = plan.window, plan.text_width
        self.grew[name] = (plan.grew_left, plan.grew_right)
        self.folded_for[name] = folded
        return plan

    def closing(self, name):
        panel = next(p for p in self.open if p.name == name)
        self.open.remove(panel)
        self.window = plan_closing(self.screen, self.window, panel, *self.grew.pop(name))
        self.open += self.folded_for.pop(name)      # back into the room it leaves
        left = sum(p.width for p in self.open if p.side == "left")
        right = sum(p.width for p in self.open if p.side == "right")
        self.text = self.window.w - left - right
        return self.window

    def text_left(self):
        return self.window.x + sum(p.width for p in self.open if p.side == "left")


# -------- the readable floor --------

def test_the_floor_at_full_width_is_72_characters_and_the_border():
    assert FULL_MIN == 620 + 2 * INSET == 716


def test_the_floor_in_paper_mode_is_the_page_and_a_little_ground():
    page_margin = 14
    floor = min_text_width(True, CHAR, page_margin)
    assert floor == 620 + 2 * page_margin + 2 * PAGE_GROUND


def test_the_floor_follows_the_measure_and_is_never_negative():
    assert min_text_width(False, 10, 0, measure=60) == 600
    assert min_text_width(False, -3, -5) == 0


# -------- a free window on a 1512 px MacBook Air --------

def test_a_left_panel_grows_the_window_to_the_left_and_the_text_stays():
    desk = Desk(screen(1512), Rect(560, 40, 880, HEIGHT - 80))
    before = desk.text_left()
    plan = desk.opening("sidebar", "left", 232)
    assert plan.window == Rect(328, 40, 1112, HEIGHT - 80)
    assert (plan.grew_left, plan.grew_right) == (232, 0)
    assert plan.text_width == 880 and plan.collapsed == ()
    assert desk.text_left() == before


def test_a_right_panel_at_the_screen_edge_moves_the_window_along():
    desk = Desk(screen(1512), Rect(560, 40, 880, HEIGHT - 80))
    plan = desk.opening("details", "right", 300)
    # 1180 wide from 560 would end at 1740: it moves left to end at 1512.
    assert plan.window == Rect(332, 40, 1180, HEIGHT - 80)
    assert (plan.grew_left, plan.grew_right) == (228, 72)
    assert plan.text_width == 880


def test_the_free_window_folds_the_list_when_all_three_do_not_fit():
    desk = Desk(screen(1512), Rect(560, 40, 880, HEIGHT - 80))
    desk.opening("sidebar", "left", 232)
    desk.opening("list", "left", 300, rank=0)
    assert desk.window == Rect(28, 40, 1412, HEIGHT - 80) and desk.text == 880
    plan = desk.opening("details", "right", 300)
    # As the mockup: the list folds, the window sits at 100, the text keeps 880.
    assert plan.collapsed == ("list",)
    assert plan.window == Rect(100, 40, 1412, HEIGHT - 80)
    assert plan.text_width == 880


def test_closing_in_reverse_returns_the_window_to_where_it_was():
    original = Rect(560, 40, 880, HEIGHT - 80)
    desk = Desk(screen(1512), original)
    desk.opening("sidebar", "left", 232)
    desk.opening("list", "left", 300, rank=0)
    with_list = desk.window
    desk.opening("details", "right", 300)
    assert desk.closing("details") == with_list      # the list back in its room
    assert desk.text == 880
    desk.closing("list")
    desk.closing("sidebar")
    assert desk.window == original and desk.text == 880


def test_closing_in_any_order_returns_the_window_to_where_it_was():
    original = Rect(300, 40, 700, HEIGHT - 80)
    desk = Desk(screen(1512), original)
    desk.opening("sidebar", "left", 232)
    desk.opening("details", "right", 300)
    left_before = desk.text_left()
    desk.closing("sidebar")
    assert desk.text_left() == left_before            # the text does not move
    desk.closing("details")
    assert desk.window == original and desk.text == 700


def test_closing_a_right_panel_shrinks_from_the_right_only():
    desk = Desk(screen(1512), Rect(100, 40, 700, HEIGHT - 80))
    desk.opening("details", "right", 300)
    assert desk.window == Rect(100, 40, 1000, HEIGHT - 80)
    assert desk.closing("details") == Rect(100, 40, 700, HEIGHT - 80)


def test_closing_gives_back_no_more_than_the_panel_is_wide():
    scr = screen(1512)
    window = Rect(0, 0, 1200, HEIGHT)
    narrowed = Panel("sidebar", "left", 180, 1, 1)   # dragged narrower since it opened
    assert plan_closing(scr, window, narrowed, 232, 0) == Rect(180, 0, 1020, HEIGHT)


# -------- maximized on the 1512 px MacBook Air --------

def test_a_maximized_window_keeps_its_size_and_the_text_gives_way():
    whole = Rect(0, 0, 1512, HEIGHT)
    desk = Desk(screen(1512), whole, maximized=True)
    desk.opening("sidebar", "left", 232)
    plan = desk.opening("list", "left", 300, rank=0)
    assert plan.window == whole and plan.text_width == 980
    assert (plan.grew_left, plan.grew_right) == (0, 0)


def test_a_maximized_window_folds_the_list_before_the_text_gets_too_narrow():
    whole = Rect(0, 0, 1512, HEIGHT)
    desk = Desk(screen(1512), whole, maximized=True)
    desk.opening("sidebar", "left", 232)
    desk.opening("list", "left", 300, rank=0)
    plan = desk.opening("details", "right", 300)
    assert plan.collapsed == ("list",) and plan.text_width == 980
    desk.closing("details")
    assert desk.window == whole and desk.text == 980   # the list is back


def test_a_full_screen_window_keeps_its_size_too():
    whole = Rect(0, 0, 1512, HEIGHT)
    plan = plan_opening(screen(1512), whole, [Panel("details", "right", 300, 1, 1)],
                        1512, FULL_MIN, maximized=False, full_screen=True)
    assert plan.window == whole and plan.text_width == 1212


# -------- a 2560 px Studio Display --------

def test_a_large_screen_keeps_every_panel_open():
    desk = Desk(screen(2560), Rect(0, 0, 2560, HEIGHT), maximized=True)
    desk.opening("sidebar", "left", 232)
    desk.opening("list", "left", 300, rank=0)
    plan = desk.opening("details", "right", 300)
    assert plan.collapsed == () and plan.text_width == 2560 - 832


# -------- a 1280 px small screen --------

def test_a_small_screen_folds_the_list_first():
    desk = Desk(screen(1280), Rect(0, 0, 1280, HEIGHT), maximized=True)
    desk.opening("sidebar", "left", 232)
    plan = desk.opening("list", "left", 300, rank=0)
    assert plan.collapsed == () and plan.text_width == 748
    plan = desk.opening("details", "right", 300)
    assert plan.collapsed == ("list",) and plan.text_width == 748


def test_then_the_panel_open_longest_folds():
    desk = Desk(screen(1280), Rect(0, 0, 1280, HEIGHT), maximized=True)
    desk.opening("sidebar", "left", 232)
    desk.opening("drafts", "right", 300)
    plan = desk.opening("details", "right", 200)
    # 1280 - 732 = 548 is too narrow; the sidebar opened before the drafts.
    assert plan.collapsed == ("sidebar",) and plan.text_width == 780


def test_panels_fold_until_the_text_has_its_line():
    desk = Desk(screen(1280), Rect(0, 0, 1280, HEIGHT), maximized=True)
    desk.opening("sidebar", "left", 232)
    desk.opening("drafts", "right", 300)
    plan = desk.opening("details", "right", 300)
    # Without the sidebar 680 is still under 716: the drafts fold as well.
    assert plan.collapsed == ("sidebar", "drafts") and plan.text_width == 980


def test_the_panel_being_opened_never_folds():
    whole = Rect(0, 0, 800, HEIGHT)
    plan = plan_opening(screen(800), whole, [Panel("details", "right", 300, 5, 0)],
                        800, FULL_MIN, maximized=True, full_screen=False)
    assert plan.collapsed == () and plan.text_width == 500


def test_paper_mode_lets_panels_use_the_ground_beside_the_page():
    page_floor = min_text_width(True, CHAR, 14)        # 724
    desk = Desk(screen(1280), Rect(0, 0, 1280, HEIGHT), maximized=True, min_text=page_floor)
    desk.opening("sidebar", "left", 232)
    plan = desk.opening("list", "left", 300, rank=0)
    assert plan.collapsed == () and plan.text_width == 748 >= page_floor


# -------- sizes and screens --------

def test_sizes_are_never_negative():
    tiny = screen(400)
    panels = [Panel("sidebar", "left", 232, 1, 1), Panel("details", "right", 300, 2, 1)]
    plan = plan_opening(tiny, Rect(0, 0, 400, HEIGHT), panels, 168, FULL_MIN, True, False)
    assert plan.text_width >= 0 and plan.window.w >= 0
    assert plan.collapsed == ("sidebar",)
    back = plan_closing(tiny, Rect(0, 0, 10, HEIGHT), panels[1], 300, 0)
    assert back.w >= 1 and back.x >= 0


def test_a_text_already_narrow_is_not_made_a_reason_to_fold():
    # A window the person made narrow: opening a panel grows it, nothing folds.
    plan = plan_opening(screen(1512), Rect(400, 0, 500, HEIGHT),
                        [Panel("list", "left", 300, 1, 0), Panel("details", "right", 300, 2, 1)],
                        200, FULL_MIN, False, False)
    assert plan.collapsed == () and plan.text_width == 200
    assert plan.window == Rect(400, 0, 800, HEIGHT)


def test_the_window_beside_another_screen_stays_on_its_own():
    # A second monitor to the right of a 1512 px one: it starts at 1512.
    second = screen(1920, x=1512)
    desk = Desk(second, Rect(1600, 40, 900, HEIGHT - 80))
    plan = desk.opening("sidebar", "left", 232)
    # 1600 - 232 would cross onto the first screen: it stops at 1512.
    assert plan.window == Rect(1512, 40, 1132, HEIGHT - 80)
    assert (plan.grew_left, plan.grew_right) == (88, 144)
    desk.closing("sidebar")
    assert desk.window == Rect(1600, 40, 900, HEIGHT - 80)


def test_a_screen_left_of_the_main_one_has_negative_coordinates():
    left_screen = screen(1440, x=-1440)
    desk = Desk(left_screen, Rect(-1000, 0, 700, HEIGHT))
    plan = desk.opening("details", "right", 300)
    assert plan.window == Rect(-1000, 0, 1000, HEIGHT)
    plan = desk.opening("drafts", "right", 300)
    # -1000 + 1300 would reach onto the main screen: it moves left instead.
    assert plan.window == Rect(-1300, 0, 1300, HEIGHT)
    desk.closing("drafts")
    desk.closing("details")
    assert desk.window == Rect(-1000, 0, 700, HEIGHT)


def test_no_panels_change_nothing():
    window = Rect(10, 20, 900, 700)
    assert plan_opening(screen(1512), window, [], 900, FULL_MIN, False, False) == \
        LayoutPlan(window, (), 900, 0, 0)


@pytest.mark.parametrize("width", [1280, 1512, 2560])
def test_opening_and_closing_every_panel_returns_the_original_rect(width):
    original = Rect(width // 4, 30, width // 2, HEIGHT - 60)
    desk = Desk(screen(width), original)
    desk.opening("sidebar", "left", 232)
    desk.opening("list", "left", 300, rank=0)
    desk.opening("details", "right", 300)
    desk.opening("drafts", "right", 280)
    while desk.open:                     # the last opened first
        desk.closing(max(desk.open, key=lambda p: p.opened_at).name)
    assert desk.window == original and desk.text == original.w
