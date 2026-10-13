# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Save As for the Media Library.

One file at a time: from this computer's copy when there is one, else
streamed from its media servers, one address after another, bounded by
its own size and checked against its hash before it takes its name. A
private file is saved as the file, never as its envelope.

The saver belongs to what outlives the library windows (the main
window), so a save goes on when the window that asked for it closes, as
a copy in the Finder does. Whoever is looking says how it went: the
library window while one is open, else the main window's status bar.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Callable, List, Optional

from PySide6.QtCore import QObject, Signal

from atomic_file import free_path, save_document
from i18n import _

from ..blossom.store import MediaFile
from .thumbnail_loader import write_problem

# Reasons another server may still have the right file.
_NEXT_SERVER = ("network", "missing", "server", "wrong_file")


@dataclass
class _Save:
    sha: str
    size: int
    target: str
    urls: List[str]
    # A private file's envelope to the file; None for a public one.
    opener: Optional[Callable[[bytes], Optional[bytes]]]
    # A file found at the target stays, and this one takes a free name.
    keep_existing: bool


class MediaSaver(QObject):
    """Saves Media Library files where the person chose, in turn."""

    started = Signal(str)            # the path being saved
    progress = Signal(int)           # percent of it
    # The path, and "" when it is saved, "cancelled", or why it wasn't
    # (a FileDownload reason, or "unreadable"): save_failure says it.
    finished = Signal(str, str)

    def __init__(self, loader, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._loader = loader
        self._queue: List[_Save] = []
        self._download = None
        self._percent = 0

    @property
    def current(self) -> str:
        """The path being downloaded to, "" when nothing is."""
        return self._queue[0].target if self._download is not None else ""

    @property
    def percent(self) -> int:
        return self._percent

    def save(self, media: MediaFile, target: str, *,
             opener: Optional[Callable[[bytes], Optional[bytes]]] = None,
             keep_existing: bool = False) -> None:
        """Save ``media`` at ``target``, after the saves before it.

        ``keep_existing`` (Save to Folder, where the names are the
        library's): a file of that name stays, now or when this one is
        written, and this one is numbered as the Finder numbers a copy.
        Either way a name an earlier save is still going to is never
        taken: the person was not asked to replace a file not there yet.
        """
        taken = {save.target for save in self._queue}
        if keep_existing or target in taken:
            target, keep_existing = free_path(target, taken), True
        urls = [u.get("url") for u in media.urls if u.get("url")] or [media.url]
        self._queue.append(_Save(media.hash, media.size, target, urls, opener, keep_existing))
        self._next()

    def cancel(self) -> None:
        """Stop the save running and drop the ones waiting."""
        if self._download is None:
            self._queue = []
            return
        self._queue = self._queue[:1]
        self._download.cancel()

    def _next(self) -> None:
        while self._queue and self._download is None:
            save = self._queue[0]
            cached = self._loader.cache_path(save.sha)
            if cached.is_file():
                self._queue.pop(0)
                self.started.emit(save.target)
                why = self._save_cached(save, cached)
                self.finished.emit(save.target, why)
                continue
            self._percent = 0
            self._download = self._loader.download_to(
                save.urls[0], save.sha, save.target, size=save.size, transform=save.opener,
                keep_existing=save.keep_existing)
            self._download.progress.connect(self._on_progress)
            self._download.finished.connect(self._on_finished)
            self.started.emit(save.target)

    @staticmethod
    def _save_cached(save: _Save, cached) -> str:
        try:
            data = cached.read_bytes()
        except OSError:
            data = b""
        if hashlib.sha256(data).hexdigest() != save.sha:
            data = b""
        if data and save.opener is not None:
            data = save.opener(data) or b""
        if not data:
            return "unreadable"
        if save.keep_existing:
            save.target = free_path(save.target)
        try:
            save_document(save.target, data)
        except OSError as exc:
            return write_problem(exc)
        return ""

    def _on_progress(self, received: int, total: int) -> None:
        if total > 0:
            self._percent = min(100, received * 100 // total)
            self.progress.emit(self._percent)

    def _on_finished(self, why: str) -> None:
        save = self._queue[0]
        save.target = os.fspath(self._download.target)
        self._download.deleteLater()
        self._download = None
        if why in _NEXT_SERVER and len(save.urls) > 1:
            save.urls = save.urls[1:]
            self._next()
            return
        self._queue.pop(0)
        self.finished.emit(save.target, why)
        self._next()


def save_failure(why: str, path: str) -> str:
    """Why the file saved at ``path`` wasn't, in a sentence."""
    said = {
        "network": _("“{name}” couldn’t be downloaded. Check your internet connection "
                     "and try again."),
        "missing": _("“{name}” isn’t on your media servers anymore, so it couldn’t be "
                     "saved."),
        "server": _("“{name}” couldn’t be downloaded: your media server didn’t send it. "
                    "Try again later."),
        "too_large": _("“{name}” wasn’t saved: a media server sent more than the file."),
        "wrong_file": _("“{name}” wasn’t saved: a media server returned the wrong file."),
        "unsafe": _("“{name}” wasn’t saved: a media server sent it from an address "
                    "MyEditor doesn’t trust."),
        "full": _("“{name}” couldn’t be saved: there isn’t enough space for it there. "
                  "Free up some space or choose another folder."),
        "unreadable": _("“{name}” couldn’t be saved. Try again."),
    }.get(why) or _("“{name}” couldn’t be saved there. Choose another folder.")
    return said.format(name=os.path.basename(path))
