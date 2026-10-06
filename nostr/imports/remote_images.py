# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Images of posts that are not imported yet: covers, thumbnails, icons.

The Imports window shows each post's cover in the list, its images in
the article, and each source's site icon. All of them are addresses a
feed named, so they are fetched like everything else the importer
fetches: through the network guard (netguard.py: nothing on the local
network, redirects checked) and :class:`BlobFetcher` (size cap), then
decoded only through the image allow-list (image_safety.py).

:class:`RemoteImages` keeps what it decoded in memory (the most recent
ones, so a long list does not grow without bound) and answers
:meth:`image` from there, never from the network; :meth:`request`
fetches one and ``ready`` says when it arrived. Every view asks
``image`` and repaints on ``ready``. A failed address is remembered for
the session and not asked again.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional, Set

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from image_safety import decode_image_bytes

from .fetch import BlobFetcher

MAX_KEPT = 120
MAX_BYTES = 8 * 1024 * 1024
MAX_PARALLEL = 4


class RemoteImages(QObject):
    """Guarded, in-memory image cache for the Imports window.

    Signals:
      ready(str)     an image arrived (its address)
    """

    ready = Signal(str)

    def __init__(self, *, fetcher=None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._fetcher = fetcher or BlobFetcher(self, max_bytes=MAX_BYTES)
        self._images: "OrderedDict[str, QImage]" = OrderedDict()
        self._failed: Set[str] = set()
        self._asked: Set[str] = set()
        self._queue: list = []

    def image(self, url: str) -> Optional[QImage]:
        """The image at ``url`` when it is here already."""
        image = self._images.get(url)
        if image is not None:
            self._images.move_to_end(url)
        return image

    def request(self, url: str) -> None:
        """Fetch ``url`` unless it is here, on its way, or failed before."""
        if not url or url in self._images or url in self._failed or url in self._asked:
            return
        if url in self._queue:
            return
        self._queue.append(url)
        self._pump()

    def _pump(self) -> None:
        while self._queue and len(self._asked) < MAX_PARALLEL:
            url = self._queue.pop(0)
            self._asked.add(url)
            self._fetcher.fetch(url, on_success=lambda data, _mime, u=url: self._got(u, data),
                                on_failure=lambda _reason, u=url: self._lost(u))

    def _got(self, url: str, data: bytes) -> None:
        self._asked.discard(url)
        image = decode_image_bytes(data)
        if image is None or image.isNull():
            self._failed.add(url)
        else:
            self._images[url] = image
            while len(self._images) > MAX_KEPT:
                self._images.popitem(last=False)
            self.ready.emit(url)
        self._pump()

    def _lost(self, url: str) -> None:
        self._asked.discard(url)
        self._failed.add(url)
        self._pump()
