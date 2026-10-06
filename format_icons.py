# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The format toolbar's icons, drawn here in one color.

Drawn rather than shipped as files, so they take the theme's text color
(light and dark) and stay sharp at any screen scale, and because the
system symbol fonts are not ours to ship (SF Symbols may only be used on
Apple platforms). A letter icon (B, I, S) follows the language: the
letters come from the translation, as in Pages ("F", "K" in German).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

SIZE = 18
_SCALES = (1, 2, 3)


def _icon(draw, color: QColor) -> QIcon:
    """An icon from ``draw(painter, color)`` on an 18 by 18 square, made
    for every common screen scale."""
    icon = QIcon()
    for scale in _SCALES:
        pixmap = QPixmap(SIZE * scale, SIZE * scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw(painter, QColor(color))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def letter(text: str, color: QColor, *, bold: bool = False, italic: bool = False,
           strike: bool = False) -> QIcon:
    """A letter in the style it stands for."""
    def draw(painter: QPainter, ink: QColor) -> None:
        font = QFont()
        font.setPixelSize(15)
        font.setBold(bold)
        font.setItalic(italic)
        font.setStrikeOut(strike)
        painter.setFont(font)
        painter.setPen(ink)
        painter.drawText(QRectF(0, 0, SIZE, SIZE), Qt.AlignmentFlag.AlignCenter, text)
    return _icon(draw, color)


def _pen(ink: QColor, width: float = 1.6) -> QPen:
    pen = QPen(ink, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def bulleted_list(color: QColor) -> QIcon:
    def draw(painter: QPainter, ink: QColor) -> None:
        for y in (4.5, 9.0, 13.5):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ink)
            painter.drawEllipse(QPointF(3.5, y), 1.6, 1.6)
            painter.setPen(_pen(ink))
            painter.drawLine(QPointF(7.5, y), QPointF(16, y))
    return _icon(draw, color)


def numbered_list(color: QColor) -> QIcon:
    def draw(painter: QPainter, ink: QColor) -> None:
        font = QFont()
        font.setPixelSize(6)
        font.setBold(True)
        painter.setFont(font)
        for number, y in ((1, 4.5), (2, 9.0), (3, 13.5)):
            painter.setPen(ink)
            painter.drawText(QRectF(0.5, y - 4, 6, 8), Qt.AlignmentFlag.AlignCenter,
                             str(number))
            painter.setPen(_pen(ink))
            painter.drawLine(QPointF(7.5, y), QPointF(16, y))
    return _icon(draw, color)


def quote(color: QColor) -> QIcon:
    def draw(painter: QPainter, ink: QColor) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        painter.drawRoundedRect(QRectF(2, 2.5, 2.2, 13), 1, 1)
        painter.setPen(_pen(ink))
        for y in (5.0, 9.0, 13.0):
            painter.drawLine(QPointF(7, y), QPointF(16 if y < 13 else 12.5, y))
    return _icon(draw, color)


def link(color: QColor) -> QIcon:
    def draw(painter: QPainter, ink: QColor) -> None:
        painter.setPen(_pen(ink, 1.7))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Two links of a chain, at an angle.
        painter.translate(SIZE / 2, SIZE / 2)
        painter.rotate(-45)
        for x in (-4.2, 4.2):
            path = QPainterPath()
            path.addRoundedRect(QRectF(x - 4.6, -2.6, 9.2, 5.2), 2.6, 2.6)
            painter.drawPath(path)
    return _icon(draw, color)
