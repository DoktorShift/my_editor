# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The card an audio or video file shows as in the text, where readers
show its player: what it is, its name, and how its upload is going.

It is the picture of the file's object in the document (an asset, as a
picture is), drawn again whenever the upload moves on: progress, done,
failed. Drawing it changes nothing in the document, so it is never a step
of Undo, and finishing an upload edits nothing the person wrote.

It plays nothing (the app carries no media player), so it shows no play
button, which would be a control that does nothing: a glyph says what the
file is, a waveform for audio and a strip of film for video. It keeps a
little room above and below it, as a picture's paragraph does.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath

import media_files
from constants import (
    DARK_BORDER, DARK_FG, DARK_MENU_BG, DARK_MUTED_FG, LIGHT_BORDER, LIGHT_FG, LIGHT_MUTED_FG,
)

WIDTH = 420
MARGIN = 6          # room above and below the card
HEIGHT = 58 + 2 * MARGIN
_SCALE = 2          # drawn at twice the size, so it is sharp on a high density screen
# A waveform's bars, as parts of the glyph's height.
_WAVE = (0.35, 0.7, 1.0, 0.55, 0.85, 0.4)


def render(title: str, state: str, *, dark: bool, mime: str) -> QImage:
    """The card: a glyph for ``mime``'s kind, ``title`` ("Audio ·
    interview.mp3") and, under it in muted text, ``state`` ("Uploading…
    42 %", or "")."""
    image = QImage(WIDTH * _SCALE, HEIGHT * _SCALE, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(_SCALE)
    image.fill(Qt.GlobalColor.transparent)
    fill = QColor(DARK_MENU_BG if dark else "#F3F4F6")
    border = QColor(DARK_BORDER if dark else LIGHT_BORDER)
    ink = QColor(DARK_FG if dark else LIGHT_FG)
    muted = QColor(DARK_MUTED_FG if dark else LIGHT_MUTED_FG)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    card = QRectF(0.5, MARGIN + 0.5, WIDTH - 1, HEIGHT - 2 * MARGIN - 1)
    path = QPainterPath()
    path.addRoundedRect(card, 8, 8)
    painter.fillPath(path, fill)
    painter.setPen(border)
    painter.drawPath(path)

    glyph = QRectF(16, HEIGHT / 2 - 11, 26, 22)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(muted)
    if media_files.kind_of(mime) == media_files.VIDEO:
        _film(painter, glyph, fill)
    else:
        _waveform(painter, glyph)

    left = 56
    room = WIDTH - left - 14
    bold = QFont()
    bold.setBold(True)
    painter.setFont(bold)
    painter.setPen(ink)
    middle = HEIGHT / 2
    title_y = middle - 4 if state else middle + QFontMetrics(bold).ascent() / 2 - 2
    painter.drawText(int(left), int(title_y),
                     QFontMetrics(bold).elidedText(title, Qt.TextElideMode.ElideMiddle, room))
    if state:
        plain = QFont()
        painter.setFont(plain)
        painter.setPen(muted)
        painter.drawText(int(left), int(middle + 16), QFontMetrics(plain).elidedText(
            state, Qt.TextElideMode.ElideRight, room))
    painter.end()
    return image


def _waveform(painter: QPainter, area: QRectF) -> None:
    step = area.width() / len(_WAVE)
    for i, part in enumerate(_WAVE):
        height = area.height() * part
        painter.drawRoundedRect(QRectF(area.left() + i * step + 1, area.center().y() - height / 2,
                                       step - 2, height), 1.5, 1.5)


def _film(painter: QPainter, area: QRectF, holes) -> None:
    painter.drawRoundedRect(area, 3, 3)
    painter.setBrush(holes)
    for row in (area.top() + 2.5, area.bottom() - 5.5):
        x = area.left() + 3
        while x + 3 <= area.right() - 2:
            painter.drawRect(QRectF(x, row, 3, 3))
            x += 5.5
