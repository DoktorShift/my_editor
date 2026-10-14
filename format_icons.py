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

# How strongly a command that cannot act now is drawn: its ink at this
# opacity, light and dark alike (the style's own dimming barely shows on
# a dark toolbar).
DISABLED_OPACITY = 0.32


def dimmed(color: QColor) -> QColor:
    """``color`` as a command that cannot act now shows it."""
    ink = QColor(color)
    ink.setAlphaF(ink.alphaF() * DISABLED_OPACITY)
    return ink


def _icon(draw, color: QColor) -> QIcon:
    """An icon from ``draw(painter, color)`` on an 18 by 18 square, made
    for every common screen scale, with its dimmed form for a command
    that cannot act now."""
    icon = QIcon()
    for mode, ink in ((QIcon.Mode.Normal, QColor(color)), (QIcon.Mode.Disabled, dimmed(color))):
        for scale in _SCALES:
            pixmap = QPixmap(SIZE * scale, SIZE * scale)
            pixmap.setDevicePixelRatio(scale)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            draw(painter, QColor(ink))
            painter.end()
            icon.addPixmap(pixmap, mode)
    return icon


def letter(text: str, color: QColor, *, bold: bool = False, italic: bool = False,
           strike: bool = False) -> QIcon:
    """A letter in the style it stands for (two letters, such as "H1",
    a little smaller, so they fit)."""
    def draw(painter: QPainter, ink: QColor) -> None:
        font = QFont()
        font.setPixelSize(15 if len(text) < 2 else 11)
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


def divider(color: QColor) -> QIcon:
    """A rule across, between two faint lines of text."""
    def draw(painter: QPainter, ink: QColor) -> None:
        painter.setPen(_pen(ink, 1.8))
        painter.drawLine(QPointF(2, 9), QPointF(16, 9))
        faint = QColor(ink)
        faint.setAlphaF(ink.alphaF() * 0.45)
        painter.setPen(_pen(faint, 1.2))
        painter.drawLine(QPointF(4, 4), QPointF(14, 4))
        painter.drawLine(QPointF(4, 14), QPointF(14, 14))
    return _icon(draw, color)


def checklist(color: QColor) -> QIcon:
    """Boxes, one ticked, each with a line beside it."""
    def draw(painter: QPainter, ink: QColor) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for y, ticked in ((5.0, True), (13.0, False)):
            painter.setPen(_pen(ink, 1.3))
            painter.drawRoundedRect(QRectF(1.5, y - 3, 6, 6), 1.2, 1.2)
            if ticked:
                painter.setPen(_pen(ink, 1.4))
                path = QPainterPath()
                path.moveTo(QPointF(2.8, y))
                path.lineTo(QPointF(4.2, y + 1.5))
                path.lineTo(QPointF(6.4, y - 1.6))
                painter.drawPath(path)
            painter.setPen(_pen(ink))
            painter.drawLine(QPointF(10, y), QPointF(16.5, y))
    return _icon(draw, color)


def table(color: QColor) -> QIcon:
    """A grid of three columns and three rows, the header row filled."""
    def draw(painter: QPainter, ink: QColor) -> None:
        frame = QRectF(1.5, 2.5, 15, 13)
        painter.setPen(Qt.PenStyle.NoPen)
        faint = QColor(ink)
        faint.setAlphaF(ink.alphaF() * 0.35)
        painter.setBrush(faint)
        painter.drawRect(QRectF(frame.left(), frame.top(), frame.width(), 4.3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(_pen(ink, 1.3))
        painter.drawRoundedRect(frame, 1.5, 1.5)
        for x in (6.5, 11.5):
            painter.drawLine(QPointF(x, frame.top()), QPointF(x, frame.bottom()))
        for y in (6.8, 11.2):
            painter.drawLine(QPointF(frame.left(), y), QPointF(frame.right(), y))
    return _icon(draw, color)


def code_block(color: QColor) -> QIcon:
    """Angle brackets around a slash: code."""
    def draw(painter: QPainter, ink: QColor) -> None:
        painter.setPen(_pen(ink, 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        left = QPainterPath()
        left.moveTo(QPointF(6, 4.5))
        left.lineTo(QPointF(1.8, 9))
        left.lineTo(QPointF(6, 13.5))
        painter.drawPath(left)
        right = QPainterPath()
        right.moveTo(QPointF(12, 4.5))
        right.lineTo(QPointF(16.2, 9))
        right.lineTo(QPointF(12, 13.5))
        painter.drawPath(right)
        painter.drawLine(QPointF(10.2, 3.5), QPointF(7.8, 14.5))
    return _icon(draw, color)


def _turning_arrow(painter: QPainter, ink: QColor) -> None:
    """An arrow that comes up from below and turns back to the left."""
    painter.setPen(_pen(ink, 1.6))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(QPointF(15, 14.5))
    path.cubicTo(QPointF(15, 9), QPointF(11.5, 6.5), QPointF(4, 6.5))
    path.moveTo(QPointF(7.2, 3.2))
    path.lineTo(QPointF(3.8, 6.5))
    path.lineTo(QPointF(7.2, 9.8))
    painter.drawPath(path)


def undo(color: QColor) -> QIcon:
    """An arrow turning back to the left: Undo."""
    return _icon(_turning_arrow, color)


def redo(color: QColor) -> QIcon:
    """Undo's arrow, mirrored: Redo."""
    def draw(painter: QPainter, ink: QColor) -> None:
        painter.translate(SIZE, 0)
        painter.scale(-1, 1)
        _turning_arrow(painter, ink)
    return _icon(draw, color)


def plus(color: QColor) -> QIcon:
    """A plus sign: Insert."""
    def draw(painter: QPainter, ink: QColor) -> None:
        painter.setPen(_pen(ink, 1.7))
        painter.drawLine(QPointF(9, 3.5), QPointF(9, 14.5))
        painter.drawLine(QPointF(3.5, 9), QPointF(14.5, 9))
    return _icon(draw, color)


def more(color: QColor) -> QIcon:
    """Two chevrons to the right: a toolbar's buttons that have no room."""
    def draw(painter: QPainter, ink: QColor) -> None:
        painter.setPen(_pen(ink, 1.7))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        for x in (3.0, 9.5):
            path.moveTo(QPointF(x, 4))
            path.lineTo(QPointF(x + 5, 9))
            path.lineTo(QPointF(x, 14))
        painter.drawPath(path)
    return _icon(draw, color)


# -- the Media Library's glyphs ----------------------------------------------
# Drawn at any size with ``paint``: a tile's kind, and the lock on the
# thumbnail of a private file.

def _draw_lock(painter: QPainter, ink: QColor) -> None:
    """A padlock: only the person can open the file."""
    painter.setPen(_pen(ink, 1.6))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    shackle = QPainterPath()
    shackle.moveTo(QPointF(5.5, 8.5))
    shackle.lineTo(QPointF(5.5, 6))
    shackle.arcTo(QRectF(5.5, 2.5, 7, 7), 180, -180)
    shackle.lineTo(QPointF(12.5, 8.5))
    painter.drawPath(shackle)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(ink)
    painter.drawRoundedRect(QRectF(3.5, 8, 11, 8), 1.6, 1.6)


def _draw_video(painter: QPainter, ink: QColor) -> None:
    """A frame of film with a play triangle."""
    painter.setPen(_pen(ink, 1.4))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(1.5, 3.5, 15, 11), 2, 2)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(ink)
    triangle = QPainterPath()
    triangle.moveTo(QPointF(7.4, 6.4))
    triangle.lineTo(QPointF(12, 9))
    triangle.lineTo(QPointF(7.4, 11.6))
    triangle.closeSubpath()
    painter.drawPath(triangle)


def _draw_audio(painter: QPainter, ink: QColor) -> None:
    """A note: two heads on a beam."""
    painter.setPen(_pen(ink, 1.5))
    painter.drawLine(QPointF(7, 13), QPointF(7, 4))
    painter.drawLine(QPointF(14.5, 11.5), QPointF(14.5, 2.5))
    painter.drawLine(QPointF(7, 4), QPointF(14.5, 2.5))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(ink)
    painter.drawEllipse(QRectF(2.6, 11, 4.8, 3.8))
    painter.drawEllipse(QRectF(10.1, 9.5, 4.8, 3.8))


def _draw_document(painter: QPainter, ink: QColor) -> None:
    """A page with a folded corner: any other file."""
    painter.setPen(_pen(ink, 1.4))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    page = QPainterPath()
    page.moveTo(QPointF(4, 1.8))
    page.lineTo(QPointF(10.5, 1.8))
    page.lineTo(QPointF(14.5, 5.8))
    page.lineTo(QPointF(14.5, 16.2))
    page.lineTo(QPointF(4, 16.2))
    page.closeSubpath()
    painter.drawPath(page)
    painter.drawLine(QPointF(10.5, 1.8), QPointF(10.5, 5.8))
    painter.drawLine(QPointF(10.5, 5.8), QPointF(14.5, 5.8))


def _draw_alert(painter: QPainter, ink: QColor) -> None:
    """An exclamation mark in a circle: this did not work."""
    painter.setPen(_pen(ink, 1.6))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(1.8, 1.8, 14.4, 14.4))
    painter.drawLine(QPointF(9, 5.2), QPointF(9, 10))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(ink)
    painter.drawEllipse(QRectF(8, 11.8, 2, 2))


def _draw_text(painter: QPainter, ink: QColor) -> None:
    _draw_document(painter, ink)
    for y in (8, 10.5, 13):
        painter.drawLine(QPointF(6.5, y), QPointF(12, y))


def _draw_spreadsheet(painter: QPainter, ink: QColor) -> None:
    _draw_document(painter, ink)
    painter.drawRect(QRectF(6, 8, 6.5, 5.5))
    painter.drawLine(QPointF(9, 8), QPointF(9, 13.5))
    painter.drawLine(QPointF(6, 10.75), QPointF(12.5, 10.75))


def _draw_presentation(painter: QPainter, ink: QColor) -> None:
    painter.setPen(_pen(ink, 1.4))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(2, 3, 14, 10), 1, 1)
    painter.drawLine(QPointF(9, 13), QPointF(9, 16))
    painter.drawLine(QPointF(6, 16), QPointF(12, 16))
    painter.drawLine(QPointF(5, 6), QPointF(12, 6))
    painter.drawLine(QPointF(5, 9), QPointF(10, 9))


def _draw_archive(painter: QPainter, ink: QColor) -> None:
    _draw_document(painter, ink)
    painter.drawLine(QPointF(9, 7), QPointF(9, 14))
    for y in (8, 10, 12):
        painter.drawLine(QPointF(7.5, y), QPointF(10.5, y))


def _draw_book(painter: QPainter, ink: QColor) -> None:
    painter.setPen(_pen(ink, 1.4))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(3, 2, 12, 14), 1.5, 1.5)
    painter.drawLine(QPointF(6, 2), QPointF(6, 16))
    painter.drawLine(QPointF(8.5, 6), QPointF(12.5, 6))


_GLYPHS = {"lock": _draw_lock, "video": _draw_video, "audio": _draw_audio,
           "document": _draw_document, "alert": _draw_alert, "text": _draw_text,
           "spreadsheet": _draw_spreadsheet, "presentation": _draw_presentation,
           "archive": _draw_archive, "book": _draw_book}


def paint(name: str, painter: QPainter, rect: QRectF, color: QColor) -> None:
    """Draw a glyph from ``_GLYPHS`` to fill
    the square ``rect``, sharp at any size."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.topLeft())
    painter.scale(rect.width() / SIZE, rect.height() / SIZE)
    _GLYPHS[name](painter, QColor(color))
    painter.restore()
