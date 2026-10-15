# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The alert that offers dictation on this computer, and downloads its
speech model when the person chooses to (models.py).

The app's alert (alerts.py), as an Apple alert asks before it downloads:
what it needs and how much, that it works without the internet afterwards,
and that the voice never leaves the computer; Not Now or Download. The
download's progress shows in the same alert, with Cancel; when it is done
the alert closes and dictation starts. One that fails says why and offers
Try Again. The download runs on a thread of its own.
"""

from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QProgressBar

import verified_download
from alerts import CANCEL, DEFAULT, Alert, Button
from i18n import _

from . import models

NOT_NOW, DOWNLOAD, INSTALLED = "not now", "download", "installed"


def size_text(size: int) -> str:
    """A download's size, in megabytes as the system counts them."""
    return _("{size} MB").format(size=round(size / 1_000_000))


class _Installer(QThread):
    progress = Signal(int, int)
    failed = Signal(str)

    def __init__(self, model: models.Model, install: Callable, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self._install = install
        self.cancel = threading.Event()

    def run(self) -> None:
        try:
            self._install(self._model, progress=self.progress.emit, cancel=self.cancel)
        except verified_download.Cancelled:
            pass
        except verified_download.DownloadError:
            self.failed.emit(_("The downloaded model didn’t match the one published, so it "
                               "wasn’t kept. Try again."))
        except OSError:
            self.failed.emit(_("The download didn’t finish. Check the internet connection "
                               "and try again."))


class SetupAlert(Alert):
    """Offers ``model`` and downloads it with ``install`` (models.install).
    Its value: INSTALLED, or NOT_NOW."""

    def __init__(self, parent, model: models.Model, *, install: Callable = models.install):
        self._not_now, self._download = _("Not Now"), _("Download")
        super().__init__(
            parent, title=_("Dictate on this computer?"),
            message=_("To write what you say, MyEditor needs a speech model ({size}). It is "
                      "downloaded once and then works without the internet. Your voice never "
                      "leaves this computer.").format(size=size_text(models.download_size(model))),
            buttons=(Button(self._not_now, NOT_NOW, CANCEL),
                     Button(self._download, DOWNLOAD, DEFAULT)))
        self._model = model
        self._install = install
        self._installer = None
        self.progress = QProgressBar(self)
        self.progress.setAccessibleName(_("Download progress"))
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout = self.layout()
        layout.insertWidget(layout.indexOf(self.button_row) - 1, self.progress)

    def _choose(self, value) -> None:
        if value == DOWNLOAD:
            self._start()
        elif self._installer is not None:
            self._installer.cancel.set()      # it ends, and closes the alert (_ended)
        else:
            super()._choose(value)

    def reject(self) -> None:
        # Escape, and closing the alert, cancel a download under way.
        if self._installer is not None:
            self._installer.cancel.set()
        else:
            super().reject()

    def _start(self) -> None:
        self.message_label.setText(_("Downloading the speech model…"))
        self.progress.setRange(0, 0)               # until the size is known
        self.progress.show()
        self.buttons[self._download].setEnabled(False)
        self.buttons[self._not_now].setText(_("Cancel"))
        self._failure = ""
        self._installer = _Installer(self._model, self._install, self)
        self._installer.progress.connect(self._advanced)
        self._installer.failed.connect(self._failed)
        self._installer.finished.connect(self._ended)
        self._installer.start()

    def _advanced(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)

    def _failed(self, why: str) -> None:
        self._failure = why

    def _ended(self) -> None:
        installer, self._installer = self._installer, None
        if installer.cancel.is_set():
            super()._choose(NOT_NOW)
        elif self._failure:
            self.message_label.setText(self._failure)
            self.progress.hide()
            self.buttons[self._download].setText(_("Try Again"))
            self.buttons[self._download].setEnabled(True)
            self.buttons[self._not_now].setText(self._not_now)
        else:
            super()._choose(INSTALLED)
