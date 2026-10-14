# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A fitted picture or file glyph, shared by Quick Look and the player."""

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPalette, QPixmap, QTransform
from PySide6.QtWidgets import QSizePolicy, QWidget

import format_icons


def frame_pixmap(frame):
    """Apply presentation rotation and mirroring to a decoded still.

    Qt's toImage handles the surface format but excludes the frame's
    presentation transform. Use the older accessor only on Qt 6.6-6.8.
    """
    if not frame.isValid():
        return None
    image = frame.toImage()
    if image.isNull():
        return None
    rotation = frame.rotation().value if hasattr(frame, "rotation") else frame.rotationAngle().value
    if rotation:
        image = image.transformed(QTransform().rotate(rotation))
    if frame.mirrored():
        image = image.transformed(QTransform().scale(-1, 1))
    return QPixmap.fromImage(image)


class MediaPicture(QWidget):
    def __init__(self, parent=None, *, allow_upscale=False):
        super().__init__(parent)
        self.pixmap = None
        self.icon = None
        self.glyph = ""
        self._allow_upscale = allow_upscale
        self.setMinimumSize(QSize(280, 200))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        area = self.rect()
        if self.pixmap is not None and not self.pixmap.isNull():
            original = self.pixmap.deviceIndependentSize().toSize()
            size = original.scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
            if not self._allow_upscale and size.width() > original.width():
                size = original
            target = self.rect()
            target.setSize(size)
            target.moveCenter(area.center())
            painter.drawPixmap(target, self.pixmap)
        elif self.glyph or (self.icon is not None and not self.icon.isNull()):
            side = min(96, area.width() // 3, area.height() // 2)
            rect = QRectF(area.center().x() - side / 2, area.center().y() - side / 2, side, side)
            if self.icon is not None and not self.icon.isNull():
                self.icon.paint(painter, rect.toRect())
            else:
                format_icons.paint(self.glyph, painter, rect,
                                   self.palette().color(QPalette.ColorRole.PlaceholderText))
        painter.end()
