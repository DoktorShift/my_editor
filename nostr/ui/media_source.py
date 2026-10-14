# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Resolve verified local bytes for a preview, without publishing a file.

Downloads reuse the guarded, streaming Save As transport. Public files
stay in the blob cache; private files stay encrypted there and only their
in-memory bytes reach viewers. Generation checks prevent a slow result
from replacing the selection the person has moved to.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from i18n import _
from ..blossom.client import looks_like_sha256
from ..blossom.store import MediaFile
from ..media.media_visibility import MediaVisibility
from ..imports.workers import run_blocking

PRIVATE_PREVIEW_LIMIT = 128 * 1024 * 1024
THUMBNAIL_DOWNLOAD_LIMIT = 25 * 1024 * 1024


class _CacheMismatch(ValueError):
    pass


class _PreviewTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class _Request:
    """A selection's immutable metadata, safe to hand to a worker."""

    sha: str
    path: Path
    size: int
    urls: tuple[str, ...]
    private: bool
    read_limit: int | None
    download_limit: int | None
    generation: int


@dataclass(frozen=True)
class PreviewSource:
    """A verified public path or decrypted private bytes; never a key."""

    path: Path | None = None
    data: bytes | None = None

    def __post_init__(self) -> None:
        if (self.path is None) == (self.data is None):
            raise ValueError("a preview needs either a path or in-memory bytes")

    def read(self, limit: int) -> bytes:
        if self.data is not None:
            return self.data[:limit]
        with self.path.open("rb") as stream:
            return stream.read(limit)


class PreviewResolver(QObject):
    ready = Signal(object)           # PreviewSource
    failed = Signal(str)             # a sentence for the preview
    progress = Signal(int, int)

    def __init__(self, loader, visibility: MediaVisibility, parent=None):
        super().__init__(parent)
        self._loader = loader
        self._visibility = visibility
        self._generation = 0
        self._download = None

    def cancel(self) -> None:
        self._generation += 1
        job, self._download = self._download, None
        if job is not None:
            job.cancel()

    def resolve(self, media: MediaFile, *, max_bytes: int | None = None,
                download_limit: int | None = None) -> None:
        self.cancel()
        generation = self._generation
        sha = media.hash
        if not looks_like_sha256(sha):
            self.failed.emit(_("This file has no valid content hash."))
            return
        if self._visibility.is_unknown(sha):
            self.failed.emit(_("This file can’t be previewed until its privacy has been checked."))
            return
        private = self._visibility.is_private(sha)
        if private:
            max_bytes = min(max_bytes, PRIVATE_PREVIEW_LIMIT) if max_bytes is not None else PRIVATE_PREVIEW_LIMIT
        limits = [limit for limit in (max_bytes, download_limit) if limit is not None]
        path = self._loader.cache_path(sha)
        urls = tuple(item["url"] for item in media.urls if item.get("url")) or (media.url,)
        request = _Request(sha.lower(), path, media.size, urls, private,
                           max_bytes, min(limits) if limits else None, generation)
        if path.is_file():
            self._read(request)
            return
        self._fetch(request)

    def _fetch(self, request: _Request, urls=None) -> None:
        if request.generation != self._generation:
            return
        limit = request.download_limit
        if limit is not None and (request.size <= 0 or request.size > limit):
            self.failed.emit(_("This file is too large to preview. Use Save As to open it."))
            return
        urls = urls or request.urls
        job = self._loader.download_to(urls[0], request.sha, request.path, size=request.size)
        self._download = job

        def progress(received, total):
            if request.generation == self._generation:
                self.progress.emit(received, total)

        job.progress.connect(progress)

        def finished(reason):
            job.deleteLater()
            if request.generation != self._generation:
                return
            self._download = None
            if reason in ("network", "missing", "server", "wrong_file") and len(urls) > 1:
                self._fetch(request, urls[1:])
            elif reason:
                self.failed.emit(_("Couldn’t load this preview. Use Save As or try again."))
            else:
                self._read(request, repair=False)

        job.finished.connect(finished)

    def _read(self, request: _Request, *, repair=True) -> None:
        # Capture the opener here, on the GUI thread. The worker never
        # reads mutable library state or holds a private-file key.
        opener = self._visibility.private_opener(request.sha) if request.private else None

        def read():
            path = request.path
            if request.read_limit is not None and path.stat().st_size > request.read_limit:
                raise _PreviewTooLarge()
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != request.sha:
                raise _CacheMismatch()
            if request.private:
                body = opener(path.read_bytes())
                if body is None:
                    raise ValueError("key")
                return PreviewSource(data=body)
            return PreviewSource(path=path)

        def done(source):
            if request.generation == self._generation:
                self.ready.emit(source)

        def failed(error):
            if request.generation != self._generation:
                return
            if isinstance(error, _CacheMismatch) and repair:
                self._fetch(request)
            elif isinstance(error, _PreviewTooLarge):
                self.failed.emit(_("This file is too large to preview. Use Save As to open it."))
            else:
                self.failed.emit(_("Couldn’t read this file’s preview. Use Save As or try again."))

        run_blocking(read, done, failed, parent=self)
