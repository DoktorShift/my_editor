# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Waiting for files of a document to be on the media servers, so that
what waits for them (publishing it, saving it as a draft) goes on by
itself once they are, as Mail sends a message once its attachments are
ready, instead of asking for the command again.

``done`` is emitted once every file is uploaded, ``failed(sha256, code)``
for the first that fails (the rest go on: a Try Again finds them done
or on their way). Either ends the wait; so does ``cancel``. Whoever
waits holds the wait, as long as it waits.
"""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QObject, Signal

from nostr.media.assets import AssetState


class UploadWait(QObject):
    done = Signal()
    failed = Signal(str, str)        # sha256, stable code

    def __init__(self, manager, shas: Iterable[str]) -> None:
        super().__init__()
        self._manager = manager
        self._waiting = {sha.lower() for sha in shas}
        self._over = False
        manager.asset_changed.connect(self._look)
        manager.asset_upload_failed.connect(self._on_failed)

    def start(self) -> None:
        """Send every file not on its way yet, then look: one already on
        the servers (a library file) is done at once."""
        for sha in sorted(self._waiting):
            asset = self._manager.get(sha)
            if asset is not None and asset.upload_state in (AssetState.LOCAL, AssetState.FAILED):
                self._manager.request_upload(sha)
        self._look()

    def cancel(self) -> None:
        self._end()

    def _look(self, _sha: str = "") -> None:
        if self._over:
            return
        for sha in list(self._waiting):
            asset = self._manager.get(sha)
            if asset is not None and asset.is_uploaded:
                self._waiting.discard(sha)
            elif asset is None or asset.upload_state is AssetState.FAILED:
                self._end()
                self.failed.emit(sha, asset.failure_code if asset is not None else "")
                return
        if not self._waiting:
            self._end()
            self.done.emit()

    def _on_failed(self, sha: str, code: str) -> None:
        if not self._over and sha.lower() in self._waiting:
            self._end()
            self.failed.emit(sha.lower(), code)

    def _end(self) -> None:
        if self._over:
            return
        self._over = True
        self._manager.asset_changed.disconnect(self._look)
        self._manager.asset_upload_failed.disconnect(self._on_failed)
