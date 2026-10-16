# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""How big text reads in MyEditor: one choice for the whole app.

macOS has no system-wide text size for apps to follow ("macOS doesn't
support Dynamic Type", Apple's guidelines on typography), so the app
offers its own: a size beside the font in Settings, and View > Zoom In,
Zoom Out and Actual Size. It sizes what is read and written, the
documents' text and every preview of it, and not the toolbar, tabs or
panels: "when people increase text size to read the content in a tabbed
window, they don't expect the tab titles to increase in size".

The size is the writing font's, in points, as a Mac's fonts are measured
and named (TextEdit's and Mail's settings: "Helvetica 12"). The sizes
offered are the usual ones from 8 to 32 pt, less any drawn smaller than
10 px: the guidelines' smallest size for a Mac is 10 pt, and there a pixel
is a point, so a Mac offers 10 pt and up, a screen of 96 dpi 8 pt and up.
Actual Size is the writing font's own size (16 px: 16 pt on a Mac, 12 pt
at 96 dpi); everything else (code, headings, previews) is sized by the
same factor, and nothing is made smaller than 10 px by it: at 10 pt code
is as big as the writing, not smaller. Like the language
(i18n), it is the app's: anything that shows text asks :func:`text_size`
and follows ``changed``.
"""

from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication

from fonts import WRITING_PIXEL_SIZE

# The usual sizes, in points, as a Mac's font sizes are offered.
SIZES = (8, 9, 10, 11, 12, 13, 14, 16, 18, 20, 22, 24, 28, 32)
# The guidelines' smallest size for a Mac, 10 pt, which is 10 px there.
SMALLEST_PIXELS = 10


def _screen_dpi() -> float:
    """The points-to-pixels measure of the screen: 72 on a Mac, 96 (or what
    the display is set to) elsewhere."""
    screen = QGuiApplication.primaryScreen() if QGuiApplication.instance() else None
    return screen.logicalDotsPerInchY() if screen is not None else 72.0


class TextSize(QObject):
    """The app's text size (see the module). ``changed(points)``."""

    changed = Signal(int)

    def __init__(self, points: Optional[float] = None, *, dpi: Optional[float] = None,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._dpi = dpi or _screen_dpi()
        self._actual = max(1, round(WRITING_PIXEL_SIZE * 72 / self._dpi))
        self._sizes = tuple(sorted({size for size in SIZES
                                    if size * self._dpi / 72 >= SMALLEST_PIXELS}
                                   | {self._actual}))
        self._points = self._nearest(points if points is not None else self._actual)

    def _nearest(self, points: float) -> int:
        return min(self._sizes, key=lambda size: (abs(size - points), size))

    def sizes(self) -> Tuple[int, ...]:
        """The sizes offered, in points, smallest first."""
        return self._sizes

    def actual(self) -> int:
        """Actual Size: the writing font's own size, in points."""
        return self._actual

    def points(self) -> int:
        return self._points

    def scale(self) -> float:
        """The factor the documents' own sizes are multiplied by."""
        return self._points / self._actual

    def pixels(self, size: int) -> int:
        """``size`` pixels at this text size; never made smaller than the
        guidelines' least (a size already smaller stays as it is)."""
        return max(min(size, SMALLEST_PIXELS), round(size * self.scale()))

    def set_points(self, points: float) -> None:
        """The nearest size offered to ``points``."""
        size = self._nearest(points)
        if size != self._points:
            self._points = size
            self.changed.emit(size)

    def can_grow(self) -> bool:
        return self._points < self._sizes[-1]

    def can_shrink(self) -> bool:
        return self._points > self._sizes[0]

    def grow(self) -> None:
        """Zoom In: the next size up."""
        if self.can_grow():
            self.set_points(self._sizes[self._sizes.index(self._points) + 1])

    def shrink(self) -> None:
        """Zoom Out: the next size down."""
        if self.can_shrink():
            self.set_points(self._sizes[self._sizes.index(self._points) - 1])

    def reset(self) -> None:
        """Actual Size."""
        self.set_points(self._actual)


def points_from_percent(percent, size: TextSize) -> int:
    """A text size kept in percent of the writing font's own size, as it
    was before sizes were points: the nearest size offered."""
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        return size.actual()
    return size._nearest(size.actual() * percent / 100)


_text_size: Optional[TextSize] = None


def text_size() -> TextSize:
    """The app's text size."""
    global _text_size
    if _text_size is None:
        _text_size = TextSize()
    return _text_size
