# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where side panels go: outside the text, which keeps its width.

The text is the middle of MyEditor. A side panel (the folder sidebar, the
file list, Article Details, Drafts) is room added beside it, not room
taken from it: opening one grows the window outward, a panel at the left
to the left and one at the right to the right, so the text stays where it
was on the screen and as wide as it was. Closing the panel gives back what
the window grew for it, and nothing more.

Only when the screen has no more room (the window reaches its edges, or
is maximized or full screen) does the text give way, and never below a
readable line (min_text_width): at full width 72 characters with the quiet
border beside them, in Paper Mode the page with a little ground on either
side. Should even that not fit, panels fold away by themselves, as Apple's
guidelines advise for split views that run out of room: the file list
first, then whichever panel has been open longest; never the one being
opened. They come back when the panel whose opening folded them closes.

This module only does the arithmetic: plain numbers in, plain numbers
out, no widgets, so it can be pinned by tests and used for any window.
Rectangles are in the screen's logical pixels (Qt's availableGeometry),
which on a second monitor need not start at 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Ground left beside Paper Mode's page when panels need room, at each
# side, in logical pixels: enough that the page still reads as a page
# laid on something, not cut at its edges.
PAGE_GROUND = 24


@dataclass(frozen=True)
class Rect:
    """A rectangle on the screen, in logical pixels."""
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class Panel:
    """A side panel: which side it sits on, how wide it is, when it was
    opened (a counter that only goes up), and how soon it folds away when
    the screen is full (lower folds first: the file list is 0, the folder
    sidebar and the panels at the right 1)."""
    name: str
    side: str            # "left" | "right"
    width: int
    opened_at: int
    collapse_rank: int


@dataclass(frozen=True)
class LayoutPlan:
    """What opening a panel does: the window's new rectangle, the panels
    folded away to make room (by name, in the order they were folded),
    the width left for the text, and how far the window grew at each side
    (negative where it moved in). Keep ``grew_left`` and ``grew_right``
    with the panel: closing it gives them back (plan_closing)."""
    window: Rect
    collapsed: tuple[str, ...]
    text_width: int
    grew_left: int
    grew_right: int


def min_text_width(paper: bool, char_width: float, inset: int, measure: int = 72) -> int:
    """The narrowest the text area may become for side panels: a line of
    ``measure`` characters of ``char_width`` (the writing font's average
    character width, as the editor measures its page), ``inset`` beside
    it at each side (at full width the editor's border, PROSE_INSET; in
    Paper Mode the page's own margin), and in Paper Mode a little ground
    beside the page (PAGE_GROUND)."""
    line = math.ceil(max(0.0, float(char_width)) * max(0, int(measure)))
    side = max(0, int(inset)) + (PAGE_GROUND if paper else 0)
    return line + 2 * side


def _clamp(value: int, low: int, high: int) -> int:
    """``value`` kept within ``low`` and ``high``; ``low`` wins should
    the two cross (a window wider than the screen keeps its left edge on
    it)."""
    return max(low, min(value, high))


def _sides(panels) -> tuple[int, int]:
    left = sum(max(0, p.width) for p in panels if p.side == "left")
    right = sum(max(0, p.width) for p in panels if p.side != "left")
    return left, right


def plan_opening(screen: Rect, window: Rect, open_panels: list[Panel],
                 text_width_now: int, min_text: int,
                 maximized: bool, full_screen: bool) -> LayoutPlan:
    """Where the window goes when a panel opens. ``open_panels``: every
    panel open once it has, the one opening being the one opened last
    (the highest ``opened_at``); the others are already in ``window``.
    ``screen``: the room the screen gives windows (availableGeometry).
    ``text_width_now``: the text area's width before the panel opens.

    A free window grows outward by the panel's width, kept on the screen:
    should it reach an edge it moves along, and should it fill the screen
    the text gives way. A maximized or full screen window keeps its
    rectangle and the text gives way. Either way the text keeps at least
    ``min_text`` (or the width it had, if it had less): failing that,
    panels fold away (the lowest ``collapse_rank`` first, then the one
    opened earliest) until it does, or until only the opening panel is
    left."""
    if not open_panels:
        return LayoutPlan(window, (), max(0, text_width_now), 0, 0)
    opening = max(open_panels, key=lambda p: p.opened_at)
    shown = [p for p in open_panels if p is not opening]
    left_before, right_before = _sides(shown)
    text_now = max(0, int(text_width_now))
    # What the window holds besides the text and the panels (a toolbar's
    # edge, a splitter's handles): it stays as it is.
    chrome = max(0, window.w - text_now - left_before - right_before)
    floor = min(max(0, int(min_text)), text_now)
    fixed = maximized or full_screen
    shown.append(opening)
    collapsed: list[str] = []
    while True:
        left, right = _sides(shown)
        if fixed:
            x, w = window.x, window.w
        else:
            w = min(max(0, screen.w), chrome + text_now + left + right)
            # The text's left edge stays where it was: the window starts
            # the panels' width to its left, if the screen lets it.
            want_x = window.x + left_before - left
            x = _clamp(want_x, screen.x, screen.x + screen.w - w)
        text = w - chrome - left - right
        folding = [p for p in shown if p is not opening]
        if text >= floor or not folding:
            break
        first = min(folding, key=lambda p: (p.collapse_rank, p.opened_at))
        shown.remove(first)
        collapsed.append(first.name)
    return LayoutPlan(window=Rect(x, window.y, w, window.h),
                      collapsed=tuple(collapsed),
                      text_width=max(0, text),
                      grew_left=window.x - x,
                      grew_right=(x + w) - (window.x + window.w))


def plan_closing(screen: Rect, window: Rect, closing: Panel,
                 grew_left: int, grew_right: int) -> Rect:
    """The window's rectangle once ``closing`` has closed: what the
    window grew for it given back (``grew_left`` and ``grew_right`` from
    its LayoutPlan), so a window that grew for a panel at the right shrinks
    from the right and the text stays where it is. Panels folded away for
    it fit back into the room this leaves. Never more than the panel's
    width comes back (it may have been dragged narrower since), and the
    window stays on ``screen``. A window that did not grow (maximized,
    full screen) keeps its rectangle and the text takes the room."""
    grew_left, grew_right = int(grew_left), int(grew_right)
    excess = grew_left + grew_right - max(0, closing.width)
    if excess > 0:
        # Give back less, first at the panel's own side.
        if closing.side == "left":
            cut = min(excess, max(0, grew_left))
            grew_left -= cut
            grew_right -= min(excess - cut, max(0, grew_right))
        else:
            cut = min(excess, max(0, grew_right))
            grew_right -= cut
            grew_left -= min(excess - cut, max(0, grew_left))
    w = max(1, window.w - grew_left - grew_right)
    w = min(w, max(1, screen.w))
    x = _clamp(window.x + grew_left, screen.x, screen.x + screen.w - w)
    return Rect(x, window.y, w, window.h)
