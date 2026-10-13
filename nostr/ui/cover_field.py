# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""An article's Cover Image field: its address, a picture chosen or
uploaded through the Media Library, and a small preview.

One field for the publish window and Article Details, so a cover is
chosen the same way in both:

    [thumbnail]   COVER IMAGE
                  [address_________________] [Choose or upload…]

The thumbnail leads, so the eye lands on the picture first; the field
stacks under it where the room is narrow (the inspector). A picture kept
private cannot be a cover as it stands, since its bytes are ciphertext:
choosing one makes a public copy first (publish_copy_dialog.resolve_pick),
or leaves the field as it was and says why. A typed address shows no
preview: downloading any address typed would be a surprise.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from i18n import _

from ..blossom.store import MediaFile, MediaStore
from ..media.media_visibility import MediaVisibility
from ..media.private_library import PrivateLibrary
from ..media.publish_copy import PublicCopyMaker
from ..profiles import Profile
from .media_library_dialog import MediaLibraryDialog
from .publish_copy_dialog import resolve_pick
from .thumbnail_loader import ThumbnailLoader

THUMB = QSize(144, 84)


class CoverImageField(QWidget):
    """The cover's address, with Choose or Upload and a preview; ``problem``
    says why a chosen picture could not become the cover."""

    changed = Signal(str)
    problem = Signal(str)

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
        self._thumb_hash = ""
        self._loader: Optional[ThumbnailLoader] = (
            ThumbnailLoader(parent=self) if media_store is not None else None)
        if self._loader is not None:
            self._loader.ready.connect(self._on_thumb_ready)

        self.url_edit = QLineEdit()
        self.url_edit.setObjectName("article_advanced_field")
        self.url_edit.setPlaceholderText("https://example.com/cover.png")
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.setAccessibleName(_("Cover image"))
        self.url_edit.textChanged.connect(self._on_url_changed)

        row = QBoxLayout(QBoxLayout.Direction.TopToBottom if stacked
                         else QBoxLayout.Direction.LeftToRight, self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8 if stacked else 12)
        self.preview = QLabel(_("No cover\nselected"))
        self.preview.setObjectName("article_cover_preview")
        self.preview.setFixedSize(THUMB)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignTop)

        controls = QVBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        label = QLabel(_("Cover image"))
        label.setObjectName("article_field_label")
        label.setBuddy(self.url_edit)
        controls.addWidget(label)
        # Beside the address in a dialog; in a narrow inspector under it, as
        # Pages stacks its controls, so the address keeps the full width.
        input_row = QBoxLayout(QBoxLayout.Direction.TopToBottom if stacked
                               else QBoxLayout.Direction.LeftToRight)
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(6)
        input_row.addWidget(self.url_edit, 1)
        self.pick_button: Optional[QPushButton] = None
        if media_store is not None:
            self.pick_button = QPushButton(_("Choose or upload…"))
            self.pick_button.setAutoDefault(False)
            self.pick_button.clicked.connect(self.choose)
            input_row.addWidget(self.pick_button, 0,
                                Qt.AlignmentFlag.AlignLeft if stacked else Qt.AlignmentFlag(0))
        controls.addLayout(input_row)
        hint = QLabel(_("Pictures you pick are uploaded to your media servers.")
                      if media_store is not None
                      else _("Paste an image URL hosted anywhere on the public web."))
        hint.setObjectName("article_field_hint")
        hint.setWordWrap(True)
        controls.addWidget(hint)
        self._hint = hint
        controls.addStretch(1)
        row.addLayout(controls, 1)

    def text(self) -> str:
        return self.url_edit.text()

    def setText(self, text: str) -> None:
        self.url_edit.setText(text)

    def setReadOnly(self, read_only: bool) -> None:
        self.url_edit.setReadOnly(read_only)
        if self.pick_button is not None:
            self.pick_button.setEnabled(not read_only)
        self._hint.setVisible(not read_only)    # how to pick one: nothing to pick

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
        picker.setWindowTitle(_("Choose hero image"))
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
        # Setting the text clears the old preview; the new one then loads.
        self.url_edit.setText(resolved.url)
        self._thumb_hash = resolved.sha256
        if self._loader is not None and (resolved.mime or "").startswith("image/"):
            self._loader.load(resolved.sha256, resolved.url)

    # -- the preview ----------------------------------------------------------

    def _on_url_changed(self, text: str) -> None:
        if not text.strip():
            self._clear_preview(_("No cover\nselected"))
        elif not self._thumb_hash:
            # Typed, with no picture of ours behind it: no preview, said so.
            self._clear_preview(_("Preview shown\nfor library picks"))
        self.changed.emit(text)

    def _clear_preview(self, placeholder: str) -> None:
        self._thumb_hash = ""
        self.preview.setPixmap(QPixmap())
        self.preview.setText(placeholder)

    def _on_thumb_ready(self, sha: str, _path: str, pix: QPixmap) -> None:
        if sha != self._thumb_hash or pix.isNull():
            return
        self.preview.setPixmap(pix.scaled(
            THUMB.width() - 2, THUMB.height() - 2,
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.preview.setText("")
