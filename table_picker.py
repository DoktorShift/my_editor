# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Insert > Table...: how many columns and rows, chosen on a grid.

A small panel at the caret with a grid of eight by eight cells, the way
Word and Google Docs ask for a table's size: the cells from the top left
to the one under the pointer are the table, and a click inserts it. The
keyboard does the same: the arrow keys make the choice wider, narrower,
taller or shorter, Return inserts it, Escape closes the panel and keeps
the text as it was. The size chosen is written under the grid ("3 x 2":
columns, then rows, as Word and Google Docs write it) and screen readers
hear it ("3 columns, 2 rows").

The first row is the table's header (Markdown tables have one), so the
smallest table has one row and one column, and the choice starts at a
header and two rows of three columns.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

import announce
import theme
from constants import (
    DARK_BORDER, DARK_FG, DARK_MENU_BG, DARK_MUTED_FG, DARK_SELECTION, LIGHT_BORDER, LIGHT_FG,
    LIGHT_MENU_BG, LIGHT_MUTED_FG, LIGHT_SELECTION,
)
from i18n import _, ngettext
from popup_placement import place_below

GRID = 8                 # columns and rows offered
CELL = 20                # one cell of the grid, in pixels
GAP = 3                  # between cells
START = (3, 3)           # the first choice: three rows (a header and two), three columns


def size_words(rows: int, columns: int) -> str:
    """The size as a screen reader says it: "3 columns, 2 rows"."""
    return _("{columns}, {rows}").format(
        columns=ngettext("{count} column", "{count} columns", columns).format(count=columns),
        rows=ngettext("{count} row", "{count} rows", rows).format(count=rows))


class _Grid(QWidget):
    """The cells; the chosen ones in the selection color."""

    def __init__(self, picker: "TablePicker") -> None:
        super().__init__(picker)
        self._picker = picker
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(_("Table Size"))
        side = GRID * CELL + (GRID - 1) * GAP
        self.setFixedSize(QSize(side, side))

    def cell_at(self, point: QPoint) -> Optional[tuple]:
        """(row, column) of the cell under ``point`` (from 1), or None."""
        column = point.x() // (CELL + GAP) + 1
        row = point.y() // (CELL + GAP) + 1
        if 1 <= row <= GRID and 1 <= column <= GRID:
            return row, column
        return None

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt's name)
        colors = self._picker.colors()
        rows, columns = self._picker.choice()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for row in range(GRID):
            for column in range(GRID):
                rect = QRect(column * (CELL + GAP), row * (CELL + GAP), CELL, CELL)
                chosen = row < rows and column < columns
                painter.setPen(QColor(colors["selection"] if chosen else colors["border"]))
                painter.setBrush(QColor(colors["selection"]) if chosen
                                 else QColor(colors["background"]))
                painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 3, 3)
        painter.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        found = self.cell_at(event.position().toPoint())
        if found is not None:
            self._picker.choose(*found)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        found = self.cell_at(event.position().toPoint())
        if found is not None and event.button() == Qt.MouseButton.LeftButton:
            self._picker.choose(*found)
            self._picker.pick()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        rows, columns = self._picker.choice()
        key = event.key()
        if key == Qt.Key.Key_Right:
            self._picker.choose(rows, columns + 1)
        elif key == Qt.Key.Key_Left:
            self._picker.choose(rows, columns - 1)
        elif key == Qt.Key.Key_Down:
            self._picker.choose(rows + 1, columns)
        elif key == Qt.Key.Key_Up:
            self._picker.choose(rows - 1, columns)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._picker.pick()
        elif key == Qt.Key.Key_Escape:
            self._picker.close()
        else:
            super().keyPressEvent(event)


class TablePicker(QFrame):
    """The panel. ``picked(rows, columns)`` when a size is chosen; it
    closes either way, and the focus goes back where it was."""

    picked = Signal(int, int)

    def __init__(self, parent: Optional[QWidget] = None, *, dark: bool = False) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("TablePicker")
        self.setAccessibleName(_("Insert Table"))
        self._dark = dark
        self._rows, self._columns = START
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)
        self.grid = _Grid(self)
        layout.addWidget(self.grid)
        self.size_label = QLabel()
        self.size_label.setObjectName("TableSize")
        self.size_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.size_label.setBuddy(self.grid)
        layout.addWidget(self.size_label)
        self._apply_colors()
        self._show_choice()

    # -- the choice ----------------------------------------------------------------

    def choice(self) -> tuple:
        """(rows, columns) chosen."""
        return self._rows, self._columns

    def choose(self, rows: int, columns: int) -> None:
        rows = max(1, min(GRID, rows))
        columns = max(1, min(GRID, columns))
        if (rows, columns) == (self._rows, self._columns):
            return
        self._rows, self._columns = rows, columns
        self._show_choice()
        announce.announce(self.grid, size_words(rows, columns))

    def pick(self) -> None:
        rows, columns = self._rows, self._columns
        self.close()
        self.picked.emit(rows, columns)

    def _show_choice(self) -> None:
        self.size_label.setText(f"{self._columns} × {self._rows}")
        self.grid.setAccessibleDescription(size_words(self._rows, self._columns))
        self.grid.update()

    # -- how it looks and where it opens ----------------------------------------------

    def colors(self) -> dict:
        if self._dark:
            return {"background": DARK_MENU_BG, "border": DARK_BORDER, "text": DARK_FG,
                    "muted": DARK_MUTED_FG, "selection": DARK_SELECTION}
        return {"background": LIGHT_MENU_BG, "border": LIGHT_BORDER, "text": LIGHT_FG,
                "muted": LIGHT_MUTED_FG, "selection": LIGHT_SELECTION}

    def _apply_colors(self) -> None:
        colors = self.colors()
        self.setStyleSheet(
            theme.dialog_stylesheet(self._dark)
            + f"#TablePicker {{ background: {colors['background']};"
              f" border: 1px solid {colors['border']}; border-radius: 8px; }}"
            + f"#TableSize {{ color: {colors['muted']}; }}")

    def open_at(self, caret: QRect, parent: QWidget) -> None:
        """Under the caret (in ``parent``'s coordinates), the grid with the
        keyboard focus."""
        place_below(self, caret, parent)
        self.show()
        self.grid.setFocus()
        announce.announce(self.grid, _("Insert Table. {size}").format(
            size=size_words(self._rows, self._columns)))
