# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Blossom blob thumbnail loader and content-addressed byte store.

Downloads image blobs to a disk cache and emits a ``QPixmap`` for the
caller (the media grid in the Library dialog, and the preview lightbox).
``put_bytes`` / ``has`` make the same cache the app's local blob store,
so bytes are durable before any upload is attempted.

Cache layout: ~/.config/my_editor/blossom_cache/<sha256>
The filename is the content hash, so the cache is content-addressed and
never needs invalidation.

The bytes are a stranger's until proven otherwise: the URL is checked
against the media policy before the request and again after redirects,
the transfer is capped mid-flight, the hash is verified, and the decode
goes through an explicit format allowlist rather than Qt sniffing.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

import url_safety
from image_safety import decode_image_bytes


CACHE_DIR = Path.home() / ".config" / "my_editor" / "blossom_cache"

# Hard upper bound on thumbnail downloads. The library is restricted to
# files the user uploaded themselves, so they can't accidentally pull a
# multi-gigabyte object, but a malicious server returning an unbounded
# stream still has to be stopped.
_MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024  # 25 MiB
_HTTP_TIMEOUT_MS = 30_000

# A content fetch carries no credentials, and CDN hops are normal, so
# redirects are followed. Qt's no-less-safe policy refuses an https to
# http downgrade; the final URL is re-validated in the handler because
# that policy still allows https to https into a loopback address.
_MAX_REDIRECTS = 4

_UNSAFE_URL_REASON = "blob URL was not allowed"


class ThumbnailLoader(QObject):
    """Resolve a Blossom blob URL to a local file path + QPixmap.

    Always keyed by sha256; the URL is only used when the cache misses.
    Concurrent requests for the same hash coalesce.
    """

    ready = Signal(str, str, object)   # sha256, local_path, QPixmap
    failed = Signal(str, str)          # sha256, reason

    def __init__(
        self,
        parent: Optional[QObject] = None,
        *,
        cache_dir=None,
        nam=None,
    ) -> None:
        super().__init__(parent)
        # Both seams exist for tests: no test may write to the real
        # ~/.config, and none may touch a network.
        self._cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        _chmod(self._cache_dir, 0o700)
        self._nam = nam or QNetworkAccessManager(self)
        self._inflight: Dict[str, QNetworkReply] = {}

    def cache_path(self, sha256: str) -> Path:
        return self._cache_dir / sha256.lower()

    # -- byte store --------------------------------------------------------

    def has(self, sha256: str) -> bool:
        return self.cache_path(sha256).is_file()

    def put_bytes(self, data: bytes) -> str:
        """Store ``data`` under its own sha256 and return that hash.

        Raises OSError when the cache cannot be written, so callers can
        degrade instead of silently losing the bytes.
        """
        sha = hashlib.sha256(data).hexdigest()
        path = self.cache_path(sha)
        if path.is_file():
            return sha
        _write_cache_file(path, data)
        return sha

    # -- resolution --------------------------------------------------------

    def load(self, sha256: str, url: str) -> None:
        """Asynchronously resolve the blob. Emits ``ready`` on success or
        ``failed`` on any error. Idempotent: a second call for the same
        hash while a request is in flight is a no-op (the in-flight reply
        will fire ``ready`` for both callers via signal broadcast)."""
        sha = sha256.lower()
        path = self.cache_path(sha)
        if path.is_file():
            try:
                data = path.read_bytes()
            except OSError:
                data = b""
            if data and hashlib.sha256(data).hexdigest() == sha:
                image = decode_image_bytes(data)
                if image is None:
                    # Valid bytes, just not a format this process will
                    # render. Keep them: re-downloading forever is the
                    # bug the old code had here.
                    self.failed.emit(sha, "not an image")
                    return
                self.ready.emit(sha, str(path), QPixmap.fromImage(image))
                return
            # Truly corrupt entry: the bytes do not hash to their name.
            try:
                path.unlink()
            except OSError:
                pass
        if sha in self._inflight:
            return
        if not url_safety.is_safe_media_url(url):
            self.failed.emit(sha, _UNSAFE_URL_REASON)
            return
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", b"my-editor-blossom-thumb/1")
        request.setTransferTimeout(_HTTP_TIMEOUT_MS)
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        request.setMaximumRedirectsAllowed(_MAX_REDIRECTS)
        reply = self._nam.get(request)
        self._inflight[sha] = reply
        oversize = {"hit": False}

        def _size_guard(received: int, total: int, r=reply) -> None:
            if oversize["hit"]:
                return
            if received > _MAX_DOWNLOAD_BYTES or (
                total > 0 and total > _MAX_DOWNLOAD_BYTES
            ):
                oversize["hit"] = True
                r.abort()

        reply.downloadProgress.connect(_size_guard)
        reply.finished.connect(
            lambda s=sha, r=reply, p=path, u=url: self._on_reply(
                s, r, p, oversize, u)
        )

    def _on_reply(
        self,
        sha: str,
        reply: QNetworkReply,
        path: Path,
        oversize: dict,
        requested_url: str,
    ) -> None:
        self._inflight.pop(sha, None)
        try:
            if oversize["hit"]:
                self.failed.emit(sha, "blob exceeds cache limit")
                return
            if reply.error() != QNetworkReply.NoError:
                self.failed.emit(sha, reply.errorString() or "network error")
                return
            # Redirects were followed, so the bytes may come from an
            # origin the caller never named; validate where they came
            # from before reading them.
            if not _final_url_allowed(requested_url, reply.url().toString()):
                self.failed.emit(sha, _UNSAFE_URL_REASON)
                return
            data = bytes(reply.readAll())
            if not data:
                self.failed.emit(sha, "empty response")
                return
            if len(data) > _MAX_DOWNLOAD_BYTES:
                self.failed.emit(sha, "blob exceeds cache limit")
                return
            # Validate the bytes match the hash before trusting them.
            actual = hashlib.sha256(data).hexdigest()
            if actual != sha:
                self.failed.emit(sha, "downloaded bytes do not match sha256")
                return
            image = decode_image_bytes(data)
            if image is None:
                # Non-image blob, or a format outside the decode
                # allowlist. Still cache it but tell the caller there is
                # no pixmap to show.
                try:
                    _write_cache_file(path, data)
                except OSError:
                    pass
                self.failed.emit(sha, "not an image")
                return
            try:
                _write_cache_file(path, data)
            except OSError:
                pass
            self.ready.emit(sha, str(path), QPixmap.fromImage(image))
        finally:
            reply.deleteLater()


def _final_url_allowed(requested: str, final: str) -> bool:
    """Whether bytes served from ``final`` may be read.

    The media policy alone is not enough here: it accepts loopback so a
    local dev server works, which would let a public server redirect
    into 127.0.0.1 and turn the app into a probe of the user's own
    machine. Staying on the requested origin is always fine; moving
    origin is fine only to a host the mirror policy accepts, which
    refuses loopback, link-local and private IP literals.
    """
    if not url_safety.is_safe_media_url(final):
        return False
    if url_safety.same_origin(requested, final):
        return True
    return url_safety.is_safe_mirror_source(final)


def _chmod(target, mode: int) -> None:
    """Best-effort permissions. On Windows chmod only toggles the
    read-only bit, and a failure here must never stop startup."""
    try:
        os.chmod(target, mode)
    except OSError:
        pass


def _write_cache_file(path: Path, data: bytes) -> None:
    """Atomically place ``data`` at ``path``, owner-readable only.

    The cache records which blobs the user looked at, so it is kept as
    private as the config directory it lives in. An aborted write leaves
    no temp file behind.
    """
    fd, tmp_path = tempfile.mkstemp(
        prefix=".blob_", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
