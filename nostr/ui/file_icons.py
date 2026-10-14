# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Platform file-type icons, without downloading or opening a user's file."""

from PySide6.QtCore import QFileInfo, QMimeDatabase
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFileIconProvider

import media_files


class FileIcons:
    """Cache type lookups for a view; callers draw a family glyph if unavailable.

    Use a synthetic filename with just the suffix, never a user's path or
    decrypted bytes. The native provider uses the system's file associations;
    desktop icon themes supply MIME icons on platforms with an icon theme.
    """

    def __init__(self):
        self._provider = QFileIconProvider()
        self._generic = self._provider.icon(QFileInfo("myeditor-file-type")).pixmap(64, 64).toImage()
        self._mime_database = QMimeDatabase()
        self._icons = {}

    def icon(self, name: str, mime: str) -> QIcon:
        suffix = media_files.file_extension(name, mime)
        key = (suffix, mime)
        if key not in self._icons:
            icon = self._provider.icon(QFileInfo("myeditor-file-type" + suffix))
            # Some platforms return a blank page for an unassociated suffix.
            # Prefer a meaningful family glyph over that generic system icon.
            if not self._generic.isNull() and icon.pixmap(64, 64).toImage() == self._generic:
                icon = QIcon()
            if icon.isNull():
                known = self._mime_database.mimeTypeForName(mime)
                icon = QIcon.fromTheme(known.iconName())
                if icon.isNull():
                    icon = QIcon.fromTheme(known.genericIconName())
            self._icons[key] = icon
        return self._icons[key]
