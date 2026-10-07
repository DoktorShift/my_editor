# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a small panel opens next to the text it belongs to.

The link popover and the slash menu open right below the words they are
about, the way Pages and Mail place theirs, and above them when the
screen has no room below; they never stick out past the screen's sides.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

GAP = 6


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
