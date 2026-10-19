# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Waiting for files of a document to be on the media servers, so that
what waits for them (publishing it, saving it as a draft) goes on by
itself once they are, as Mail sends a message once its attachments are
ready, instead of asking for the command again.

``done`` is emitted once every file is uploaded, ``failed(sha256, code)``
for the file whose upload failed. Either ends the wait; so does
``cancel``. Whoever waits holds the wait, as long as it waits.

The outcome is told once the asset manager has finished what it was
doing (from the event loop, never inside its signals): an alert opened
inside them would run while its queue is half drained. And it names the
file that failed, not one the manager stopped because of it: when an
upload fails the manager marks the files queued behind it failed too,
before the one that failed, with no failure signal of their own.
"""

from __future__ import annotations

from typing import Iterable, Optional, Tuple

from PySide6.QtCore import QObject, QTimer, Signal

from nostr.media.assets import AssetState


class UploadWait(QObject):
    done = Signal()
    failed = Signal(str, str)        # sha256, stable code

    def __init__(self, manager, shas: Iterable[str]) -> None:
        super().__init__()
        self._manager = manager
        self._waiting = {sha.lower() for sha in shas}
        self._over = False
        self._failure: Optional[Tuple[str, str]] = None
        manager.asset_changed.connect(self._moved)
        manager.asset_upload_failed.connect(self._on_failed)

    def start(self) -> None:
        """Send every file not on its way yet (a file that failed before
        is sent again), then settle: one already on the servers (a
        library file) is done at once. Nothing more is sent once the wait
        is over or a file has failed, a refusal on the spot included."""
        for sha in sorted(self._waiting):
            if self._over or self._failure is not None:
                break
            asset = self._manager.get(sha)
            if asset is not None and asset.upload_state in (AssetState.LOCAL, AssetState.FAILED):
                self._manager.request_upload(sha)
        self._settle_soon()

    def cancel(self) -> None:
        self._end()

    def _moved(self, sha: str) -> None:
        if sha.lower() in self._waiting:
            self._settle_soon()

    def _on_failed(self, sha: str, code: str) -> None:
        if not self._over and self._failure is None and sha.lower() in self._waiting:
            self._failure = (sha.lower(), code)
            self._settle_soon()

    def _settle_soon(self) -> None:
        if not self._over:
            QTimer.singleShot(0, self._settle)

    def _settle(self) -> None:
        if self._over:
            return
        if self._failure is not None:
            self._end()
            self.failed.emit(*self._failure)
            return
        assets = [(sha, self._manager.get(sha)) for sha in sorted(self._waiting)]
        for sha, asset in assets:
            if asset is None or asset.upload_state is AssetState.FAILED:
                # Failed with no signal of its own (stopped behind another
                # document's upload), or gone from the index.
                self._end()
                self.failed.emit(sha, asset.failure_code if asset is not None else "")
                return
        if all(asset.is_uploaded for _sha, asset in assets):
            self._end()
            self.done.emit()

    def _end(self) -> None:
        if self._over:
            return
        self._over = True
        self._manager.asset_changed.disconnect(self._moved)
        self._manager.asset_upload_failed.disconnect(self._on_failed)
