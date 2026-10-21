# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Quick Look: a file shown in a floating panel, as the Finder shows it.

Space in the file list shows the file selected in a panel that floats
over the window without taking the keyboard: the arrow keys go on moving
through the list, and the panel follows; Space again or Escape closes it.
Nothing in it can be edited. It holds the viewer a file's tab would
(viewer_tabs.viewer_for: a picture, a film, sound, an office document),
the PDF reader, a spreadsheet shown read-only, or a note or text file as
its text, in the app's reading font.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QLabel, QTextBrowser, QVBoxLayout, QWidget

import file_kinds
import regular_files
from i18n import _

log = logging.getLogger(__name__)

# Larger notes are shown as far as this, in characters.
TEXT_LIMIT = 200_000


def _text_preview(path: str) -> QWidget:
    """A note shown as it reads (its Markdown formatted), or a text file
    as its text."""
    view = QTextBrowser()
    view.setOpenLinks(False)
    view.setAccessibleName(_("Preview"))
    # Only a regular file is read (a pipe with a note's name would hold
    # the window), and only as far as is shown.
    data = regular_files.read_regular_prefix(path, TEXT_LIMIT * 4)
    if data is None:
        text = _("This file can’t be shown.")
    else:
        text = data.decode("utf-8", "replace")[:TEXT_LIMIT]
    if file_kinds.kind_of_path(path) == file_kinds.NOTE:
        view.setMarkdown(text)
    else:
        view.setPlainText(text)
    return view


def preview_for(path: str, is_dark: bool) -> QWidget:
    """What Quick Look shows for the file at ``path``."""
    kind = file_kinds.kind_of_path(path)
    if kind in (file_kinds.NOTE, file_kinds.TEXT):
        return _text_preview(path)
    if kind == file_kinds.PDF:
        from pdf_viewer import PdfViewerTab
        return PdfViewerTab(path, is_dark=is_dark)
    if kind == file_kinds.SHEET and file_kinds.extension(path) in file_kinds.EDITABLE_SHEET_EXTS:
        import sheet_tab
        sheet = sheet_tab.SheetTab(path, is_dark=is_dark)
        sheet.set_view_only()
        return sheet
    import viewer_tabs
    viewer = viewer_tabs.viewer_for(path, is_dark)
    if viewer is None:
        return _text_preview(path)
    return viewer


class QuickLookPanel(QWidget):
    """The floating panel. ``show_path`` shows a file in it (replacing
    what it showed), ``close`` puts it away; ``closed`` says it went."""

    closed = Signal()
    # A new content widget, made just now: the window wires what it asks
    # for (a viewer's Open, Open With and reveal, its app's name) as it
    # does a tab's.
    content_created = Signal(QWidget)

    def __init__(self, parent: Optional[QWidget] = None, *, is_dark: bool = False) -> None:
        super().__init__(parent, Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAccessibleName(_("Quick Look"))
        self.is_dark = is_dark
        self._path: Optional[str] = None
        self._content: Optional[QWidget] = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._empty = QLabel()
        self._empty.setWordWrap(True)
        self._layout.addWidget(self._empty)
        for keys in (QKeySequence(Qt.Key.Key_Escape), QKeySequence(Qt.Key.Key_Space)):
            shortcut = QShortcut(keys, self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(self.close)
        self.resize(760, 560)

    def path(self) -> Optional[str]:
        """The file shown, while the panel is up."""
        return self._path if self.isVisible() else None

    def content(self) -> Optional[QWidget]:
        return self._content

    def show_path(self, path: str) -> None:
        """Show ``path`` in the panel, and the panel."""
        if path == self._path and self._content is not None and self.isVisible():
            return
        self._drop_content()
        self._path = path
        self.setWindowTitle(os.path.basename(path.rstrip("/\\")))
        try:
            self._content = preview_for(path, self.is_dark)
        except Exception as error:                      # noqa: BLE001, shown, not raised
            log.warning("Quick Look could not show %s: %s", path, error)
            self._content = None
            self._empty.setText(_("This file can’t be shown."))
        self._empty.setVisible(self._content is None)
        if self._content is not None:
            self._layout.addWidget(self._content, 1)
            self.content_created.emit(self._content)
        if not self.isVisible():
            self._place()
            self.show()

    def _place(self) -> None:
        """Over the middle of the window it belongs to."""
        owner = self.parentWidget()
        if owner is None:
            return
        middle = owner.frameGeometry().center()
        self.move(middle.x() - self.width() // 2, middle.y() - self.height() // 2)

    def _drop_content(self) -> None:
        content, self._content = self._content, None
        if content is None:
            return
        release = getattr(content, "release", None)
        if callable(release):
            release()
        content.hide()
        content.setParent(None)
        content.deleteLater()

    def update_theme(self, is_dark: bool) -> None:
        self.is_dark = is_dark
        update = getattr(self._content, "update_theme", None)
        if callable(update):
            update(is_dark)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt's name)
        self._drop_content()
        self._path = None
        super().closeEvent(event)
        self.closed.emit()
