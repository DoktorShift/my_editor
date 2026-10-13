# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""An article's cover image: a picture well, Choose…, Remove, and the
cover's web address. One field for Publish as Article and Article
Details, so a cover is chosen the same way in both.

In the publish window the well leads, with its buttons beside it; the
address is the window's to place (under Show Details):

    [ picture ]   Choose…
                  Remove

In the narrow inspector everything stacks, as Pages stacks its controls:
the well, the address, then the buttons.

Choose… is the Media Library's picker, which also uploads. A picture
kept private cannot be a cover as it stands, since its bytes are
ciphertext: choosing one makes a public copy first
(publish_copy_dialog.resolve_pick), or leaves the cover as it was and
says why. A picture file dropped on the well is uploaded to the media
servers as a public picture, with what it reveals besides its pixels
(where and when it was taken) taken out first (media/scrub.py), and
becomes the cover once it is there. Remove takes the cover away.

The well shows the picture of a cover from the account's media library;
an address typed or pasted shows no preview, since downloading any
address typed would be a surprise.
"""

from __future__ import annotations

import os
import uuid
from typing import Callable, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

import media_files
from i18n import _

from ..blossom.hashes import hash_from_url
from ..blossom.store import MediaFile, MediaStore
from ..media.media_visibility import MediaVisibility
from ..media.private_library import PrivateLibrary
from ..media.publish_copy import PublicCopyMaker
from ..media.scrub import ScrubError, scrub_for_publication
from ..profiles import Profile
from .media_library_dialog import MediaLibraryDialog
from .publish_copy_dialog import resolve_pick
from .thumbnail_loader import ThumbnailLoader

THUMB = QSize(144, 84)


class _Well(QLabel):
    """The picture, where a picture file can be dropped."""

    dropped = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    @staticmethod
    def _picture_path(event) -> str:
        for url in event.mimeData().urls() if event.mimeData().hasUrls() else ():
            if url.isLocalFile():
                return url.toLocalFile()
        return ""

    def dragEnterEvent(self, event) -> None:  # noqa: N802, Qt's name
        if self.isEnabled() and self._picture_path(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802, Qt's name
        path = self._picture_path(event)
        if not self.isEnabled() or not path:
            event.ignore()
            return
        event.acceptProposedAction()
        self.dropped.emit(path)


class CoverImageField(QWidget):
    """The cover (see the module docstring). ``changed(address)`` as it
    changes, ``picture_changed`` when the well's picture comes or goes,
    and ``problem(reason)`` says why a picture could not become the cover."""

    changed = Signal(str)
    problem = Signal(str)
    picture_changed = Signal()
    uploading_changed = Signal(bool)

    def __init__(self, *, media_store: Optional[MediaStore] = None,
                 media_visibility: Optional[MediaVisibility] = None,
                 copy_maker: Optional[PublicCopyMaker] = None,
                 private_library: Optional[PrivateLibrary] = None,
                 profile: Callable[[], Optional[Profile]] = lambda: None,
                 is_dark: bool = False, stacked: bool = False,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._media_store = media_store
        self._media_visibility = media_visibility or MediaVisibility()
        self._copy_maker = copy_maker
        self._private_library = private_library
        self._profile = profile
        self._is_dark = is_dark
        self._read_only = False
        self._thumb_hash = ""
        self._picture = QPixmap()
        # The dropped picture being uploaded (its upload's name), if any.
        self._uploading = ""
        self._loader: Optional[ThumbnailLoader] = (
            ThumbnailLoader(parent=self) if media_store is not None else None)
        if self._loader is not None:
            self._loader.ready.connect(self._on_thumb_ready)
        if media_store is not None:
            media_store.upload_finished.connect(self._on_uploaded)
            media_store.upload_failed.connect(self._on_upload_failed)
            media_store.upload_progress.connect(self._on_upload_progress)

        self.url_edit = QLineEdit()
        self.url_edit.setObjectName("article_advanced_field")
        self.url_edit.setPlaceholderText("https://example.com/cover.png")
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.setAccessibleName(_("Cover image"))
        self.url_edit.textChanged.connect(self._on_url_changed)

        self.preview = _Well()
        self.preview.setObjectName("article_cover_preview")
        self.preview.setFixedSize(THUMB)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setAccessibleName(_("Cover image"))
        self.preview.dropped.connect(self.drop)
        if media_store is not None:
            self.preview.setToolTip(_("Drop a picture here to upload it as the cover."))

        self.pick_button: Optional[QPushButton] = None
        if media_store is not None:
            self.pick_button = QPushButton(_("Choose…"))
            self.pick_button.setAccessibleName(_("Choose Cover Image"))
            self.pick_button.setToolTip(_("Choose a picture from your Media Library, or "
                                          "upload one."))
            self.pick_button.setAutoDefault(False)
            self.pick_button.clicked.connect(self.choose)
        self.remove_button = QPushButton(_("Remove"))
        self.remove_button.setAccessibleName(_("Remove Cover Image"))
        self.remove_button.setAutoDefault(False)
        self.remove_button.clicked.connect(self.url_edit.clear)

        buttons = [button for button in (self.pick_button, self.remove_button) if button]
        if stacked:
            column = QVBoxLayout(self)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(6)
            column.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignLeft)
            label = QLabel(_("Cover image"))
            label.setObjectName("article_field_label")
            label.setBuddy(self.url_edit)
            column.addWidget(label)
            column.addWidget(self.url_edit)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            for button in buttons:
                row.addWidget(button)
            row.addStretch(1)
            column.addLayout(row)
            hint = QLabel(_("Pictures you pick are uploaded to your media servers.")
                          if media_store is not None
                          else _("Paste an image URL hosted anywhere on the public web."))
            hint.setObjectName("article_field_hint")
            hint.setWordWrap(True)
            column.addWidget(hint)
        else:
            row = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(12)
            row.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignTop)
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(6)
            for button in buttons:
                column.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
            column.addStretch(1)
            row.addLayout(column, 1)
            # The address is the window's to place; until then it is not shown.
            self.url_edit.hide()
        self._show_placeholder(_("No cover\nselected"))
        self.remove_button.setVisible(False)

    def text(self) -> str:
        return self.url_edit.text()

    def setText(self, text: str) -> None:  # noqa: N802, Qt's name
        self.url_edit.setText(text)

    def uploading(self) -> bool:
        """Whether a picture dropped on the well is on its way up: the
        cover it will be is not there yet."""
        return bool(self._uploading)

    def picture(self) -> QPixmap:
        """The cover's picture, as large as it was loaded; null when none
        is shown."""
        return self._picture

    def setReadOnly(self, read_only: bool) -> None:  # noqa: N802, Qt's name
        self._read_only = read_only
        self.url_edit.setReadOnly(read_only)
        self.preview.setEnabled(not read_only)
        if self.pick_button is not None:
            self.pick_button.setEnabled(not read_only)
        self.remove_button.setEnabled(not read_only)

    def set_dark(self, is_dark: bool) -> None:
        self._is_dark = is_dark

    # -- choosing a picture -------------------------------------------------

    def choose(self) -> None:
        """The Media Library's picker, showing pictures, without the
        alternative text row (a cover has none on Nostr)."""
        if self._media_store is None:
            return
        # Reading the private library is what lets the picker tell a
        # private file from a public one; it starts here, when someone
        # goes looking for a picture, not on the way into the window.
        if self._private_library is not None:
            self._private_library.bind_profile(self._profile())
        picker = MediaLibraryDialog(store=self._media_store, is_dark=self._is_dark,
                                    pick_mode=True, pick_alt_text=False,
                                    visibility=self._media_visibility, parent=self)
        if self._private_library is not None:
            picker.bind_private_library(self._private_library)
        picker.setWindowTitle(_("Choose Cover Image"))
        # Pictures only: audio and video can't be a cover.
        picker._filter_combo.setCurrentIndex(1)
        picker.file_picked.connect(self.picked)
        picker.exec()

    def picked(self, media: MediaFile, _alt: str = "") -> None:
        """``media`` chosen: its public address, or nothing and why."""
        resolved = resolve_pick(media, visibility=self._media_visibility,
                                maker=self._copy_maker, is_dark=self._is_dark, parent=self)
        if not resolved.ok:
            if resolved.reason:
                self.problem.emit(resolved.reason)
            return
        self.url_edit.setText(resolved.url)
        sha = resolved.sha256.lower()
        if sha != self._thumb_hash:
            self._thumb_hash = sha
            if self._loader is not None and (resolved.mime or "").startswith("image/"):
                self._loader.load(resolved.sha256, resolved.url)

    def drop(self, path: str) -> None:
        """A picture file dropped on the well: uploaded as a public
        picture, without what it reveals besides its pixels, and the cover
        once it is on the media servers."""
        if self._read_only:
            return
        if self._media_store is None:
            self.problem.emit(_("A picture can be uploaded once you use a Nostr account with "
                                "media servers. Paste its web address instead."))
            return
        if not (media_files.type_of(path) or "").startswith("image/"):
            self.problem.emit(_("Only a picture can be a cover."))
            return
        try:
            with open(path, "rb") as file:
                data = file.read()
            public = scrub_for_publication(data, media_files.type_of(path) or "")
        except OSError as exc:
            self.problem.emit(_("Could not read file: {error}").format(
                error=exc.strerror or exc))
            return
        except ScrubError as exc:
            self.problem.emit(_("This picture can’t be a cover: {reason}.").format(reason=exc))
            return
        # The upload is known by a name of its own: another upload of a
        # file of the same name (from the Media Library) is not this one.
        stem, extension = os.path.splitext(os.path.basename(path) or "cover")
        self._uploading = f"{stem}-{uuid.uuid4().hex[:8]}{extension}"
        self._show_placeholder(_("Uploading…"))
        self.uploading_changed.emit(True)
        self._media_store.upload_bytes(public.data, name=self._uploading,
                                       mime_type=public.mime)

    def _on_upload_progress(self, name: str, sent: int, total: int) -> None:
        if self._uploading and name == self._uploading and total > 0:
            percent = min(100, sent * 100 // total)
            self.preview.setText(_("Uploading…\n{percent} %").format(percent=percent))

    def _on_uploaded(self, name: str, media: MediaFile) -> None:
        if not self._uploading or name != self._uploading:
            return
        self._uploading = ""
        # Uploaded here, from the picture's own pixels: public by making.
        self.url_edit.setText(media.url)
        if self._loader is not None:
            self._thumb_hash = media.hash.lower()
            self._loader.load(media.hash, media.url)
        self.uploading_changed.emit(False)

    def _on_upload_failed(self, name: str, reason: str) -> None:
        if not self._uploading or name != self._uploading:
            return
        self._uploading = ""
        self._on_url_changed(self.url_edit.text(), quiet=True)
        self.uploading_changed.emit(False)
        self.problem.emit(_("The picture was not uploaded: {reason}").format(reason=reason))

    # -- the well -------------------------------------------------------------

    def _on_url_changed(self, text: str, *, quiet: bool = False) -> None:
        if self._uploading and not quiet:
            # A cover typed, pasted, picked or removed while a dropped
            # picture uploads: that one is the cover, not the upload.
            self._uploading = ""
            self.uploading_changed.emit(False)
        address = text.strip()
        sha = (hash_from_url(address) or "").lower() if address else ""
        self.remove_button.setVisible(bool(address))
        if not address:
            self._show_placeholder(_("No cover\nselected"))
        elif sha and sha == self._thumb_hash and not self._picture.isNull():
            pass                                   # this picture is shown
        elif (sha and self._loader is not None and self._media_store is not None
              and sha in self._media_store.files):
            # A picture of the library: its preview, from the library's copy.
            self._show_placeholder("")
            self._thumb_hash = sha
            self._loader.load(sha, address)
        else:
            # Typed, with no picture of ours behind it: no preview, said so.
            self._show_placeholder(_("Preview shown\nfor library picks"))
        if not quiet:
            self.changed.emit(text)

    def _show_placeholder(self, words: str) -> None:
        self._thumb_hash = ""
        had_picture = not self._picture.isNull()
        self._picture = QPixmap()
        self.preview.setPixmap(QPixmap())
        self.preview.setText(words)
        if had_picture:
            self.picture_changed.emit()

    def _on_thumb_ready(self, sha: str, _path: str, pix: QPixmap) -> None:
        if sha.lower() != self._thumb_hash or pix.isNull():
            return
        self._picture = pix
        self.preview.setPixmap(pix.scaled(
            THUMB.width() - 2, THUMB.height() - 2,
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.preview.setText("")
        self.picture_changed.emit()
