# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Quick Look for the Media Library: the selected file, larger.

Space opens it, as in the Finder, and Space or Escape closes it. It
follows the grid's selection, so the arrow keys page through the
library while it is open (they are passed on to the grid). It shows the
picture fitted to the window, the file's name and one quiet line: what
kind of file, how large, its dimensions and the day it was added. A
video or an audio file shows its kind and Open in Browser, where it
plays.

A view only: the library says what to show (``show_file``) and hands it
the full picture when it arrives (``set_picture``).
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import format_icons
import theme
import url_safety
from i18n import _


class _Picture(QWidget):
    """The picture, fitted and centred, or the kind of file."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.pixmap: Optional[QPixmap] = None
        self.glyph = ""
        self.setMinimumSize(QSize(280, 200))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        area = self.rect()
        if self.pixmap is not None and not self.pixmap.isNull():
            size = self.pixmap.deviceIndependentSize().toSize()
            size.scale(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
            if size.width() > self.pixmap.width() and size.height() > self.pixmap.height():
                size = self.pixmap.size()          # never larger than it is
            target = area.adjusted(0, 0, 0, 0)
            target.setSize(size)
            target.moveCenter(area.center())
            painter.drawPixmap(target, self.pixmap)
        elif self.glyph:
            side = min(96, area.width() // 3, area.height() // 2)
            rect = QRectF(area.center().x() - side / 2, area.center().y() - side / 2, side, side)
            format_icons.paint(self.glyph, painter, rect,
                               self.palette().color(QPalette.ColorRole.PlaceholderText))
        painter.end()


class QuickLook(QDialog):
    """The Quick Look window, beside the library and above it."""

    closed = Signal()

    def __init__(self, *, grid: QWidget, is_dark: bool, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Tool)
        self.setWindowTitle(_("Quick Look"))
        self.setStyleSheet(theme.dialog_stylesheet(is_dark)
                           + "QLabel#quick_look_name { font-weight: 600; }")
        self._grid = grid
        self._url = ""
        self.resize(560, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(8)
        self.picture = _Picture(self)
        layout.addWidget(self.picture, 1)
        self.name = QLabel()
        self.name.setObjectName("quick_look_name")
        self.name.setTextFormat(Qt.TextFormat.PlainText)
        self.name.setWordWrap(True)
        self.name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.name)
        self.detail = QLabel()
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.detail.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        layout.addWidget(self.detail)
        row = QHBoxLayout()
        row.addStretch(1)
        self.open_button = QPushButton(_("Open in Browser"))
        self.open_button.setAutoDefault(False)
        self.open_button.clicked.connect(self._open)
        row.addWidget(self.open_button)
        row.addStretch(1)
        layout.addLayout(row)

    def show_file(self, *, name: str, detail: str, url: str, glyph: str,
                  picture: Optional[QPixmap]) -> None:
        """Show this file: its picture when there is one yet, else the
        glyph of its kind ("" while a picture is on its way)."""
        self.setWindowTitle(name)
        self.name.setText(name)
        self.detail.setText(detail)
        self._url = url
        self.picture.pixmap = picture
        self.picture.glyph = glyph
        self.picture.update()
        # A picture is seen here; a video or a sound is played there.
        self.open_button.setVisible(glyph in ("video", "audio")
                                    and url_safety.is_safe_media_url(url))

    def set_picture(self, picture: QPixmap) -> None:
        self.picture.pixmap = picture
        self.picture.glyph = ""
        self.picture.update()

    def _open(self) -> None:
        if url_safety.is_safe_media_url(self._url):
            QDesktopServices.openUrl(QUrl(self._url))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Escape):
            self.close()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            QApplication.sendEvent(self._grid, event)
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        super().closeEvent(event)
        self.closed.emit()

    def reject(self) -> None:
        super().reject()
        self.closed.emit()
