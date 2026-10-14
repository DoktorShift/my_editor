# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The window's grid: side panels whose bands line up with the editor's.

    ┌ toolbar ───────────────────────┬ panel band 1 ────────┐
    ├ document tabs ─────────────────┼ panel band 2 ────────┤
    │ the document                   │ the list             │
    └────────────────────────────────┴──────────────────────┘

Mail, Notes and Xcode draw one toolbar band across the window, split
into sections over the panes, with the panes' divider running through
it. Here the format toolbar and the document tabs belong to the editor's
pane, and each side panel draws its own section of the two bands; this
keeps them the same height, so the lines under the toolbar and under the
tabs run straight across the window.

``BandAligner`` watches the toolbar and the tab widget and, after any
change of size or visibility (larger text, the toolbar hidden, the find
bar opening between the toolbar and the tabs), tells every panel how tall
its bands are to be: band 1 as tall as the toolbar, band 2 ending on the
line under the tabs. A band given 0 takes its own height: no toolbar
shown, or something between the toolbar and the tabs, where band 2 would
otherwise stretch over the find bar.
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional, Tuple

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer
from PySide6.QtWidgets import QStackedWidget, QTabWidget, QWidget

# What a panel is told: (toolbar height, search band height).
Align = Callable[[int, int], None]


def band_heights(side: QWidget, toolbar: QWidget, tabs: QTabWidget) -> Tuple[int, int]:
    """``(band 1, band 2)`` for panels beside ``side``, from where the
    toolbar and the tabs are in it; 0 where a band has nothing to match."""
    toolbar_height = toolbar.height() if toolbar.isVisible() else 0
    if not toolbar_height:
        return 0, 0
    content = tabs.findChild(QStackedWidget)
    bar = tabs.tabBar()
    if content is None or not tabs.isVisible() or not bar.isVisible():
        return toolbar_height, 0
    tabs_top = tabs.mapTo(side, QPoint(0, 0)).y()
    if tabs_top != toolbar_height:
        # Something sits between the toolbar and the tabs (the find bar,
        # the update bar): band 2 keeps its own height.
        return toolbar_height, 0
    # The line under the tabs is the tab pane's top border, on the last
    # row before the content; band 2 ends on it with its own line.
    content_top = content.mapTo(side, QPoint(0, 0)).y()
    return toolbar_height, max(0, content_top - toolbar_height)


class BandAligner(QObject):
    """Keeps every side panel's bands level with the editor's (see the
    module). ``panels`` are the panels' ``align`` callables."""

    _WATCHED = (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show, QEvent.Type.Hide,
                QEvent.Type.LayoutRequest)

    def __init__(self, *, side: QWidget, toolbar: QWidget, tabs: QTabWidget,
                 panels: Iterable[Align], parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._side, self._toolbar, self._tabs = side, toolbar, tabs
        self._panels = list(panels)
        # One alignment for a burst of changes (a font change resizes
        # everything at once).
        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.setInterval(0)
        self._pending.timeout.connect(self.align)
        for widget in (toolbar, tabs, tabs.tabBar()):
            widget.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if event.type() in self._WATCHED:
            self._pending.start()
        return False

    def align(self) -> None:
        toolbar_height, search_height = band_heights(self._side, self._toolbar, self._tabs)
        for align in self._panels:
            align(toolbar_height, search_height)
