# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pictures, films and sound in a tab of their own.

ImageViewerTab shows a picture fitted to the window, as Preview opens
one, with Zoom In, Zoom Out, Actual Size and Zoom to Fit on the keys
Preview has (Command-Plus, Command-Minus, Command-0, Command-9; Ctrl
elsewhere) and Ctrl or Command with the wheel. MediaPlayerTab plays a
film or a sound file with the player Quick Look uses
(nostr/ui/media_player.MediaPlayer), a sound file on a calm card with its
name.

Pictures are read and decoded by safe_picture on a worker thread, so
neither a large picture nor a file that is no picture at all (a pipe, a
device behind a symlink) can hold the window: only a regular file is
read, and only as far as a byte limit; the format the file's first
bytes claim is decoded, and nothing past MAX_DECODE_PIXELS. HEIC and
AVIF are shown when this Qt can decode them; viewer_for gives the info
card otherwise. An SVG is drawn with QtSvg after everything in it that
would make QtSvg read another file has been taken out, and only when
what it would draw stays within a bound.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QAction, QImage, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

import background
import file_kinds
import image_safety
import media_files
import safe_picture
from commands import platform_keys, standard_and
from i18n import _
from viewer_tabs import (
    FileViewerTab, card_css, colors, tile_pixmap,
)

_log = logging.getLogger(__name__)

_ZOOM_STEP = 1.25
_MIN_ZOOM = 0.05
_MAX_ZOOM = 16.0
# Room left around a picture fitted to the window.
_FIT_PADDING = 24



# --------------------------------------------------------------------------- #
# Decoding                                                                    #
# --------------------------------------------------------------------------- #

def can_show_picture(path: str) -> bool:
    """Whether ImageViewerTab can show the kind of picture at ``path`` on
    this computer (whether this one file decodes is known on loading)."""
    ext = os.path.splitext(path)[1].lower()
    # Formats known by their first bytes (image_safety's allowlist) are
    # always read; the others when this Qt has their plugin.
    if ext in image_safety.IMAGE_FILE_SUFFIXES:
        return True
    if ext == ".svg":
        return safe_picture.svg_available()
    if ext in safe_picture.PLUGIN_FORMATS:
        return safe_picture.qt_reads(safe_picture.PLUGIN_FORMATS[ext])
    return False


# --------------------------------------------------------------------------- #
# The picture tab                                                             #
# --------------------------------------------------------------------------- #

class _Canvas(QWidget):
    """The picture at a zoom: as large as the picture at that zoom, inside
    a scroll area that centers it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pixmap: QPixmap | None = None
        self.zoom = 1.0

    def natural_size(self) -> QSize:
        if self.pixmap is None:
            return QSize(0, 0)
        return self.pixmap.deviceIndependentSize().toSize()

    def set_zoom(self, zoom: float) -> None:
        self.zoom = zoom
        natural = self.natural_size()
        self.setFixedSize(max(1, round(natural.width() * zoom)),
                          max(1, round(natural.height() * zoom)))
        self.update()

    def paintEvent(self, _event) -> None:
        if self.pixmap is None:
            return
        painter = QPainter(self)
        # Smooth while shrinking or a little enlarged; past that, the
        # pixels themselves, as Preview shows them up close.
        if self.zoom < 3:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(self.rect(), self.pixmap)
        painter.end()


def image_keys():
    """The picture tab's own keys, for Help > Keyboard Shortcuts and for
    the window's check that no command takes them: (words, what it does,
    keys)."""
    return (
        (_("Zoom In"), lambda tab: tab.zoom_in(),
         standard_and(QKeySequence.StandardKey.ZoomIn, "Ctrl+=")),
        (_("Zoom Out"), lambda tab: tab.zoom_out(),
         standard_and(QKeySequence.StandardKey.ZoomOut)),
        (_("Actual Size"), lambda tab: tab.actual_size(), ("Ctrl+0",)),
        # The PDF reader's key for the whole page: Option-Command-2 on a
        # Mac, where Command-1 to 9 go to tabs.
        (_("Zoom to Fit"), lambda tab: tab.zoom_to_fit(),
         (platform_keys("Ctrl+Alt+2", "Ctrl+2"),)),
    )


class ImageViewerTab(FileViewerTab):
    """A picture, fitted to the window until it is zoomed."""

    kind = file_kinds.IMAGE
    _offers_insert = True

    def _build_body(self, body: QWidget) -> None:
        self._fit = True
        self._pixel_size = QSize()
        self._generation = 0
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        self._message = QLabel()
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)
        self._message.hide()
        layout.addWidget(self._message, 1)
        self.canvas = _Canvas()
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.canvas)
        self.scroll.setAccessibleName(_("Picture"))
        self.scroll.viewport().installEventFilter(self)
        layout.addWidget(self.scroll)
        self.setFocusProxy(self.scroll)

        self._zoom_out_btn = QToolButton()
        self._zoom_out_btn.setText("−")
        self._zoom_out_btn.setAccessibleName(_("Zoom Out"))
        self._zoom_out_btn.setToolTip(_("Zoom Out"))
        self._zoom_out_btn.clicked.connect(self.zoom_out)
        self._bar_middle.addWidget(self._zoom_out_btn)
        self._zoom_label = QLabel()
        self._zoom_label.setMinimumWidth(42)
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bar_middle.addWidget(self._zoom_label)
        self._zoom_in_btn = QToolButton()
        self._zoom_in_btn.setText("+")
        self._zoom_in_btn.setAccessibleName(_("Zoom In"))
        self._zoom_in_btn.setToolTip(_("Zoom In"))
        self._zoom_in_btn.clicked.connect(self.zoom_in)
        self._bar_middle.addWidget(self._zoom_in_btn)
        self._fit_btn = QToolButton()
        self._fit_btn.setText(_("Fit"))
        self._fit_btn.setAccessibleName(_("Zoom to Fit"))
        self._fit_btn.setToolTip(_("Zoom to Fit"))
        self._fit_btn.setCheckable(True)
        self._fit_btn.setChecked(True)
        self._fit_btn.clicked.connect(self.zoom_to_fit)
        self._bar_middle.addWidget(self._fit_btn)

        self.zoom_actions = []
        for words, act, keys in image_keys():
            action = QAction(words, self)
            action.setShortcuts([QKeySequence(k) for k in keys])
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            action.triggered.connect(lambda _checked=False, act=act: act(self))
            self.addAction(action)
            self.zoom_actions.append(action)

    def _load(self) -> bool:
        """Start reading the picture on a worker thread; ``load_ok`` and
        ``load_error`` are set when it arrives (``is_loading`` until then).
        A picture shown already stays until the new one is there."""
        self._generation += 1
        generation = self._generation
        if not super()._load():
            self._loading = False
            self.canvas.pixmap = None
            self._show_message(self.load_error)
            return False
        self.load_ok = False
        self._loading = True
        if self.canvas.pixmap is None:
            self._show_message(_("Loading…"))
        path = self._file_path
        background.run(lambda: safe_picture.read_picture(path),
                       lambda image: self._loaded(generation, image),
                       lambda _error: self._loaded(generation, None), receiver=self)
        return True

    def _loaded(self, generation: int, image: QImage | None) -> None:
        if self._released or generation != self._generation:
            return                                   # closed, or a newer read is on its way
        self._loading = False
        if image is None or image.isNull():
            self.load_ok = False
            self.load_error = _("This picture could not be shown.")
            self.canvas.pixmap = None
            self._pixel_size = QSize()
            self._show_message(self.load_error)
        else:
            self.load_ok = True
            self.load_error = ""
            # The picture's own pixels, whatever resolution it is drawn at.
            ratio = image.devicePixelRatio() or 1.0
            self._pixel_size = QSize(round(image.width() / ratio),
                                     round(image.height() / ratio))
            self.canvas.pixmap = QPixmap.fromImage(image)
            self._show_message("")
            self._apply_zoom()
        self._refresh()

    def _show_message(self, words: str) -> None:
        """A calm sentence in place of the picture (loading, unreadable);
        the picture again when ``words`` is empty."""
        self._message.setText(words)
        self._message.setVisible(bool(words))
        self.scroll.setVisible(not words)

    def _facts(self) -> list[str]:
        facts = super()._facts()
        if self._pixel_size.isValid() and not self._pixel_size.isEmpty():
            facts.insert(0, f"{self._pixel_size.width()} × {self._pixel_size.height()}")
        return facts

    # -- zoom --------------------------------------------------------------

    def _fit_zoom(self) -> float:
        natural = self.canvas.natural_size()
        if natural.isEmpty():
            return 1.0
        room = self.scroll.viewport().size()
        width = max(1, room.width() - _FIT_PADDING)
        height = max(1, room.height() - _FIT_PADDING)
        return min(1.0, width / natural.width(), height / natural.height())

    def _apply_zoom(self, zoom: float | None = None) -> None:
        if self.canvas.pixmap is None:
            return
        if zoom is None:
            zoom = self._fit_zoom() if self._fit else self.canvas.zoom
        self.canvas.set_zoom(max(_MIN_ZOOM, min(zoom, _MAX_ZOOM)))
        self._fit_btn.setChecked(self._fit)
        self._zoom_label.setText(_("{percent}%").format(percent=round(self.canvas.zoom * 100)))

    def zoom(self) -> float:
        return self.canvas.zoom

    def is_fitted(self) -> bool:
        return self._fit

    def zoom_in(self) -> None:
        self._fit = False
        self._apply_zoom(self.canvas.zoom * _ZOOM_STEP)

    def zoom_out(self) -> None:
        self._fit = False
        self._apply_zoom(self.canvas.zoom / _ZOOM_STEP)

    def actual_size(self) -> None:
        self._fit = False
        self._apply_zoom(1.0)

    def zoom_to_fit(self) -> None:
        self._fit = True
        self._apply_zoom()

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.scroll.viewport():
            if event.type() == QEvent.Type.Resize and self._fit:
                self._apply_zoom()
            elif (event.type() == QEvent.Type.Wheel
                  and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                if event.angleDelta().y() > 0:
                    self.zoom_in()
                elif event.angleDelta().y() < 0:
                    self.zoom_out()
                return True
        return super().eventFilter(obj, event)

    def release(self) -> None:
        super().release()
        self._generation += 1
        self._loading = False
        self.canvas.pixmap = None

    def _apply_theme(self, is_dark: bool) -> None:
        c = colors(is_dark)
        self._message.setStyleSheet(
            f"color: {c['muted']}; font-size: 13px; background: {c['viewer']};")
        self.scroll.setStyleSheet(f"QScrollArea {{ background: {c['viewer']}; border: none; }}")
        self.scroll.viewport().setStyleSheet(f"background: {c['viewer']};")
        self.canvas.setStyleSheet("background: transparent;")


# --------------------------------------------------------------------------- #
# Films and sound                                                             #
# --------------------------------------------------------------------------- #

class MediaPlayerTab(FileViewerTab):
    """A film, or a sound file on a calm card, with the player's controls.
    Nothing plays until asked; closing the tab stops it."""

    kind = file_kinds.VIDEO
    _offers_insert = True

    @classmethod
    def _kind_of(cls, path: str) -> str:
        audio = file_kinds.kind_of_path(path) == file_kinds.AUDIO
        return file_kinds.AUDIO if audio else file_kinds.VIDEO

    def _build_body(self, body: QWidget) -> None:
        # Imported here: the player module lives with the media library
        # and needs nothing of Nostr but its package.
        from nostr.ui.media_player import MediaPlayer
        self._duration = 0
        self._resolution = QSize()
        layout = QVBoxLayout(body)
        self.player = MediaPlayer()
        self.player.failed.connect(self._failed)
        self.player.player.durationChanged.connect(self._duration_changed)
        self.player.player.mediaStatusChanged.connect(self._status_changed)
        self._message = QLabel()
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)
        self._message.hide()

        if self.kind == file_kinds.AUDIO:
            layout.setContentsMargins(28, 28, 28, 28)
            layout.addStretch(1)
            row = QHBoxLayout()
            row.addStretch(1)
            self._card = QFrame()
            self._card.setObjectName("ViewerCard")
            self._card.setMaximumWidth(520)
            self._card.setMinimumWidth(320)
            self._card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            self._card_shadow = QGraphicsDropShadowEffect(self._card)
            self._card_shadow.setBlurRadius(24)
            self._card_shadow.setOffset(0, 4)
            self._card.setGraphicsEffect(self._card_shadow)
            card = QVBoxLayout(self._card)
            card.setContentsMargins(28, 26, 28, 22)
            card.setSpacing(4)
            self._card_tile = QLabel()
            self._card_tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._card_tile.setFixedHeight(64)
            card.addWidget(self._card_tile)
            card.addSpacing(8)
            self._card_name = QLabel(os.path.basename(self._file_path))
            self._card_name.setObjectName("CardName")
            self._card_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._card_name.setWordWrap(True)
            self._card_name.setTextFormat(Qt.TextFormat.PlainText)
            card.addWidget(self._card_name)
            self._card_facts = QLabel()
            self._card_facts.setObjectName("CardFacts")
            self._card_facts.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._card_facts.setWordWrap(True)
            card.addWidget(self._card_facts)
            card.addSpacing(12)
            # The card is the picture of a sound; the player's own glyph
            # area is not needed.
            self.player.display.hide()
            card.addWidget(self.player)
            card.addWidget(self._message)
            row.addWidget(self._card, 4)
            row.addStretch(1)
            layout.addLayout(row)
            layout.addStretch(1)
        else:
            layout.setContentsMargins(16, 16, 16, 12)
            layout.addWidget(self.player, 1)
            layout.addWidget(self._message)
        self.setFocusProxy(self.player.play_button)

    def _load(self) -> bool:
        if not super()._load():
            return False
        from nostr.ui.media_source import PreviewSource
        self._message.hide()
        mime = media_files.type_of(self._file_path) or (
            "audio/mpeg" if self.kind == file_kinds.AUDIO else "video/mp4")
        self.player.set_source(PreviewSource(path=Path(self._file_path)), mime)
        return True

    def _failed(self, _reason: str) -> None:
        self._message.setText(_("This audio or video format can’t be played on this computer. "
                                "Open it in another app."))
        self._message.show()

    def _duration_changed(self, duration: int) -> None:
        self._duration = max(0, duration)
        self._refresh()

    def _status_changed(self, _status) -> None:
        if self.kind != file_kinds.VIDEO:
            return
        from PySide6.QtMultimedia import QMediaMetaData
        resolution = self.player.player.metaData().value(QMediaMetaData.Key.Resolution)
        if (isinstance(resolution, QSize) and resolution.isValid()
                and resolution != self._resolution):
            self._resolution = resolution
            self._refresh()

    def _facts(self) -> list[str]:
        from nostr.ui.media_player import time_label
        facts = super()._facts()
        if self._duration:
            facts.insert(0, time_label(self._duration))
        if self._resolution.isValid() and not self._resolution.isEmpty():
            facts.insert(0, f"{self._resolution.width()} × {self._resolution.height()}")
        return facts

    def _refresh(self) -> None:
        super()._refresh()
        if self.kind == file_kinds.AUDIO:
            self._card_facts.setText(" · ".join(self._facts()))

    def release(self) -> None:
        """Stop playing and let go of the file as the tab closes."""
        super().release()
        self.player.clear()

    def _apply_theme(self, is_dark: bool) -> None:
        c = colors(is_dark)
        self._message.setStyleSheet(
            f"color: {c['muted']}; font-size: 12px; background: transparent;")
        self.player.setStyleSheet(f"QLabel {{ color: {c['fg']}; background: transparent; }}")
        if self.kind == file_kinds.AUDIO:
            self._card.setStyleSheet(card_css(is_dark))
            self._card_shadow.setColor(c["shadow"])
            self._card_tile.setPixmap(tile_pixmap(file_kinds.kind_icon_name(file_kinds.AUDIO),
                                                   file_kinds.kind_tint(file_kinds.AUDIO)))
