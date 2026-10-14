# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Platform file-type icons, without downloading or opening a user's file."""

from PySide6.QtCore import QDir, QFile, QFileInfo, QIODevice, QMimeDatabase, QTemporaryDir
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFileIconProvider

import media_files


class FileIcons:
    """Cache type lookups for a view; callers draw a family glyph if unavailable.

    Some native providers require an existing file. Empty placeholders in an
    automatically removed temporary directory supply only a suffix, never a
    user's filename or decrypted bytes. Desktop themes supply MIME icons when
    a native icon is unavailable.
    """

    def __init__(self):
        self._provider = QFileIconProvider()
        self._directory = QTemporaryDir(QDir.tempPath() + "/myeditor-file-icons-XXXXXX")
        self._generic = self._native_icon("").pixmap(64, 64).toImage()
        self._mime_database = QMimeDatabase()
        self._icons = {}

    def icon(self, name: str, mime: str) -> QIcon:
        suffix = media_files.file_extension(name, mime)
        key = (suffix, mime)
        if key not in self._icons:
            icon = self._native_icon(suffix)
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

    def _native_icon(self, suffix: str) -> QIcon:
        if not self._directory.isValid():
            return QIcon()
        path = self._directory.filePath("file" + suffix)
        placeholder = QFile(path)
        if not placeholder.open(QIODevice.OpenModeFlag.WriteOnly):
            return QIcon()
        placeholder.close()
        return self._provider.icon(QFileInfo(path))
