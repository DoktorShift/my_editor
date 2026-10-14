# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cover framing and export, independent of the editor and upload lifecycle."""

from dataclasses import dataclass

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, QSize
from PySide6.QtGui import QImage, QPainter

MAX_COVER_EDGE = 1600
WIDE_ASPECT = 16 / 9


def crop_rect(size: QSize, aspect: float = WIDE_ASPECT,
              focus: QPointF = QPointF(0.5, 0.5), zoom: float = 1.0) -> QRectF:
    """A bounded source rectangle around a normalized focal point.

    The image keeps its proportions. Moving the subject toward an edge
    stops at that edge; zoom narrows the selected area without empty pixels.
    """
    if size.isEmpty() or aspect <= 0 or zoom < 1:
        raise ValueError("invalid cover geometry")
    width = min(size.width(), size.height() * aspect) / zoom
    height = width / aspect
    left = min(max(0.0, focus.x() * size.width() - width / 2), size.width() - width)
    top = min(max(0.0, focus.y() * size.height() - height / 2), size.height() - height)
    return QRectF(left, top, width, height)


def output_size(rect: QRectF) -> QSize:
    """Cap cover transfers at 1600 pixels per edge without upscaling."""
    scale = min(1.0, MAX_COVER_EDGE / max(rect.width(), rect.height()))
    return QSize(max(1, round(rect.width() * scale)), max(1, round(rect.height() * scale)))


@dataclass(frozen=True)
class CroppedCover:
    data: bytes
    mime: str
    size: QSize


def export_cover(image: QImage, rect: QRectF, *, jpeg: bool = False) -> CroppedCover:
    """Encode only the selected pixels into a new, metadata-free still image."""
    if image.isNull() or rect.isEmpty() or not QRectF(image.rect()).contains(rect):
        raise ValueError("crop is outside the image")
    size = output_size(rect)
    result = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(0)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(QRectF(result.rect()), image, rect)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    fmt, mime = ("JPEG", "image/jpeg") if jpeg and not image.hasAlphaChannel() else ("PNG", "image/png")
    if not result.save(buffer, fmt, 92):
        raise ValueError("cover could not be encoded")
    return CroppedCover(bytes(buffer.data()), mime, size)
