# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A layout that wraps its widgets into rows.

A row of buttons in a narrow pane must not make the pane wider: German
words, the wide fonts of Windows and Linux, and text at 200 percent all
grow the buttons. Qt has no layout that moves what does not fit to a new
row, so this one does: its narrowest width is its widest widget's, never
the sum of them, and it asks for the height its rows need at the width
it gets (height for width).

Rows start at the leading edge, or with ``trailing=True`` end at the
trailing edge, where a row of buttons keeps its default button (Apple's
guidelines for button rows). Hidden widgets take no room.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidget


class FlowLayout(QLayout):
    """Widgets in rows that wrap (see the module docstring)."""

    def __init__(self, parent: Optional[QWidget] = None, *, spacing: int = 8,
                 trailing: bool = False) -> None:
        super().__init__(parent)
        self._items: List[QLayoutItem] = []
        self._gap = spacing
        self._trailing = trailing
        self.setContentsMargins(0, 0, 0, 0)

    # -- QLayout ---------------------------------------------------------------

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:
        """Everything in one row."""
        shown = self._shown()
        margins = self.contentsMargins()
        width = sum(item.sizeHint().width() for item in shown) + self._gap * max(
            0, len(shown) - 1)
        height = max((item.sizeHint().height() for item in shown), default=0)
        return QSize(width + margins.left() + margins.right(),
                     height + margins.top() + margins.bottom())

    def minimumSize(self) -> QSize:
        """As narrow as the widest widget: the others wrap."""
        shown = self._shown()
        margins = self.contentsMargins()
        width = max((item.minimumSize().width() for item in shown), default=0)
        height = max((item.minimumSize().height() for item in shown), default=0)
        return QSize(width + margins.left() + margins.right(),
                     height + margins.top() + margins.bottom())

    # -- laying out --------------------------------------------------------------

    def _shown(self) -> List[QLayoutItem]:
        return [item for item in self._items if not item.isEmpty()]

    def rows(self, width: int) -> List[List[QLayoutItem]]:
        """The items of each row at ``width`` (for tests and for callers
        that want to know whether the row wrapped)."""
        margins = self.contentsMargins()
        room = width - margins.left() - margins.right()
        rows: List[List[QLayoutItem]] = []
        row: List[QLayoutItem] = []
        used = 0
        for item in self._shown():
            wanted = item.sizeHint().width()
            if row and used + self._gap + wanted > room:
                rows.append(row)
                row, used = [], 0
            used = wanted if not row else used + self._gap + wanted
            row.append(item)
        if row:
            rows.append(row)
        return rows

    def _arrange(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        y = area.y()
        rows = self.rows(rect.width())
        for row in rows:
            height = max(item.sizeHint().height() for item in row)
            widths = [min(item.sizeHint().width(), area.width()) for item in row]
            used = sum(widths) + self._gap * (len(row) - 1)
            x = area.x() + (area.width() - used if self._trailing else 0)
            for item, width in zip(row, widths):
                if apply:
                    size = item.sizeHint()
                    item.setGeometry(QRect(QPoint(x, y + (height - size.height()) // 2),
                                           QSize(width, size.height())))
                x += width + self._gap
            y += height + self._gap
        if rows:
            y -= self._gap
        return y - rect.y() + margins.bottom()
