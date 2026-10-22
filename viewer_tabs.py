# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only tabs for the files a folder holds besides notes and PDFs.

Every viewer is a sibling of the PDF reader (pdf_viewer.PdfViewerTab):
the same contract for the window (``_file_path``, ``load_ok``,
``load_error``, ``save_view_state``, ``release``, ``schedule_reload``,
``update_theme``, ``document_title``) and the same slim bar on top, in
the same greys. The bar says what the file is (its kind's icon, its
name, a few facts) and offers only what makes sense for it: Insert into
Note for pictures, films and sound, Import as Note for Word and Writer
documents, "Open in {app}" with a menu of other apps, and the button
that shows the file in the Finder (or Explorer, or the file manager).

The tabs never act on their own; they say what was asked with a signal
and the window does it (default_apps.py opens and reveals, the note
inserts, office_preview.import_as_markdown imports).

- FileInfoTab: a calm card for files MyEditor shows and sorts but does
  not read, with the app that edits them.
- PackagePreviewTab: the preview picture an office app stores in its
  file (Numbers, Pages, Keynote, PowerPoint, Impress), else the card.
- DocumentReadingTab: a reading view of a .docx or .odt.
- ImageViewerTab and MediaPlayerTab live in media_tabs.py.

``viewer_for(path)`` picks the tab for a file, or None for the files
other tabs own (notes and text, PDFs, spreadsheets MyEditor edits).
"""

from __future__ import annotations

import html
import logging
import os
import shutil
import tempfile
import threading

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QDesktopServices, QIcon, QImage, QPainter, QPainterPath, QPalette, QPen, QPixmap,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QStackedWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

import background
import file_kinds
import format_icons
import i18n
import image_safety
import media_files
import theme
from constants import (
    DARK_BG, DARK_BORDER, DARK_FG, DARK_MENU_BG, DARK_MUTED_FG, DARK_PAPER,
    LIGHT_BG, LIGHT_BORDER, LIGHT_FG, LIGHT_MUTED_FG, LIGHT_PAPER,
)
from i18n import _
from url_safety import is_safe_external_url
from widgets import ElidedLabel

_log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# What kind of file (file_kinds.py names every kind and format)               #
# --------------------------------------------------------------------------- #

# Files whose app may store a preview picture in them.
_PREVIEW_PICTURE_EXTS = {".numbers", ".pages", ".key", ".pptx", ".odp", ".xls", ".doc", ".ppt"}

def _file_size(path: str) -> int:
    """Bytes of a file, or of everything in a package folder (a Numbers,
    Pages or Keynote document saved as a folder), counted over a limited
    number of its entries, folders included (file_kinds.package_size)."""
    try:
        if not os.path.isdir(path):
            return os.path.getsize(path)
        return file_kinds.package_size(path)
    except OSError:
        return 0


def _modified_day(path: str) -> str:
    from PySide6.QtCore import QDateTime
    try:
        moment = QDateTime.fromSecsSinceEpoch(int(os.path.getmtime(path)))
    except (OSError, OverflowError, ValueError):
        return ""
    return i18n.format_day(moment)


# --------------------------------------------------------------------------- #
# Colors and icons                                                            #
# --------------------------------------------------------------------------- #

def colors(is_dark: bool) -> dict:
    """The viewers' colors: the PDF reader's toolbar greys, its surface
    around the page, and the paper of Paper Mode for the reading view."""
    if is_dark:
        return {"bar": DARK_MENU_BG, "line": DARK_BORDER, "text": "#CCCCCC", "fg": DARK_FG,
                "muted": DARK_MUTED_FG, "hover": "#3C3C3C", "viewer": "#1E1E1E",
                "card": DARK_MENU_BG, "field": DARK_BG, "paper": DARK_PAPER.name(),
                "dot": "#FF9F0A", "shadow": QColor(0, 0, 0, 110)}
    return {"bar": "#F8F8F8", "line": LIGHT_BORDER, "text": "#333333", "fg": LIGHT_FG,
            "muted": LIGHT_MUTED_FG, "hover": "#E1E1E1", "viewer": "#E8E8E8",
            "card": LIGHT_BG, "field": LIGHT_BG, "paper": LIGHT_PAPER.name(),
            "dot": "#E07B00", "shadow": QColor(0, 0, 0, 40)}


def _bar_css(is_dark: bool) -> str:
    c = colors(is_dark)
    ring = theme.focus_ring_color(is_dark)
    on_checked = theme.focus_ring_on_accent_color(is_dark)
    checked = ("background: #264F78; color: #FFFFFF; border-color: #3794FF;" if is_dark
               else "background: #DCEBFA; color: #0B4F8A; border-color: #0078D4;")
    return f"""
    #ViewerBar {{ background: {c['bar']}; border-bottom: 1px solid {c['line']}; }}
    QLabel {{ color: {c['text']}; font-size: 12px; background: transparent; }}
    QLabel#ViewerName {{ font-weight: 600; }}
    QLabel#ViewerFacts {{ color: {c['muted']}; }}
    QToolButton {{
        background: transparent; color: {c['text']};
        border: 1px solid transparent; border-radius: 4px;
        padding: 3px 8px; font-size: 12px;
    }}
    QToolButton:hover {{ background: {c['hover']}; }}
    QToolButton:checked {{ {checked} }}
    QToolButton#ViewerPill {{ border-color: {c['line']}; border-radius: 6px; }}
    QToolButton#ViewerSplitMain {{
        border-color: {c['line']}; border-top-left-radius: 6px; border-bottom-left-radius: 6px;
        border-top-right-radius: 0; border-bottom-right-radius: 0;
    }}
    QToolButton#ViewerSplitMenu {{
        border-color: {c['line']}; border-top-right-radius: 6px;
        border-bottom-right-radius: 6px; border-top-left-radius: 0;
        border-bottom-left-radius: 0; padding: 3px 3px;
    }}
    QToolButton:focus {{ border: 2px solid {ring}; padding: 2px 7px; }}
    QToolButton#ViewerSplitMenu:focus {{ padding: 2px 2px; }}
    QToolButton:checked:focus {{ border-color: {on_checked}; }}
    """


def _info_css(is_dark: bool) -> str:
    """The quiet line under the bar (a reading view's note, a lock notice)."""
    c = colors(is_dark)
    ring = theme.focus_ring_color(is_dark)
    return f"""
    #ViewerInfo {{ background: {c['field']}; border-bottom: 1px solid {c['line']}; }}
    QLabel {{ color: {c['muted']}; font-size: 12px; background: transparent; }}
    QLabel#ViewerNoticeText {{ color: {c['fg']}; }}
    QLabel#ViewerDot {{ background: {c['dot']}; border-radius: 4px; }}
    QToolButton {{
        background: {c['field']}; color: {c['fg']}; border: 1px solid {c['line']};
        border-radius: 6px; padding: 2px 10px; font-size: 12px;
    }}
    QToolButton:hover {{ border-color: {c['muted']}; }}
    QToolButton:focus {{ border: 2px solid {ring}; padding: 1px 9px; }}
    """


def card_css(is_dark: bool) -> str:
    """The style of a viewer's card: the file's facts on a raised card,
    with its buttons (media_tabs' sound card wears it too)."""
    c = colors(is_dark)
    ring = theme.focus_ring_color(is_dark)
    return (f"""
    #ViewerCard {{ background: {c['card']}; border: 1px solid {c['line']}; border-radius: 14px; }}
    QLabel {{ color: {c['fg']}; background: transparent; font-size: 13px; }}
    QLabel#CardName {{ font-size: 17px; font-weight: 600; }}
    QLabel#CardFacts, QLabel#CardNote {{ color: {c['muted']}; font-size: 12px; }}
    QPushButton {{
        background: {c['field']}; color: {c['fg']}; border: 1px solid {c['line']};
        padding: 6px 14px; border-radius: 6px; font-size: 13px;
    }}
    QPushButton:hover {{ border-color: {c['muted']}; }}
    QPushButton:focus {{ border: 2px solid {ring}; padding: 5px 13px; }}
    """ + theme.accent_button_css("QPushButton#ViewerPrimary", is_dark)
        + theme.accent_button_css("QPushButton#ViewerPrimaryMenu", is_dark) + """
    QPushButton#ViewerPrimary { border-top-right-radius: 0; border-bottom-right-radius: 0; }
    QPushButton#ViewerPrimaryMenu {
        border-top-left-radius: 0; border-bottom-left-radius: 0; padding: 6px 7px;
    }
    QPushButton#ViewerPrimaryMenu:focus { padding: 5px 6px; }
    """)


def _drawn_icon(draw, color: QColor, side: int = 16) -> QIcon:
    """An icon from ``draw(painter, color, side)``, sharp at every common
    screen scale, with its dimmed form."""
    icon = QIcon()
    for mode, ink in ((QIcon.Mode.Normal, QColor(color)),
                      (QIcon.Mode.Disabled, format_icons.dimmed(color))):
        for scale in (1, 2, 3):
            pixmap = QPixmap(side * scale, side * scale)
            pixmap.setDevicePixelRatio(scale)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            draw(painter, QColor(ink), side)
            painter.end()
            icon.addPixmap(pixmap, mode)
    return icon


def _draw_chevron(painter: QPainter, ink: QColor, side: int) -> None:
    pen = QPen(ink, 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    s = side / 16
    path = QPainterPath()
    path.moveTo(4.5 * s, 6.5 * s)
    path.lineTo(8 * s, 10 * s)
    path.lineTo(11.5 * s, 6.5 * s)
    painter.drawPath(path)


def _draw_folder(painter: QPainter, ink: QColor, side: int) -> None:
    """A folder with a magnifier's hint of "show where it is"."""
    pen = QPen(ink, 1.4)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    s = side / 16
    path = QPainterPath()
    path.moveTo(1.5 * s, 4 * s)
    path.lineTo(6 * s, 4 * s)
    path.lineTo(7.5 * s, 5.5 * s)
    path.lineTo(14.5 * s, 5.5 * s)
    path.lineTo(14.5 * s, 13 * s)
    path.lineTo(1.5 * s, 13 * s)
    path.closeSubpath()
    painter.drawPath(path)
    painter.drawLine(QPointF(1.5 * s, 7.5 * s), QPointF(14.5 * s, 7.5 * s))


def _glyph_pixmap(glyph: str, color: QColor, side: int) -> QPixmap:
    pixmap = QPixmap(side * 2, side * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    format_icons.paint(glyph, painter, QRectF(0, 0, side, side), color)
    painter.end()
    return pixmap


def tile_pixmap(glyph: str, tint: QColor, side: int = 64) -> QPixmap:
    """The card's big icon: the kind's glyph in white on a tile of its tint."""
    pixmap = QPixmap(side * 2, side * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(tint)
    painter.drawRoundedRect(QRectF(0, 0, side, side), side * 0.22, side * 0.22)
    inner = side * 0.53
    offset = (side - inner) / 2
    format_icons.paint(glyph, painter, QRectF(offset, offset, inner, inner), QColor("#FFFFFF"))
    painter.end()
    return pixmap


# The prefix of the temporary folders reading views make (and remove
# again as they close): one left behind (a crash, a forced quit) is swept
# away at a later start once it is a day old.
TEMP_PREFIX = "myeditor-reading-"
STALE_TEMP_SECONDS = 24 * 60 * 60


def sweep_stale_temp_folders(root: str | None = None, now: float | None = None) -> int:
    """Remove the reading views' temporary folders older than a day from
    ``root`` (the system's temporary folder); only this app's, by their
    prefix, never anything else. Returns how many went."""
    import time
    root = root or tempfile.gettempdir()
    now = time.time() if now is None else now
    removed = 0
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    for name in names:
        if not name.startswith(TEMP_PREFIX):
            continue
        path = os.path.join(root, name)
        try:
            if os.path.islink(path) or not os.path.isdir(path):
                continue
            if now - os.path.getmtime(path) < STALE_TEMP_SECONDS:
                continue
        except OSError:
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed += not os.path.exists(path)
    return removed


# --------------------------------------------------------------------------- #
# The tab every viewer is                                                     #
# --------------------------------------------------------------------------- #

def lock_notice_text() -> str:
    """What a tab says while another app has its file open (its lock file
    is there): one sentence for every kind of file."""
    return _("This file is open in another app. You are seeing its last saved version.")


class FileBar(QWidget):
    """The slim bar over a file's tab: the kind's icon, the file's name and
    facts, room for the kind's own controls, "Insert into Note" and
    "Import as Note" where they fit, the joined "Open in {app}" button with
    its "Open With" chevron (a plain "Open With…" when no app is known),
    and the button that shows the file in the Finder. Viewers and the
    spreadsheet tab share it."""

    open_requested = Signal()
    open_with_requested = Signal(QPoint)
    reveal_requested = Signal()
    insert_requested = Signal()
    import_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.default_app: str | None = None
        self.reveal_label = _("Show in Finder")
        self.setObjectName("ViewerBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 10, 4)
        row.setSpacing(6)

        self.kind_icon = QLabel()
        self.kind_icon.setFixedSize(18, 18)
        row.addWidget(self.kind_icon)
        self.name_label = ElidedLabel()
        self.name_label.setObjectName("ViewerName")
        row.addWidget(self.name_label)
        self.facts_label = ElidedLabel()
        self.facts_label.setObjectName("ViewerFacts")
        row.addWidget(self.facts_label)
        row.addStretch(1)

        # Controls of the kind's own (a picture's zoom), between the name
        # and the commands.
        self.middle = QHBoxLayout()
        self.middle.setSpacing(4)
        row.addLayout(self.middle)
        row.addSpacing(8)

        self.insert_button = QToolButton()
        self.insert_button.setObjectName("ViewerPill")
        self.insert_button.setText(_("Insert into Note"))
        self.insert_button.setAccessibleName(_("Insert into Note"))
        self.insert_button.setToolTip(_("Insert this file into the note you are writing"))
        self.insert_button.clicked.connect(self.insert_requested)
        self.insert_button.setVisible(False)
        row.addWidget(self.insert_button)

        self.import_button = QToolButton()
        self.import_button.setObjectName("ViewerPill")
        self.import_button.setText(_("Import as Note"))
        self.import_button.setAccessibleName(_("Import as Note"))
        self.import_button.setToolTip(_("Make a note of this document next to it. "
                                        "The document stays as it is."))
        self.import_button.clicked.connect(self.import_requested)
        self.import_button.setVisible(False)
        row.addWidget(self.import_button)

        split = QHBoxLayout()
        split.setSpacing(0)
        self.open_button = QToolButton()
        self.open_button.clicked.connect(self._open_clicked)
        split.addWidget(self.open_button)
        self.open_menu_button = QToolButton()
        self.open_menu_button.setObjectName("ViewerSplitMenu")
        self.open_menu_button.setAccessibleName(_("Open With"))
        self.open_menu_button.setToolTip(_("Open With"))
        self.open_menu_button.setIconSize(QSize(12, 12))
        self.open_menu_button.clicked.connect(lambda: self.ask_open_with(self.open_menu_button))
        split.addWidget(self.open_menu_button)
        row.addLayout(split)

        self.reveal_button = QToolButton()
        self.reveal_button.setIconSize(QSize(16, 16))
        self.reveal_button.clicked.connect(self.reveal_requested)
        row.addWidget(self.reveal_button)
        self.set_reveal_label(self.reveal_label)
        self.set_default_app(None)

    def set_file(self, path: str, facts) -> None:
        """Show ``path``'s name and ``facts`` (size, dimensions, sheets)."""
        self.name_label.setText(os.path.basename(path.rstrip("/\\")))
        self.facts_label.setText(" \u00b7 ".join(fact for fact in facts if fact))

    def open_words(self) -> str:
        if self.default_app:
            return _("Open in {app}").format(app=self.default_app)
        return _("Open With…")

    def set_default_app(self, name: str | None) -> None:
        self.default_app = name or None
        words = self.open_words()
        self.open_button.setText(words)
        self.open_button.setAccessibleName(words)
        self.open_button.setToolTip(words)
        has_app = self.default_app is not None
        self.open_button.setObjectName("ViewerSplitMain" if has_app else "ViewerPill")
        self.open_button.style().unpolish(self.open_button)
        self.open_button.style().polish(self.open_button)
        self.open_menu_button.setVisible(has_app)

    def _open_clicked(self) -> None:
        if self.default_app:
            self.open_requested.emit()
        else:
            self.ask_open_with(self.open_button)

    def ask_open_with(self, button: QWidget) -> None:
        """Ask the window for the "Open With" menu, under ``button``."""
        self.open_with_requested.emit(button.mapToGlobal(QPoint(0, button.height())))

    def set_reveal_label(self, text: str) -> None:
        self.reveal_label = text or _("Show in Finder")
        self.reveal_button.setAccessibleName(self.reveal_label)
        self.reveal_button.setToolTip(self.reveal_label)

    def update_theme(self, is_dark: bool, path: str) -> None:
        c = colors(is_dark)
        self.setStyleSheet(_bar_css(is_dark))
        kind = file_kinds.kind_of_path(path)
        self.kind_icon.setPixmap(_glyph_pixmap(file_kinds.kind_icon_name(kind, path),
                                               file_kinds.format_tint(path), 18))
        self.open_menu_button.setIcon(_drawn_icon(_draw_chevron, QColor(c["text"]), 12))
        self.reveal_button.setIcon(_drawn_icon(_draw_folder, QColor(c["text"])))


class FileViewerTab(QWidget):
    """A read-only tab for one file: the bar on top, the kind's own view
    below. Subclasses build the view in ``_build_body`` and read the file
    in ``_load``; the window wires the signals."""

    open_requested = Signal()
    open_with_requested = Signal(QPoint)
    reveal_requested = Signal()
    insert_into_note_requested = Signal()
    import_as_note_requested = Signal()
    pandoc_install_requested = Signal()

    kind = file_kinds.OTHER
    # What the bar offers besides opening and revealing.
    _offers_insert = False
    _offers_import = False

    def __init__(self, path: str, is_dark: bool = False, parent=None):
        super().__init__(parent)
        self._file_path = path
        self.kind = self._kind_of(path)
        self.is_dark = is_dark
        self.load_ok = False
        self.load_error = ""
        self._default_app: str | None = None
        self._reveal_label = _("Show in Finder")
        self._reload_pending = False
        self._released = False
        # A tab that reads its file on a worker thread is loading until the
        # result arrives; load_ok and load_error say how it went then.
        self._loading = False

        self._build_bar()
        self._notice = self._build_notice()
        self.body = QWidget()
        self.body.setObjectName("ViewerBody")
        self.body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._bar)
        layout.addWidget(self._notice)
        layout.addWidget(self.body, 1)

        self._build_body(self.body)
        self._sync_reveal()
        self._load()
        self._refresh()
        self._sync_open_buttons()
        self.update_theme(is_dark)

    # -- construction ------------------------------------------------------

    def _build_bar(self) -> None:
        self._bar = FileBar()
        self._bar.open_requested.connect(self.open_requested)
        self._bar.open_with_requested.connect(self.open_with_requested)
        self._bar.reveal_requested.connect(self.reveal_requested)
        self._bar.insert_requested.connect(self.insert_into_note_requested)
        self._bar.import_requested.connect(self.import_as_note_requested)
        self._bar.insert_button.setVisible(self._offers_insert)
        self._bar.import_button.setVisible(self._offers_import)
        # The bar's parts by the names the viewers have always used.
        self._kind_icon = self._bar.kind_icon
        self._name_label = self._bar.name_label
        self._facts_label = self._bar.facts_label
        self._bar_middle = self._bar.middle
        self._insert_btn = self._bar.insert_button
        self._import_btn = self._bar.import_button
        self._open_btn = self._bar.open_button
        self._open_menu_btn = self._bar.open_menu_button
        self._reveal_btn = self._bar.reveal_button

    def _build_notice(self) -> QWidget:
        """The line that says another app has the file open."""
        notice = QWidget()
        notice.setObjectName("ViewerInfo")
        notice.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(notice)
        row.setContentsMargins(14, 6, 14, 6)
        row.setSpacing(8)
        dot = QLabel()
        dot.setObjectName("ViewerDot")
        dot.setFixedSize(8, 8)
        row.addWidget(dot)
        self._notice_text = QLabel(lock_notice_text())
        self._notice_text.setObjectName("ViewerNoticeText")
        self._notice_text.setWordWrap(True)
        row.addWidget(self._notice_text, 1)
        notice.setVisible(False)
        return notice

    @classmethod
    def _kind_of(cls, path: str) -> str:
        return cls.kind

    def _build_body(self, body: QWidget) -> None:
        """Fill ``body`` with the kind's own view."""

    # -- loading -----------------------------------------------------------

    def _load(self) -> bool:
        """Read the file; sets ``load_ok`` and ``load_error``. The base only
        asks that the file be there."""
        if os.path.exists(self._file_path):
            self.load_ok = True
            self.load_error = ""
        else:
            self.load_ok = False
            self.load_error = _("The file could not be found.")
        return self.load_ok

    def is_loading(self) -> bool:
        """Whether the file is still being read on a worker thread."""
        return self._loading

    def _facts(self) -> list[str]:
        """The facts the bar shows after the name (size, dimensions,
        duration), most telling first."""
        if not os.path.exists(self._file_path):
            return []
        return [media_files.format_size(_file_size(self._file_path))]

    def _refresh(self) -> None:
        """Bring the bar and the lock notice up to date with the file."""
        self._bar.set_file(self._file_path, self._facts())
        self._notice.setVisible(file_kinds.lock_owner_file(self._file_path) is not None)

    def schedule_reload(self) -> None:
        """Reload a moment after the file changed on disk, once however
        often the watcher fires while an app saves it."""
        if self._reload_pending or self._released:
            return
        self._reload_pending = True
        QTimer.singleShot(400, self, self._do_scheduled_reload)

    def _do_scheduled_reload(self) -> None:
        self._reload_pending = False
        if not self._released and os.path.exists(self._file_path):
            self.reload()

    def reload(self) -> None:
        self._load()
        self._refresh()

    def release(self) -> None:
        """Let go of what the tab holds as it closes (a player, a temporary
        folder); the base holds nothing."""
        self._released = True
        self._reload_pending = False

    def follow_move(self, path: str) -> None:
        """The file was renamed or moved (in the library, or from the tab's
        menu): the tab goes on with it there, as it is."""
        self._file_path = path
        self._refresh()

    def save_view_state(self) -> None:
        """Viewers keep no reading position."""

    def document_title(self) -> str:
        """The file's name, extension included: a folder can hold "Budget.pdf"
        and "Budget.xlsx" side by side."""
        return os.path.basename(self._file_path.rstrip("/\\"))

    def is_modified(self) -> bool:
        return False

    # -- opening -----------------------------------------------------------

    def set_default_app(self, name: str | None) -> None:
        """Name the app this computer opens the file with ("Open in
        Numbers"); None when there is none ("Open With…")."""
        self._default_app = name or None
        self._sync_open_buttons()

    def _open_words(self) -> str:
        return self._bar.open_words()

    def _sync_open_buttons(self) -> None:
        self._bar.set_default_app(self._default_app)

    def _ask_open_with(self, button: QWidget) -> None:
        """Ask the window for the "Open With" menu, under ``button``."""
        self._bar.ask_open_with(button)

    def set_reveal_label(self, text: str) -> None:
        """The words of the reveal button ("Show in Finder", "Show in
        Explorer", "Show in File Manager"), from default_apps.reveal_label."""
        self._reveal_label = text or _("Show in Finder")
        self._sync_reveal()

    def _sync_reveal(self) -> None:
        self._bar.set_reveal_label(self._reveal_label)

    # -- theme -------------------------------------------------------------

    def update_theme(self, is_dark: bool) -> None:
        self.is_dark = is_dark
        c = colors(is_dark)
        self._bar.update_theme(is_dark, self._file_path)
        self._notice.setStyleSheet(_info_css(is_dark))
        self.body.setStyleSheet(f"#ViewerBody {{ background: {c['viewer']}; }}")
        self._apply_theme(is_dark)

    def _apply_theme(self, is_dark: bool) -> None:
        """The kind's own view's colors."""


# --------------------------------------------------------------------------- #
# The info card                                                               #
# --------------------------------------------------------------------------- #

class FileInfoTab(FileViewerTab):
    """For a file MyEditor shows and sorts but does not read: a centered
    card with what it is and the app that edits it."""

    kind = file_kinds.OTHER

    @classmethod
    def _kind_of(cls, path: str) -> str:
        return file_kinds.kind_of_path(path)

    def _build_body(self, body: QWidget) -> None:
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._make_card_page())

    def _make_card_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(28, 28, 28, 28)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch()

        self._card = QFrame()
        self._card.setObjectName("ViewerCard")
        self._card.setMaximumWidth(520)
        self._card.setMinimumWidth(280)
        self._card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._card_shadow = QGraphicsDropShadowEffect(self._card)
        self._card_shadow.setBlurRadius(24)
        self._card_shadow.setOffset(0, 4)
        self._card.setGraphicsEffect(self._card_shadow)
        card = QVBoxLayout(self._card)
        card.setContentsMargins(28, 26, 28, 24)
        card.setSpacing(4)

        self._card_tile = QLabel()
        self._card_tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._card_tile.setFixedHeight(64)
        card.addWidget(self._card_tile)
        card.addSpacing(8)
        self._card_name = QLabel()
        self._card_name.setObjectName("CardName")
        self._card_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._card_name.setWordWrap(True)
        self._card_name.setTextFormat(Qt.TextFormat.PlainText)
        card.addWidget(self._card_name)
        self._card_facts = QLabel()
        self._card_facts.setObjectName("CardFacts")
        self._card_facts.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._card_facts.setWordWrap(True)
        self._card_facts.setTextFormat(Qt.TextFormat.PlainText)
        card.addWidget(self._card_facts)
        card.addSpacing(14)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        split = QHBoxLayout()
        split.setSpacing(0)
        self._card_open = QPushButton()
        self._card_open.setObjectName("ViewerPrimary")
        self._card_open.setAutoDefault(False)
        self._card_open.clicked.connect(self._card_open_clicked)
        split.addWidget(self._card_open)
        self._card_open_menu = QPushButton()
        self._card_open_menu.setObjectName("ViewerPrimaryMenu")
        self._card_open_menu.setAutoDefault(False)
        self._card_open_menu.setAccessibleName(_("Open With"))
        self._card_open_menu.setToolTip(_("Open With"))
        self._card_open_menu.setIconSize(QSize(12, 12))
        self._card_open_menu.clicked.connect(
            lambda: self._ask_open_with(self._card_open_menu))
        split.addWidget(self._card_open_menu)
        buttons.addLayout(split)
        self._card_reveal = QPushButton()
        self._card_reveal.setAutoDefault(False)
        self._card_reveal.clicked.connect(self.reveal_requested)
        buttons.addWidget(self._card_reveal)
        buttons.addStretch(1)
        card.addLayout(buttons)

        card.addSpacing(12)
        note = QLabel(_("MyEditor shows and sorts this file. "
                        "It is edited in the app this computer has set for it."))
        note.setObjectName("CardNote")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setWordWrap(True)
        card.addWidget(note)

        # The card takes the room first (up to its 520 px), so a longer
        # button text ("In LibreOffice Writer öffnen") is not cut; the
        # stretches only center what is left.
        row.addWidget(self._card, 1)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch(1)
        return page

    def _card_open_clicked(self) -> None:
        if self._default_app:
            self.open_requested.emit()
        else:
            self._ask_open_with(self._card_open)

    def _refresh(self) -> None:
        super()._refresh()
        name = os.path.basename(self._file_path.rstrip("/\\"))
        self._card_name.setText(name)
        if os.path.exists(self._file_path):
            facts = _("{kind} · {size} · Modified {date}").format(
                kind=file_kinds.format_label(self._file_path),
                size=media_files.format_size(_file_size(self._file_path)),
                date=_modified_day(self._file_path))
        else:
            facts = _("The file could not be found.")
        self._card_facts.setText(facts)

    def _sync_open_buttons(self) -> None:
        super()._sync_open_buttons()
        words = self._open_words()
        self._card_open.setText(words)
        self._card_open.setAccessibleName(words)
        has_app = self._default_app is not None
        self._card_open.setObjectName("ViewerPrimary" if has_app else "ViewerPrimaryAlone")
        self._card_open.style().unpolish(self._card_open)
        self._card_open.style().polish(self._card_open)
        self._card_open_menu.setVisible(has_app)

    def _sync_reveal(self) -> None:
        super()._sync_reveal()
        if hasattr(self, "_card_reveal"):
            self._card_reveal.setText(self._reveal_label)
            self._card_reveal.setAccessibleName(self._reveal_label)

    def _apply_theme(self, is_dark: bool) -> None:
        c = colors(is_dark)
        self._card.setStyleSheet(card_css(is_dark) + theme.accent_button_css(
            "QPushButton#ViewerPrimaryAlone", is_dark))
        self._card_shadow.setColor(c["shadow"])
        glyph = file_kinds.kind_icon_name(self.kind, self._file_path)
        self._card_tile.setPixmap(tile_pixmap(glyph, file_kinds.format_tint(self._file_path)))
        self._card_open_menu.setIcon(_drawn_icon(_draw_chevron, QColor("#FFFFFF"), 12))


# --------------------------------------------------------------------------- #
# The preview picture an office app stores in its file                        #
# --------------------------------------------------------------------------- #

class _Figure(QWidget):
    """A picture fitted into the room it has (never enlarged), with a
    shadow, as a page lies on a desk."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pixmap: QPixmap | None = None
        self.setMinimumSize(160, 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.shadow = QColor(0, 0, 0, 60)

    def paintEvent(self, _event) -> None:
        if self.pixmap is None or self.pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = self.rect().adjusted(12, 12, -12, -12)
        original = self.pixmap.deviceIndependentSize().toSize()
        size = original.scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
        if size.width() > original.width():
            size = original
        target = self.rect()
        target.setSize(size)
        target.moveCenter(area.center())
        for spread, alpha in ((6, 0.12), (3, 0.25), (1, 0.45)):
            shade = QColor(self.shadow)
            shade.setAlphaF(shade.alphaF() * alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shade)
            painter.drawRoundedRect(QRectF(target).adjusted(-spread, -spread + 3,
                                                            spread, spread + 3), 6, 6)
        painter.drawPixmap(target, self.pixmap)
        painter.end()


class PackagePreviewTab(FileInfoTab):
    """The preview picture an office app saved in its file (Numbers,
    Pages, Keynote, PowerPoint, Impress; the old Office formats only when
    they carry one), with a line saying what it is; the info card when
    the file has none."""

    def _build_body(self, body: QWidget) -> None:
        self._generation = 0
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        self._pages = QStackedWidget()
        layout.addWidget(self._pages)

        self._preview_page = QWidget()
        column = QVBoxLayout(self._preview_page)
        column.setContentsMargins(28, 28, 28, 20)
        column.setSpacing(10)
        self._figure = _Figure()
        column.addWidget(self._figure, 1)
        self._caption = QLabel()
        self._caption.setObjectName("ViewerCaption")
        self._caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._caption.setWordWrap(True)
        column.addWidget(self._caption)
        self._pages.addWidget(self._preview_page)
        self._pages.addWidget(self._make_card_page())

    @property
    def has_preview(self) -> bool:
        return self._figure.pixmap is not None

    def _load(self) -> bool:
        """Start reading the preview picture on a worker thread (a file
        from elsewhere can be a zip of a million parts, or no file at
        all); it is shown when it arrives."""
        self._generation += 1
        generation = self._generation
        if not super()._load():
            self._loading = False
            self._show_picture(None)
            return False
        import office_preview
        self._loading = True
        path = self._file_path

        def arrived(image):
            if self._released or generation != self._generation:
                return
            self._loading = False
            self._show_picture(image)

        background.run(lambda: office_preview.package_thumbnail(path), arrived,
                       lambda _error: arrived(None), receiver=self)
        return True

    def release(self) -> None:
        super().release()
        self._generation += 1
        self._loading = False

    def _show_picture(self, image: QImage | None) -> None:
        if image is None or image.isNull():
            self._figure.pixmap = None
            self._pages.setCurrentWidget(self._pages.widget(1))
            return
        self._figure.pixmap = QPixmap.fromImage(image)
        self._figure.update()
        self._caption.setText(self._caption_words())
        self._pages.setCurrentWidget(self._preview_page)

    def _caption_words(self) -> str:
        if self.kind == file_kinds.SLIDES:
            return _("The first slide, as the app saved it as a preview picture in the file.")
        if self.kind == file_kinds.SHEET:
            return _("The preview picture the app saved in the file. "
                     "The whole spreadsheet opens in the app.")
        return _("The preview picture the app saved in the file. "
                 "The whole document opens in the app.")

    def _facts(self) -> list[str]:
        facts = super()._facts()
        return [file_kinds.format_label(self._file_path)] + facts if facts else facts

    def _apply_theme(self, is_dark: bool) -> None:
        super()._apply_theme(is_dark)
        c = colors(is_dark)
        self._caption.setStyleSheet(
            f"color: {c['muted']}; font-size: 12px; background: transparent;")
        self._figure.shadow = QColor(0, 0, 0, 200 if is_dark else 90)
        self._figure.update()


# --------------------------------------------------------------------------- #
# The reading view of a Word or Writer document                               #
# --------------------------------------------------------------------------- #

class _ReadingBrowser(QTextBrowser):
    """A QTextBrowser for a document somebody else wrote: it follows no
    link itself (web links go to the browser on a click, the rest
    nowhere) and loads no resource but a picture that is a data: address
    or lies in the tab's own temporary folder, decoded by image_safety.
    Its text lies on a page-like column in the middle."""

    COLUMN = 760

    def __init__(self, picture_root: str, parent=None):
        super().__init__(parent)
        self._policy = image_safety.ImageRootPolicy([picture_root])
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setSearchPaths([])
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAccessibleName(_("Document"))
        self.document().setDocumentMargin(44)
        font = self.document().defaultFont()
        font.setPixelSize(15)
        self.document().setDefaultFont(font)
        self.anchorClicked.connect(self._follow)
        self.setAutoFillBackground(True)
        self.viewport().setAutoFillBackground(True)

    def resizeEvent(self, event) -> None:
        side = max(0, (self.width() - self.COLUMN) // 2)
        self.setViewportMargins(side, 0, side, 0)
        super().resizeEvent(event)

    def _follow(self, url) -> None:
        if url.toString().startswith("#"):
            self.scrollToAnchor(url.fragment())
        elif is_safe_external_url(url.toString()):
            QDesktopServices.openUrl(url)

    def loadResource(self, kind, url):  # noqa: N802 (Qt's name)
        # Whatever is returned is final: returning nothing would let
        # QTextDocument read the file itself, past every check here.
        if kind != QTextDocument.ResourceType.ImageResource:
            return ""
        name = url.toString()
        if name.lower().startswith("data:"):
            data = image_safety.data_uri_bytes(name)
        else:
            data = self._policy.read(url.toLocalFile() if url.isLocalFile() else name)
        image = image_safety.decode_image_bytes(data) if data else None
        if image is None:
            return QImage()
        room = self.COLUMN - 2 * int(self.document().documentMargin())
        if image.width() > room:
            image = image.scaledToWidth(room, Qt.TransformationMode.SmoothTransformation)
        return image


class DocumentReadingTab(FileViewerTab):
    """A Word or Writer document (.docx, .odt) to read: text, headings,
    lists, tables and pictures, without the page layout. pandoc makes it
    when it is on this computer; without it a plainer reading is shown
    with a quiet offer to install pandoc."""

    kind = file_kinds.DOCUMENT
    _offers_import = True

    def _build_body(self, body: QWidget) -> None:
        self._temp_root = tempfile.mkdtemp(prefix=TEMP_PREFIX)
        self._render_dir: str | None = None
        self._generation = 0
        self._cancel = threading.Event()
        self._html = ""
        self._message = ""

        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._info = QWidget()
        self._info.setObjectName("ViewerInfo")
        self._info.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(self._info)
        row.setContentsMargins(14, 6, 14, 6)
        row.setSpacing(10)
        about = QLabel(_("Reading view: text, tables and pictures, without the exact page layout."))
        about.setWordWrap(True)
        row.addWidget(about, 1)
        self._pandoc_line = QLabel(_("Install pandoc for a more faithful view."))
        row.addWidget(self._pandoc_line)
        self._pandoc_btn = QToolButton()
        self._pandoc_btn.setText(_("Install pandoc…"))
        self._pandoc_btn.setAccessibleName(_("Install pandoc…"))
        self._pandoc_btn.clicked.connect(self.pandoc_install_requested)
        row.addWidget(self._pandoc_btn)
        self._show_pandoc_offer(False)
        layout.addWidget(self._info)

        self.browser = _ReadingBrowser(self._temp_root)
        layout.addWidget(self.browser, 1)
        self.setFocusProxy(self.browser)

    def _show_pandoc_offer(self, shown: bool) -> None:
        self._pandoc_line.setVisible(shown)
        self._pandoc_btn.setVisible(shown)

    @property
    def pandoc_offered(self) -> bool:
        return not self._pandoc_btn.isHidden()

    def _load(self) -> bool:
        if not super()._load():
            self._show_message(self.load_error)
            return False
        self._start_render()
        return True

    def _start_render(self) -> None:
        import office_preview
        self._generation += 1
        generation = self._generation
        self._show_message(_("Preparing the reading view…"))
        render_dir = tempfile.mkdtemp(dir=self._temp_root)
        path = self._file_path
        cancel = self._cancel

        def work():
            result = office_preview.render_document(path, media_dir=render_dir)
            if cancel.is_set():
                shutil.rmtree(render_dir, ignore_errors=True)
            return result

        def done(result):
            if self._released or generation != self._generation:
                shutil.rmtree(render_dir, ignore_errors=True)
                return
            old, self._render_dir = self._render_dir, render_dir
            self._html = result.html
            self._message = ""
            self._show_pandoc_offer(result.pandoc_missing)
            self._set_html()
            if old:
                shutil.rmtree(old, ignore_errors=True)

        def failed(error):
            shutil.rmtree(render_dir, ignore_errors=True)
            if self._released or generation != self._generation:
                return
            if isinstance(error, office_preview.DocumentUnreadable) and str(error):
                message = str(error)
            else:
                _log.warning("No reading view of %s: %r", path, error)
                message = _("This document could not be shown. Open it in its app to read it.")
            self._show_message(message)

        background.run(work, done, failed, receiver=self)

    def _show_message(self, words: str) -> None:
        """A calm sentence in place of the document (loading, unreadable)."""
        self._message = words
        self._html = ""
        self._set_html()

    def _set_html(self) -> None:
        c = colors(self.is_dark)
        link = theme.dialog_link_color(self.is_dark)
        document = self.browser.document()
        document.setDefaultStyleSheet(f"""
            p, li {{ line-height: 150%; }}
            h1 {{ font-size: 24px; }} h2 {{ font-size: 20px; }} h3 {{ font-size: 17px; }}
            a {{ color: {link}; }}
            table {{ border-collapse: collapse; margin-top: 6px; margin-bottom: 14px; }}
            th, td {{ border: 1px solid {c['line']}; padding: 4px 6px; font-size: 13px; }}
            th {{ font-weight: 600; }}
        """)
        if self._message:
            self.browser.setHtml('<p style="color: {color};">{words}</p>'.format(
                color=c["muted"], words=html.escape(self._message)))
        else:
            self.browser.setHtml(self._html)

    def release(self) -> None:
        super().release()
        self._cancel.set()
        self._generation += 1
        self.browser.clear()
        shutil.rmtree(self._temp_root, ignore_errors=True)

    def _apply_theme(self, is_dark: bool) -> None:
        c = colors(is_dark)
        self._info.setStyleSheet(_info_css(is_dark))
        # The text's color is the browser's own Text (a body color in the
        # style sheet is not used by QTextDocument).
        outer = self.browser.palette()
        outer.setColor(QPalette.ColorRole.Window, QColor(c["viewer"]))
        outer.setColor(QPalette.ColorRole.Text, QColor(c["fg"]))
        outer.setColor(QPalette.ColorRole.Base, QColor(c["paper"]))
        self.browser.setPalette(outer)
        inner = self.browser.viewport().palette()
        inner.setColor(QPalette.ColorRole.Base, QColor(c["paper"]))
        inner.setColor(QPalette.ColorRole.Window, QColor(c["paper"]))
        inner.setColor(QPalette.ColorRole.Text, QColor(c["fg"]))
        self.browser.viewport().setPalette(inner)
        if self._html or self._message:
            self._set_html()


# --------------------------------------------------------------------------- #
# Choosing the tab                                                            #
# --------------------------------------------------------------------------- #

def viewer_for(path: str, is_dark: bool, parent=None) -> FileViewerTab | None:
    """The viewer tab for the file at ``path``, or None for a file another
    tab owns: a note or text file (the editor), a PDF (the PDF reader), a
    spreadsheet MyEditor edits (the sheet tab)."""
    kind = file_kinds.kind_of_path(path)
    ext = file_kinds.extension(path)
    if (kind in (file_kinds.NOTE, file_kinds.TEXT, file_kinds.PDF)
            or ext in file_kinds.EDITABLE_SHEET_EXTS):
        return None
    if kind == file_kinds.IMAGE:
        import media_tabs
        if media_tabs.can_show_picture(path):
            return media_tabs.ImageViewerTab(path, is_dark, parent)
        return FileInfoTab(path, is_dark, parent)
    if kind in (file_kinds.VIDEO, file_kinds.AUDIO):
        import media_tabs
        return media_tabs.MediaPlayerTab(path, is_dark, parent)
    if ext in file_kinds.READING_VIEW_EXTS and not os.path.isdir(path):
        return DocumentReadingTab(path, is_dark, parent)
    if ext in _PREVIEW_PICTURE_EXTS:
        return PackagePreviewTab(path, is_dark, parent)
    return FileInfoTab(path, is_dark, parent)
