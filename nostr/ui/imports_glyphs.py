# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Small line glyphs for the Imports window, drawn here.

Apple's symbols may only be used on Apple's platforms, so the sidebar's
and the toolbar's glyphs are drawn with QPainter: thin strokes in the
color of the text beside them, at any size and pixel density, the same on
Windows, macOS and Linux.

- ``inbox``   a tray (the Inbox)
- ``clock``   a clock face (Older Posts)
- ``check``   a check mark (Imported)
- ``skip``    a circle with a slash (Skipped)
- ``file``    a page with a folded corner (a file being imported)
- ``sidebar`` a window with its left pane marked (Show or Hide Sidebar)

:func:`letter_avatar` draws the round initial that stands for a source
until its site icon is known.
"""

from __future__ import annotations

import hashlib

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap

from .avatar import initials_for


def is_dark(palette: QPalette) -> bool:
    """Whether ``palette`` is the dark theme's."""
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def glyph(name: str, size: int, color: QColor, dpr: float = 1.0) -> QPixmap:
    """The glyph ``name`` as a ``size`` point square pixmap."""
    pixmap = QPixmap(int(size * dpr), int(size * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, max(1.2, size / 13.0))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    _DRAW[name](painter, float(size))
    painter.end()
    return pixmap


def glyph_icon(name: str, size: int, color: QColor, dpr: float = 2.0) -> QIcon:
    return QIcon(glyph(name, size, color, dpr))


def _inbox(p: QPainter, s: float) -> None:
    m = s * 0.14
    tray = QPainterPath()
    tray.moveTo(m, s * 0.55)
    tray.lineTo(s * 0.32, s * 0.55)
    tray.lineTo(s * 0.38, s * 0.66)
    tray.lineTo(s * 0.62, s * 0.66)
    tray.lineTo(s * 0.68, s * 0.55)
    tray.lineTo(s - m, s * 0.55)
    p.drawPath(tray)
    outline = QPainterPath()
    outline.moveTo(s * 0.24, s * 0.22)
    outline.lineTo(s * 0.76, s * 0.22)
    outline.lineTo(s - m, s * 0.55)
    outline.lineTo(s - m, s * 0.80)
    outline.lineTo(m, s * 0.80)
    outline.lineTo(m, s * 0.55)
    outline.closeSubpath()
    p.drawPath(outline)


def _clock(p: QPainter, s: float) -> None:
    p.drawEllipse(QRectF(s * 0.14, s * 0.14, s * 0.72, s * 0.72))
    p.drawLine(QPointF(s * 0.5, s * 0.5), QPointF(s * 0.5, s * 0.28))
    p.drawLine(QPointF(s * 0.5, s * 0.5), QPointF(s * 0.66, s * 0.6))


def _check(p: QPainter, s: float) -> None:
    path = QPainterPath()
    path.moveTo(s * 0.2, s * 0.52)
    path.lineTo(s * 0.42, s * 0.74)
    path.lineTo(s * 0.8, s * 0.28)
    p.drawPath(path)


def _skip(p: QPainter, s: float) -> None:
    p.drawEllipse(QRectF(s * 0.16, s * 0.16, s * 0.68, s * 0.68))
    p.drawLine(QPointF(s * 0.27, s * 0.27), QPointF(s * 0.73, s * 0.73))


def _file(p: QPainter, s: float) -> None:
    path = QPainterPath()
    path.moveTo(s * 0.24, s * 0.12)
    path.lineTo(s * 0.58, s * 0.12)
    path.lineTo(s * 0.76, s * 0.30)
    path.lineTo(s * 0.76, s * 0.88)
    path.lineTo(s * 0.24, s * 0.88)
    path.closeSubpath()
    p.drawPath(path)
    corner = QPainterPath()
    corner.moveTo(s * 0.58, s * 0.12)
    corner.lineTo(s * 0.58, s * 0.30)
    corner.lineTo(s * 0.76, s * 0.30)
    p.drawPath(corner)


def _sidebar(p: QPainter, s: float) -> None:
    frame = QRectF(s * 0.1, s * 0.2, s * 0.8, s * 0.6)
    p.drawRoundedRect(frame, s * 0.1, s * 0.1)
    p.drawLine(QPointF(s * 0.38, s * 0.2), QPointF(s * 0.38, s * 0.8))
    for y in (0.34, 0.46, 0.58):
        p.drawLine(QPointF(s * 0.17, s * y), QPointF(s * 0.29, s * y))


_DRAW = {"inbox": _inbox, "clock": _clock, "check": _check, "skip": _skip,
         "file": _file, "sidebar": _sidebar}


def hue_for(text: str) -> int:
    """A hue picked by ``text`` (a source's address), the same every time."""
    digest = hashlib.sha256((text or "").encode("utf-8")).digest()
    return int.from_bytes(digest[:2], "big") % 360


def letter_avatar(title: str, key: str, size: int, *, dark: bool = False,
                  dpr: float = 1.0) -> QPixmap:
    """A rounded square with the first letter of ``title`` in a calm tint
    picked by ``key``: a source until its site icon is known. The letter
    keeps a readable contrast with its square in both themes."""
    pixmap = QPixmap(int(size * dpr), int(size * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    hue = hue_for(key)
    square = QColor.fromHsv(hue, 110, 70) if dark else QColor.fromHsv(hue, 40, 248)
    letter_color = QColor.fromHsv(hue, 60, 250) if dark else QColor.fromHsv(hue, 210, 130)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(square)
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.24, size * 0.24)
    painter.setPen(letter_color)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(max(7, int(size * 0.56)))
    painter.setFont(font)
    letter = initials_for(title)[:1] or "?"
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, letter)
    painter.end()
    return pixmap
