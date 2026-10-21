# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The side panels of the main window, outside the text.

The window's middle is the text; every side panel (the folder sidebar and
the file list at the left, Article Details and the Drafts panel at the
right) is a pane of one splitter beside it. ``SidePanels`` opens and
closes them the way panel_layout.py works out: opening one grows the
window outward by the panel's width, so the text stays where it was and
as wide; closing it gives back what the window grew for it. A maximized
or full screen window keeps its size and the text gives way, never below
a readable line; should even that not fit, panels fold away by
themselves and come back when the panel that folded them closes.

People resize a panel by dragging its divider: a line of one pixel with
a grab area a few pixels wide (Qt's own handle, widened here), kept
within the panel's narrowest and widest. Each panel's width is kept in
the settings (``panel_widths``, by the panel's name) and used the next
time it opens. Dragging a divider never moves the window: the text gives
the room.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

from PySide6.QtCore import QMargins, QObject, QRect, Qt, QTimer
from PySide6.QtGui import QRegion
from PySide6.QtWidgets import QSplitter, QSplitterHandle, QWidget

import panel_layout
from panel_layout import Rect
from settings import load_settings, save_setting

log = logging.getLogger(__name__)

LEFT = "left"
RIGHT = "right"

# Settings key: each panel's width, by its name.
WIDTHS_KEY = "panel_widths"

# How wide a divider is to the pointer, in logical pixels; one of them is
# drawn. Apple's dividers are a point thin and easy to grab.
GRAB_WIDTH = 7

# How soon a dragged width is written down: once the drag rests.
_SAVE_DELAY_MS = 400


class _GripHandle(QSplitterHandle):
    """A divider drawn one pixel wide (the splitter's handle width) that
    the pointer finds over GRAB_WIDTH pixels. Qt does the same for a thin
    handle with five pixels: contents margins the splitter lays out
    around, a mask that draws only the middle, and the mouse taken over
    the whole of it. This does it with GRAB_WIDTH, in place of Qt's own
    (which would set its five again, and the two would take turns)."""

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt's name)
        drawn = max(1, self.splitter().handleWidth())
        margin = max(0, (GRAB_WIDTH - drawn) // 2)
        self.setAttribute(Qt.WidgetAttribute.WA_MouseNoMask, margin > 0)
        if self.orientation() == Qt.Orientation.Horizontal:
            margins = QMargins(margin, 0, margin, 0)
        else:
            margins = QMargins(0, margin, 0, margin)
        if self.contentsMargins() != margins:
            self.setContentsMargins(margins)
        self.setMask(QRegion(self.contentsRect()))
        QWidget.resizeEvent(self, event)


class PanelSplitter(QSplitter):
    """The window's splitter: the text and the panels beside it, with
    thin dividers that are easy to grab."""

    def createHandle(self) -> QSplitterHandle:  # noqa: N802 (Qt's name)
        return _GripHandle(self.orientation(), self)


@dataclass
class _Panel:
    name: str
    widget: QWidget
    side: str
    collapse_rank: int
    default_width: int
    # Told when the panel folds away by itself (True) or comes back
    # (False), so its menu command and toolbar button follow.
    on_fold: Optional[Callable[[bool], None]] = None
    opened_at: int = 0
    grew_left: int = 0
    grew_right: int = 0
    # The panels this one folded away when it opened.
    folded: Tuple[str, ...] = field(default_factory=tuple)


def _rect(qrect: QRect) -> Rect:
    return Rect(qrect.x(), qrect.y(), qrect.width(), qrect.height())


def _within(window: Rect, screen: Rect) -> bool:
    """Whether ``window`` lies across ``screen``'s width."""
    return window.x >= screen.x and window.x + window.w <= screen.x + screen.w


class SidePanels(QObject):
    """The panels beside the text in ``splitter``, whose text pane is
    ``document``. ``window``: the top-level window that grows.
    ``screen_room()``: the room the window's screen gives windows, a
    panel_layout.Rect. ``text_floor()``: the narrowest the text may
    become (panel_layout.min_text_width for the font and mode in use)."""

    def __init__(self, window: QWidget, splitter: QSplitter, document: QWidget, *,
                 screen_room: Callable[[], Rect], text_floor: Callable[[], int],
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._window = window
        self._splitter = splitter
        self._document = document
        self._screen_room = screen_room
        self._text_floor = text_floor
        self._panels: Dict[str, _Panel] = {}
        self._opened = 0
        self._widths: Dict[str, int] = {}
        stored = load_settings().get(WIDTHS_KEY)
        if isinstance(stored, dict):
            self._widths = {str(name): int(width) for name, width in stored.items()
                            if isinstance(width, int) and not isinstance(width, bool)
                            and width > 0}
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(_SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._save_widths)
        if splitter.indexOf(document) < 0:
            splitter.addWidget(document)
        splitter.setStretchFactor(splitter.indexOf(document), 1)
        splitter.splitterMoved.connect(self._on_divider_dragged)

    # -- panels ------------------------------------------------------------

    def register(self, name: str, widget: QWidget, *, side: str, collapse_rank: int,
                 default_width: int, on_fold: Optional[Callable[[bool], None]] = None) -> None:
        """Add a panel, hidden, at its ``side`` of the text: a left panel
        right before the text (so the first registered is outermost), a
        right one after the panels already there. ``collapse_rank``: how
        soon it folds away when the screen is full (lower first: the file
        list 0, the sidebar and the right panels 1)."""
        if name in self._panels:
            raise ValueError(f"a panel called {name!r} is there already")
        if side == LEFT:
            self._splitter.insertWidget(self._splitter.indexOf(self._document), widget)
        else:
            self._splitter.addWidget(widget)
        self._splitter.setStretchFactor(self._splitter.indexOf(widget), 0)
        self._splitter.setStretchFactor(self._splitter.indexOf(self._document), 1)
        widget.hide()
        self._panels[name] = _Panel(name, widget, side, collapse_rank, default_width, on_fold)

    def names(self) -> Tuple[str, ...]:
        return tuple(self._panels)

    def is_open(self, name: str) -> bool:
        panel = self._panels.get(name)
        return panel is not None and not panel.widget.isHidden()

    def width_of(self, name: str) -> int:
        """The width the panel opens with: the one it was dragged to last,
        else its default, within its narrowest and widest."""
        panel = self._panels[name]
        return self._clamped(panel, self._widths.get(name, panel.default_width))

    @staticmethod
    def _clamped(panel: _Panel, width: int) -> int:
        low = max(1, panel.widget.minimumWidth())
        high = max(low, panel.widget.maximumWidth())
        return max(low, min(int(width), high))

    def growth(self, name: str) -> Tuple[int, int]:
        """How far the window grew at its left and right for the panel."""
        panel = self._panels[name]
        return panel.grew_left, panel.grew_right

    def folded_by(self, name: str) -> Tuple[str, ...]:
        return self._panels[name].folded

    # -- opening and closing -------------------------------------------------

    def _fixed(self) -> bool:
        return self._window.isMaximized() or self._window.isFullScreen()

    def _room(self, panel: _Panel, width: int) -> panel_layout.Panel:
        """The panel as panel_layout sees it: its width and its divider."""
        return panel_layout.Panel(panel.name, panel.side, width + self._splitter.handleWidth(),
                                  panel.opened_at, panel.collapse_rank)

    def _shown_width(self, panel: _Panel) -> int:
        index = self._splitter.indexOf(panel.widget)
        sizes = self._splitter.sizes()
        width = sizes[index] if 0 <= index < len(sizes) else 0
        return width if width > 0 else self.width_of(panel.name)

    def open(self, name: str) -> None:
        """Show the panel, the window grown outward to hold it (see the
        module docstring)."""
        panel = self._panels[name]
        if not panel.widget.isHidden():
            return
        # Opened by hand while folded away: it is no longer to come back.
        for other in self._panels.values():
            if name in other.folded:
                other.folded = tuple(n for n in other.folded if n != name)
        self._opened += 1
        panel.opened_at = self._opened
        width = self.width_of(name)
        panel.grew_left = panel.grew_right = 0
        panel.folded = ()
        if not self._window.isVisible():
            # Opened as the window is made (the panels open when it was
            # last closed): the window's size as restored holds the panel
            # already, so the panel is said to have grown it at its side,
            # and closing it gives exactly that back.
            panel.widget.show()
            if not self._fixed():
                room = width + self._splitter.handleWidth()
                if panel.side == LEFT:
                    panel.grew_left = room
                else:
                    panel.grew_right = room
            self.apply_sizes()
            return
        shown = [p for p in self._panels.values() if not p.widget.isHidden()]
        layout_panels = [self._room(p, self._shown_width(p)) for p in shown]
        layout_panels.append(self._room(panel, width))
        window = _rect(self._window.geometry())
        screen = self._screen_room()
        # A window wider than its screen, or reaching past it (stretched
        # over two displays), is left as the person made it, as a
        # maximized one is: the text gives way.
        fixed = self._fixed() or not _within(window, screen)
        plan = panel_layout.plan_opening(
            screen, window, layout_panels, self._document.width(),
            self._text_floor(), fixed, self._window.isFullScreen())
        for folded in plan.collapsed:
            self._fold(folded, True)
        panel.folded = tuple(plan.collapsed)
        panel.widget.show()
        if not fixed:
            panel.grew_left, panel.grew_right = plan.grew_left, plan.grew_right
            if plan.window != window:
                self._window.setGeometry(plan.window.x, plan.window.y,
                                         plan.window.w, plan.window.h)
        self.apply_sizes()

    def close(self, name: str) -> None:
        """Hide the panel and give back what the window grew for it; the
        panels it folded away come back."""
        panel = self._panels[name]
        if panel.widget.isHidden():
            # Closed while folded away: it is not to come back either.
            for other in self._panels.values():
                if name in other.folded:
                    other.folded = tuple(n for n in other.folded if n != name)
            return
        width = self._shown_width(panel)
        panel.widget.hide()
        grew = (panel.grew_left, panel.grew_right)
        panel.grew_left = panel.grew_right = 0
        if self._window.isVisible() and not self._fixed() and any(grew):
            closing = self._room(panel, width)
            window = _rect(self._window.geometry())
            rect = panel_layout.plan_closing(self._screen_room(), window, closing, *grew)
            if rect != window:
                self._window.setGeometry(rect.x, rect.y, rect.w, rect.h)
        folded, panel.folded = panel.folded, ()
        for other in folded:
            self._fold(other, False)
        self.apply_sizes()

    def toggle(self, name: str) -> None:
        if self.is_open(name):
            self.close(name)
        else:
            self.open(name)

    def fold(self, name: str) -> None:
        """Fold the panel away for room the window does not have (Split
        view): hidden, the window as it is, its growth kept for when it
        comes back (unfold)."""
        if self.is_open(name):
            self._fold(name, True)
            self.apply_sizes()

    def unfold(self, name: str) -> None:
        panel = self._panels.get(name)
        if panel is not None and panel.widget.isHidden():
            self._fold(name, False)
            self.apply_sizes()

    def _fold(self, name: str, folding: bool) -> None:
        panel = self._panels.get(name)
        if panel is None:
            return
        panel.widget.setHidden(folding)
        if panel.on_fold is not None:
            panel.on_fold(folding)

    def geometry_without_panels(self, names=None) -> QRect:
        """The window's rectangle as it would be with the panels ``names``
        closed (every panel when not given): what the window is
        remembered as, so the next launch, which opens only the panels
        that stay open from one launch to the next, starts as wide as the
        text was."""
        window = _rect(self._window.geometry())
        if self._fixed():
            return QRect(window.x, window.y, window.w, window.h)
        screen = self._screen_room()
        shown = sorted((p for p in self._panels.values() if not p.widget.isHidden()
                        and (names is None or p.name in names)),
                       key=lambda p: p.opened_at, reverse=True)
        for p in shown:
            if p.grew_left or p.grew_right:
                closing = self._room(p, self._shown_width(p))
                window = panel_layout.plan_closing(screen, window, closing,
                                                   p.grew_left, p.grew_right)
        return QRect(window.x, window.y, window.w, window.h)

    def hide_all(self, names=None) -> None:
        """Hide the panels ``names`` (every panel when not given) as they
        are, nothing given back or remembered (the window is closing)."""
        for panel in self._panels.values():
            if names is None or panel.name in names:
                panel.widget.hide()

    # -- widths --------------------------------------------------------------

    def apply_sizes(self) -> None:
        """Every open panel as wide as it is meant to be, the text the
        rest; again once the window's new size has arrived."""
        self._set_sizes()
        QTimer.singleShot(0, self, self._set_sizes)

    def _set_sizes(self) -> None:
        splitter = self._splitter
        sizes = splitter.sizes()
        total = sum(sizes)
        if total <= 0:
            return
        widths = []
        panels_total = 0
        for index in range(splitter.count()):
            widget = splitter.widget(index)
            panel = next((p for p in self._panels.values() if p.widget is widget), None)
            if panel is None or widget.isHidden():
                widths.append(None if widget is self._document else 0)
                continue
            width = self.width_of(panel.name)
            panels_total += width
            widths.append(width)
        document = max(1, total - panels_total)
        sizes = [document if width is None else width for width in widths]
        splitter.setSizes(sizes)

    def _on_divider_dragged(self, _position: int, _index: int) -> None:
        sizes = self._splitter.sizes()
        for panel in self._panels.values():
            index = self._splitter.indexOf(panel.widget)
            if panel.widget.isHidden() or not 0 <= index < len(sizes) or sizes[index] <= 0:
                continue
            self._widths[panel.name] = self._clamped(panel, sizes[index])
        self._save_timer.start()

    def _save_widths(self) -> None:
        save_setting(WIDTHS_KEY, dict(self._widths))

    def flush(self) -> None:
        """Write the widths down now (as the window closes)."""
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_widths()
