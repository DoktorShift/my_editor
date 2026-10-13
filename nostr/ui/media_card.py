# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The card an audio or video file shows as in the text, where readers
show its player: what it is, its name, and how its upload is going.

It is the picture of the file's object in the document (an asset, as a
picture is), drawn again whenever the upload moves on: progress, done,
failed. Drawing it changes nothing in the document, so it is never a step
of Undo, and finishing an upload edits nothing the person wrote.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath

from constants import (
    DARK_BORDER, DARK_FG, DARK_MENU_BG, DARK_MUTED_FG, LIGHT_BORDER, LIGHT_FG, LIGHT_MUTED_FG,
)

WIDTH = 420
HEIGHT = 58
_SCALE = 2          # drawn at twice the size, so it is sharp on a high density screen


def render(title: str, state: str, *, dark: bool) -> QImage:
    """The card: a play mark, ``title`` ("Audio · interview.mp3") and,
    under it in muted text, ``state`` ("Uploading… 42 %", or "")."""
    image = QImage(WIDTH * _SCALE, HEIGHT * _SCALE, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(_SCALE)
    image.fill(Qt.GlobalColor.transparent)
    fill = QColor(DARK_MENU_BG if dark else "#F3F4F6")
    border = QColor(DARK_BORDER if dark else LIGHT_BORDER)
    ink = QColor(DARK_FG if dark else LIGHT_FG)
    muted = QColor(DARK_MUTED_FG if dark else LIGHT_MUTED_FG)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.5, 0.5, WIDTH - 1, HEIGHT - 1), 8, 8)
    painter.fillPath(path, fill)
    painter.setPen(border)
    painter.drawPath(path)

    # The play mark, in a circle.
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(muted)
    painter.drawEllipse(QRectF(14, (HEIGHT - 30) / 2, 30, 30))
    triangle = QPainterPath()
    cx, cy = 14 + 15, HEIGHT / 2
    triangle.moveTo(cx - 4, cy - 7)
    triangle.lineTo(cx + 7, cy)
    triangle.lineTo(cx - 4, cy + 7)
    triangle.closeSubpath()
    painter.fillPath(triangle, fill)

    left = 56
    room = WIDTH - left - 14
    bold = QFont()
    bold.setBold(True)
    painter.setFont(bold)
    painter.setPen(ink)
    title_y = 22 if state else HEIGHT / 2 + QFontMetrics(bold).ascent() / 2 - 2
    painter.drawText(int(left), int(title_y),
                     QFontMetrics(bold).elidedText(title, Qt.TextElideMode.ElideMiddle, room))
    if state:
        plain = QFont()
        painter.setFont(plain)
        painter.setPen(muted)
        painter.drawText(int(left), 42, QFontMetrics(plain).elidedText(
            state, Qt.TextElideMode.ElideRight, room))
    painter.end()
    return image
