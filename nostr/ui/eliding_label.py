# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A one-line label that elides its text to the room it gets.

A long title (a source called "Verbraucherzentrale Nordrhein-Westfalen
Pressemitteilungen", a long display name) must neither widen what holds
it nor be cut without a sign: QLabel does both, clipping without an
ellipsis once it is squeezed and asking for the whole text's width.

:class:`ElidingLabel` asks for no width of its own, paints its text
elided at the width it got (at paint time, so the elision can never
change the width it was computed for), and keeps the whole text in its
accessible name and its tooltip. ``setText`` and ``text`` work as on a
QLabel, with the whole text.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class ElidingLabel(QLabel):
    """One line of text, elided to fit (see the module docstring)."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None, *,
                 mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
                 tooltip: bool = True) -> None:
        super().__init__(parent)
        self._full = ""
        self._mode = mode
        self._tooltip = tooltip
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    # QLabel's own text stays empty: its size hint must not grow with it.
    def setText(self, text: str) -> None:  # noqa: N802 (Qt's name)
        self._full = text or ""
        self.setAccessibleName(self._full)
        if self._tooltip:
            self.setToolTip(self._full if self.painted_text() != self._full else "")
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return self._full

    def full_text(self) -> str:
        return self._full

    def set_full_text(self, text: str) -> None:
        self.setText(text)

    def painted_text(self) -> str:
        """What is painted at the current width."""
        width = self.contentsRect().width()
        if width <= 0:
            return self._full
        return self.fontMetrics().elidedText(self._full, self._mode, width)

    def sizeHint(self) -> QSize:
        margins = self.contentsMargins()
        return QSize(self.fontMetrics().horizontalAdvance(self._full)
                     + margins.left() + margins.right(),
                     self.fontMetrics().height() + margins.top() + margins.bottom())

    def minimumSizeHint(self) -> QSize:
        hint = self.sizeHint()
        hint.setWidth(0)
        return hint

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._tooltip:
            self.setToolTip(self._full if self.painted_text() != self._full else "")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        self.style().drawItemText(painter, self.contentsRect(), int(self.alignment()),
                                  self.palette(), self.isEnabled(), self.painted_text(),
                                  self.foregroundRole())
