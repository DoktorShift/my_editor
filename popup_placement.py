# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a small panel opens next to the text it belongs to.

The link popover and the slash menu open right below the words they are
about, the way Pages and Mail place theirs, and above them when the
screen has no room below; they never stick out past the screen's sides.
The format bar over a selection sits above the words, as the iPad's and
Word's do, inside the text's view (place_above). A window asks here how
much of the screen it may take.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

GAP = 6
# Room left for a window's title bar, which its frame adds to the size
# the window asks for.
TITLE_BAR = 40


def available_area(widget: QWidget) -> Optional[QRect]:
    """The part of the screen ``widget`` is shown on (or, not shown yet,
    the main screen) that windows may use: without the menu bar, the
    Dock or the taskbar. None when there is no screen."""
    handle = widget.windowHandle()
    screen = (handle.screen() if handle is not None else None) \
        or QGuiApplication.primaryScreen()
    return screen.availableGeometry() if screen is not None else None


def fitting_size(widget: QWidget, wanted: QSize) -> QSize:
    """``wanted``, made small enough that the whole window, title bar
    included, fits the screen it opens on (its buttons stay on screen)."""
    area = available_area(widget)
    if area is None:
        return wanted
    return QSize(min(wanted.width(), area.width()),
                 min(wanted.height(), area.height() - TITLE_BAR))


def place_below(popup: QWidget, anchor: QRect, parent: QWidget) -> QPoint:
    """Move ``popup`` (a top-level window) under ``anchor`` (in
    ``parent``'s coordinates), or above it when there is no room below,
    inside the screen. Returns where it went."""
    popup.adjustSize()
    below = parent.mapToGlobal(anchor.bottomLeft()) + QPoint(0, GAP)
    above = parent.mapToGlobal(anchor.topLeft()) - QPoint(0, popup.height() + GAP)
    screen = QGuiApplication.screenAt(below) or QGuiApplication.primaryScreen()
    area = screen.availableGeometry() if screen is not None else QRect(below, popup.size())
    point = below if below.y() + popup.height() <= area.bottom() else above
    point.setX(max(area.left(), min(point.x(), area.right() - popup.width())))
    popup.move(point)
    return point


def place_above(panel: QWidget, first: QRect, last: QRect, area: QRect) -> QPoint:
    """Move ``panel`` (a child widget) centred above ``first`` (the first
    line of the words it is about), or below ``last`` (their last line)
    when there is no room above, inside ``area``; all in the coordinates of
    the panel's parent. Returns where it went."""
    panel.adjustSize()
    x = first.center().x() - panel.width() // 2
    y = first.top() - GAP - panel.height()
    if y < area.top():
        y = last.bottom() + GAP
    point = QPoint(max(area.left(), min(x, area.right() + 1 - panel.width())),
                   max(area.top(), min(y, area.bottom() + 1 - panel.height())))
    panel.move(point)
    return point
