# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""How big text reads in MyEditor: one choice for the whole app.

macOS has no system-wide text size for apps to follow ("macOS doesn't
support Dynamic Type", Apple's guidelines on typography), so the app
offers its own: View > Zoom In, Zoom Out and Actual Size, and a slider in
Settings. It sizes what is read and written, the documents' text and
every preview of it, and not the toolbar, tabs or panels: "when people
increase text size to read the content in a tabbed window, they don't
expect the tab titles to increase in size".

The sizes are steps of the documents' own sizes, from 80 % to 200 %; at
80 % writing (16 px) and code (14 px) stay above the guidelines' smallest
size for a Mac (10 pt). Like the language (i18n), it is the app's, so
anything that shows text asks :func:`text_size` and follows ``changed``.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal

# The sizes offered, in percent of the documents' own.
STEPS = (80, 90, 100, 110, 125, 150, 175, 200)
ACTUAL = 100


def _nearest(percent: float) -> int:
    return min(STEPS, key=lambda step: (abs(step - percent), step))


class TextSize(QObject):
    """The app's text size (see the module). ``changed(percent)``."""

    changed = Signal(int)

    def __init__(self, percent: int = ACTUAL, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._percent = _nearest(percent)

    def percent(self) -> int:
        return self._percent

    def scale(self) -> float:
        """The factor sizes are multiplied by."""
        return self._percent / ACTUAL

    def pixels(self, size: int) -> int:
        """``size`` pixels at this text size."""
        return max(1, round(size * self.scale()))

    def set_percent(self, percent: float) -> None:
        """The nearest step to ``percent``."""
        step = _nearest(percent)
        if step != self._percent:
            self._percent = step
            self.changed.emit(step)

    def can_grow(self) -> bool:
        return self._percent < STEPS[-1]

    def can_shrink(self) -> bool:
        return self._percent > STEPS[0]

    def grow(self) -> None:
        """Zoom In: the next step up."""
        if self.can_grow():
            self.set_percent(STEPS[STEPS.index(self._percent) + 1])

    def shrink(self) -> None:
        """Zoom Out: the next step down."""
        if self.can_shrink():
            self.set_percent(STEPS[STEPS.index(self._percent) - 1])

    def reset(self) -> None:
        """Actual Size."""
        self.set_percent(ACTUAL)


_text_size: Optional[TextSize] = None


def text_size() -> TextSize:
    """The app's text size."""
    global _text_size
    if _text_size is None:
        _text_size = TextSize()
    return _text_size
