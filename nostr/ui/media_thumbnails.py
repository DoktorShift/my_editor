# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A bounded, serial queue for video posters and PDF first-page thumbnails.

Only visible files are queued. Remote files have a small download budget;
large videos already cached locally need no transfer. Thumbnails never
play sound or persist decrypted pixels to disk.
"""

from collections import deque

from PySide6.QtCore import QBuffer, QIODevice, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
from PySide6.QtPdf import QPdfDocument

import media_files
from ..blossom.store import MediaFile
from .media_source import PreviewResolver, THUMBNAIL_DOWNLOAD_LIMIT
from .media_picture import frame_pixmap


class MediaThumbnails(QObject):
    ready = Signal(str, object)
    # How long a video or audio file plays (its hash, milliseconds), found
    # on the way to its first frame. Audio has no frame: it ends there.
    duration_ready = Signal(str, int)

    def __init__(self, loader, visibility, parent=None):
        super().__init__(parent)
        self._loader = loader
        self._resolver = PreviewResolver(loader, visibility, parent=self)
        self._resolver.ready.connect(self._resolved)
        self._resolver.failed.connect(self._skip)
        self._queue = deque()
        self._active = None
        self._buffer = None
        self._player = None
        self._sink = None
        self._document = None
        self._generation = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(8000)
        self._timer.timeout.connect(self._skip)

    def request(self, media: MediaFile, mime: str) -> None:
        self._queue.append((media, mime))
        if self._active is None:
            self._next()

    def cancel(self) -> None:
        self._queue.clear()
        self._active = None
        self._resolver.cancel()
        self._clear_decoder()

    def _next(self):
        if self._active is not None or not self._queue:
            return
        self._active = self._queue.popleft()
        media, _mime = self._active
        # A cached public movie is cheap to decode regardless of size.
        cached = self._loader.cache_path(media.hash).is_file()
        self._resolver.resolve(media, max_bytes=None if cached else THUMBNAIL_DOWNLOAD_LIMIT,
                               download_limit=THUMBNAIL_DOWNLOAD_LIMIT)

    def _resolved(self, source):
        if self._active is None:
            return
        _media, mime = self._active
        generation = self._generation
        self._timer.start()
        if mime == "application/pdf":
            self._document = document = QPdfDocument(self)
            document.statusChanged.connect(
                lambda status: self._pdf_status(status, generation))
            if source.data is not None:
                self._buffer = QBuffer(self)
                self._buffer.setData(source.data)
                self._buffer.open(QIODevice.OpenModeFlag.ReadOnly)
                document.load(self._buffer)
            else:
                document.load(str(source.path))
            return
        self._player = QMediaPlayer(self)
        self._sink = QVideoSink(self)
        self._player.setVideoOutput(self._sink)
        self._sink.videoFrameChanged.connect(lambda frame: self._frame(frame, generation))
        self._player.durationChanged.connect(lambda ms: self._duration(ms, generation))
        self._player.errorOccurred.connect(lambda *_args: self._decoder_failed(generation))
        if source.data is not None:
            self._buffer = QBuffer(self)
            self._buffer.setData(source.data)
            self._buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            self._player.setSourceDevice(self._buffer, QUrl("file:///preview" + media_files.extension_for(mime)))
        else:
            self._player.setSource(QUrl.fromLocalFile(str(source.path)))
        self._player.play()              # no audio output is attached

    def _frame(self, frame, generation):
        if generation != self._generation or self._active is None or not frame.isValid():
            return
        pixmap = frame_pixmap(frame)
        if pixmap is not None:
            self._finish(pixmap)

    def _duration(self, milliseconds, generation):
        if generation != self._generation or self._active is None or milliseconds <= 0:
            return
        media, mime = self._active
        self.duration_ready.emit(media.hash, int(milliseconds))
        if media_files.kind_of(mime) == media_files.AUDIO:
            self._skip()

    def _decoder_failed(self, generation):
        if generation == self._generation:
            self._skip()

    def _pdf_status(self, status, generation):
        if generation != self._generation:
            return
        if status == QPdfDocument.Status.Ready:
            QTimer.singleShot(0, self, lambda: self._render_pdf(generation))
        elif status == QPdfDocument.Status.Error:
            self._skip()

    def _render_pdf(self, generation):
        if generation != self._generation or self._document is None:
            return
        size = self._document.pagePointSize(0).toSize()
        size.scale(QSize(320, 320), Qt.AspectRatioMode.KeepAspectRatio)
        image = self._document.render(0, size)
        self._finish(None if image.isNull() else QPixmap.fromImage(image))

    def _skip(self, *_args):
        if self._active is not None:
            self._finish(None)

    def _finish(self, pixmap):
        active, self._active = self._active, None
        self._clear_decoder()
        if active is not None and pixmap is not None:
            self.ready.emit(active[0].hash, pixmap)
        QTimer.singleShot(0, self, self._next)

    def _clear_decoder(self):
        self._generation += 1
        self._timer.stop()
        if self._player is not None:
            self._player.stop()
            self._player.setSource(QUrl())
            self._player.deleteLater()
            self._sink.deleteLater()
            self._player = self._sink = None
        if self._document is not None:
            self._document.close()
            self._document.deleteLater()
            self._document = None
        if self._buffer is not None:
            self._buffer.close()
            self._buffer.deleteLater()
            self._buffer = None
