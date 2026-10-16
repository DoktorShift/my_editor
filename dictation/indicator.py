# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The indicator shown while dictating on this computer.

As a Mac shows its dictation: a small panel at the insertion point with a
microphone and a meter that follows the voice, "Getting ready…" until the
model listens, then "Listening", and Done. It follows the insertion point
as phrases are written, inside the text's view (a child of it, as the
format bar is: drawn and moved the same on every platform). Done, or
Escape in the text, stops dictating (``done``). It never takes the
keyboard focus: the person can type, and dictate again, at any time.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

import format_icons
from announce import announce
from constants import DARK_BORDER, DARK_FG, DARK_MENU_BG, LIGHT_BORDER, LIGHT_FG, LIGHT_MENU_BG
from i18n import _
from popup_placement import place_above

_BARS = 5


class _Meter(QWidget):
    """Bars that rise with the voice, as a Mac's input level does."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedSize(QSize(26, 16))
        self.lit = 0
        self.ink = QColor(LIGHT_FG)

    def set_level(self, level: float) -> None:
        # Speech is quiet as numbers go: a voice at a tenth of full scale
        # fills most of the meter.
        lit = min(_BARS, round(min(1.0, level * 6) * _BARS))
        if lit != self.lit:
            self.lit = lit
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802, Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        width = (self.width() - (_BARS - 1) * 2) / _BARS
        for bar in range(_BARS):
            height = 4 + bar * (self.height() - 4) / (_BARS - 1)
            ink = QColor(self.ink)
            ink.setAlphaF(1.0 if bar < self.lit else 0.25)
            painter.setBrush(ink)
            painter.drawRoundedRect(QRectF(bar * (width + 2), self.height() - height,
                                           width, height), 1.5, 1.5)


class DictationIndicator(QFrame):
    """The indicator over ``editor``'s insertion point. ``done`` when the
    person stops dictating with it."""

    done = Signal()

    def __init__(self, editor, *, dark: bool = False) -> None:
        super().__init__(editor.viewport())
        self._editor = editor
        self.setObjectName("DictationIndicator")
        self.setAccessibleName(_("Dictation"))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.hide()

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 4, 4)
        row.setSpacing(6)
        self._icon = QLabel(self)
        self._meter = _Meter(self)
        self._state = QLabel(self)
        self.done_button = QPushButton(_("Done"), self)
        self.done_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.done_button.setAutoDefault(False)
        self.done_button.clicked.connect(self.done)
        for widget in (self._icon, self._meter, self._state):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            row.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.done_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.set_dark(dark)
        self.set_listening(False)

        editor.cursorPositionChanged.connect(self._place)
        editor.visible_area_changed.connect(self._place)
        editor.installEventFilter(self)

    # -- what it shows --------------------------------------------------------------

    def open(self) -> None:
        self.show()
        self.raise_()
        self._place()
        announce(self, _("Dictation started"))

    def close_quietly(self) -> None:
        """Hide it, dictation over (the window has stopped it)."""
        if self.isVisible():
            self.hide()
            announce(self._editor, _("Dictation stopped"))

    def set_listening(self, listening: bool) -> None:
        self._state.setText(_("Listening") if listening else _("Getting ready…"))
        self.setAccessibleDescription(self._state.text())
        self._place()

    def set_level(self, level: float) -> None:
        self._meter.set_level(level)

    def set_dark(self, dark: bool) -> None:
        ink, ground, edge = ((DARK_FG, DARK_MENU_BG, DARK_BORDER) if dark
                             else (LIGHT_FG, LIGHT_MENU_BG, LIGHT_BORDER))
        self._meter.ink = QColor(ink)
        self._icon.setPixmap(format_icons.microphone(QColor(ink)).pixmap(
            format_icons.SIZE, format_icons.SIZE))
        self.setStyleSheet(f"#DictationIndicator {{ background: {ground}; color: {ink};"
                           f" border: 1px solid {edge}; border-radius: 8px; }}"
                           f" #DictationIndicator QLabel {{ color: {ink}; }}")

    def state_text(self) -> str:
        return self._state.text()

    def lit_bars(self) -> int:
        return self._meter.lit

    # -- where it goes --------------------------------------------------------------

    def _place(self) -> None:
        if self.isVisible():
            caret = self._editor.cursorRect()
            place_above(self, caret, caret, self._editor.viewport().rect())

    def eventFilter(self, watched, event) -> bool:  # noqa: N802, Qt's name
        # Escape in the text stops dictating while the indicator is up.
        if (watched is self._editor and self.isVisible()
                and event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Escape):
            self.done.emit()
            return True
        return False
