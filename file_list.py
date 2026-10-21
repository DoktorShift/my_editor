# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The file list: the middle column between the folder sidebar and the
viewer, as in Apple Notes and the Finder's gallery.

    Writing                       [List|Gallery] [+]
    12 items
    [ Search                          ]
    [ All | Notes | Media | Files ]
    Today
      [icon] Plan for October
             14:05  First line of the note …
    Yesterday
      [pic]  Harbour.jpg
             Yesterday  4032 × 3024
    Previous 7 Days
      ...

The list groups files by the day they changed (Today, Yesterday,
Previous 7 Days, Previous 30 Days, then the months of this year and the
years before), newest first. A note is named without its extension and
shows its first line of text; a picture its size in pixels; anything
else its size on the disk. Across folders (Recents, Notes, Media,
Documents) each row also names its folder. The gallery shows the same
files as pictures: photos scaled down, notes and PDFs as a small page,
spreadsheets as a small grid, the rest as their kind's tinted icon.

Nothing is read from a file on the GUI thread: the rows painted ask for
their first lines, picture sizes and thumbnails, a small pool of worker
threads reads them, and the row repaints when they arrive (cached by
path and modification time). The model holds only what the folder
listing or the libraries' index already knows, so 20 000 files scroll
as smoothly as 20.

Files can be dragged out as the real files (the Finder, Mail and
Messages get the file itself) and onto a folder in the sidebar (they
move there; links follow). Files dragged in from elsewhere are copied
into the folder shown.

The pane only says what the person wants (``file_selected``,
``file_activated``, ``quick_look_requested``, ``new_note_requested``,
``insert_into_note_requested``, ``import_as_note_requested``); the host
shows, opens and inserts.
"""

from __future__ import annotations

import functools
import logging
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from PySide6.QtCore import (
    QAbstractListModel,
    QDate,
    QDateTime,
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QLocale,
    QModelIndex,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDrag,
    QFont,
    QFontMetrics,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import background
import default_apps
import file_kinds
import format_icons
import front_matter
import i18n
import library_actions
import media_files
import regular_files
import safe_picture
import theme
from folder_library import UNMEASURED, Entry, natural_key
from i18n import _, ngettext
from library_set import LibrarySet
from library_sidebar import DOCUMENTS, MEDIA, NOTES, RECENTS, RECENTS_LIMIT, SECTION_KINDS
from segmented_control import SegmentedControl

log = logging.getLogger(__name__)

# The scope filter's segments and the kinds each lets through.
ALL, SCOPE_NOTES, SCOPE_MEDIA, SCOPE_FILES = "all", "notes", "media", "files"
SCOPE_KINDS = {
    SCOPE_NOTES: SECTION_KINDS[NOTES],
    SCOPE_MEDIA: SECTION_KINDS[MEDIA],
    SCOPE_FILES: SECTION_KINDS[DOCUMENTS],
}
LIST, GALLERY = "list", "gallery"
FOLDER_MODE = "folder"

# How much of a note is read for its first line, and how long a line is kept.
SNIPPET_BYTES = 16 * 1024
SNIPPET_CHARS = 160
# A thumbnail's shorter side, in pixels (enough for a 2x screen's gallery).
THUMBNAIL_PIXELS = 200
# How many results (first lines, sizes, thumbnails) are kept.
CACHE_LIMIT = 600
# How many notes' first lines a search keeps once read, apart from the
# cache above: more than a search reads in one pass, so what it read stays
# found and is never read again while the notes are unchanged.
SEARCH_STORE_LIMIT = 20000
# Searching reads the first lines of at most this many notes not yet read.
SEARCH_READ_LIMIT = 2000

LIST_ROW_HEIGHT = 56
LIST_ROW_FOLDER_HEIGHT = 72
HEADER_HEIGHT = 30
THUMB = 36
GALLERY_THUMB_HEIGHT = 92
GALLERY_CELL_HEIGHT = 140
GALLERY_MIN_CELL = 116


# -- reading files (on worker threads) -------------------------------------------

_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_REF_LINK = re.compile(r"!?\[([^\]]*)\]\[[^\]]*\]")
_AUTOLINK = re.compile(r"<(https?://[^>]+)>")
_HTML = re.compile(r"<[^>]+>")
_LEAD = re.compile(r"^\s{0,3}(?:#{1,6}\s+|>\s*|[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)+")
_MARKS = re.compile(r"\*\*|__|~~|[*_`]")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_SKIP = re.compile(r"^\s*(?:[-*_]{3,}\s*$|\|?\s*:?-{3,}|<!--|\[[^\]]+\]:\s)")


def plain_line(line: str) -> str:
    """One line of Markdown as plain words: no heading or list marks, no
    emphasis, links as their text."""
    line = _LEAD.sub("", line)
    line = _LINK.sub(r"\1", line)
    line = _REF_LINK.sub(r"\1", line)
    line = _AUTOLINK.sub(r"\1", line)
    line = _HTML.sub("", line)
    line = _MARKS.sub("", line)
    return re.sub(r"\s+", " ", line).strip()


def note_snippet(path: str, title: str = "") -> str:
    """The first line of text of a note (or a text file): front matter,
    Markdown and a first line that only repeats ``title`` left out."""
    # Only a regular file: a pipe with a note's name would hold the thread.
    data = regular_files.read_regular_prefix(path, SNIPPET_BYTES)
    if data is None:
        return ""
    text = data.decode("utf-8", "replace").lstrip("﻿")
    if text.startswith(("---", "+++")):
        front, body = front_matter.split(text)
        if front is None and re.match(r"\A(---|\+\+\+)[ \t]*\r?\n", text) \
                and len(data) >= SNIPPET_BYTES:
            return ""                        # front matter longer than what was read
        text = body
    seen_title = False
    fence = ""
    for line in text.splitlines():
        opened = _FENCE.match(line)
        if fence:
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence):
                fence = ""
            continue
        if opened:
            fence = opened.group(1)            # code is not what a note says
            continue
        if not line.strip() or _SKIP.match(line):
            continue
        words = plain_line(line)
        if not words:
            continue
        if not seen_title and title and words.casefold() == title.casefold():
            seen_title = True
            continue
        return words[:SNIPPET_CHARS]
    return ""


def picture_size(path: str) -> str:
    """A picture's size in pixels ("4032 × 3024"), read from its header
    the one safe way (safe_picture: a regular file, the format its bytes
    claim, an SVG only sanitized)."""
    size = safe_picture.picture_size(path)
    if size is None:
        return ""
    return _("{width} × {height}").format(width=size.width(), height=size.height())


def scaled_picture(path: str, pixels: int = THUMBNAIL_PIXELS) -> Optional[QImage]:
    """A picture scaled down so its shorter side is ``pixels``, decoded
    at that size the one safe way (safe_picture); None for what cannot or
    should not be read."""
    return safe_picture.read_picture(path, QSize(pixels, pixels))


# Files whose app may keep a preview picture in them (office_preview).
OFFICE_PREVIEW_KINDS = frozenset({file_kinds.DOCUMENT, file_kinds.SLIDES, file_kinds.SHEET})


def office_thumbnail(path: str, pixels: int = THUMBNAIL_PIXELS) -> Optional[QImage]:
    """The preview picture a document, presentation or spreadsheet keeps
    in its file, scaled down like a picture's; None when it has none."""
    import office_preview
    image = office_preview.package_thumbnail(path)
    if image is None or image.isNull():
        return None
    shorter = min(image.width(), image.height())
    if shorter > pixels:
        image = image.scaled(QSize(max(1, round(image.width() * pixels / shorter)),
                                   max(1, round(image.height() * pixels / shorter))),
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return image


def _read(key: tuple, work: Callable[[], object]):
    """``work()`` on a worker thread; None (and a line in the log) when it
    fails, so the row shows without what could not be read."""
    try:
        return work()
    except Exception:                                   # noqa: BLE001
        log.exception("Reading %s for the file list failed", key)
        return None


class _Reader(QObject):
    """Reads first lines, picture sizes and thumbnails on two worker
    threads, each once, and keeps the latest results."""

    ready = Signal(str)           # the path whose result came

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._workers = background.Workers(self, threads=2)
        self._cache: "OrderedDict[tuple, object]" = OrderedDict()
        self._waiting: Set[tuple] = set()
        # Told every result as it comes, ``on_result(key, result)``.
        self.on_result: Optional[Callable[[tuple, object], None]] = None

    def get(self, key: tuple, work: Callable[[], object]):
        """The result for ``key``, or None while it is read (``ready``
        follows)."""
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        if key not in self._waiting:
            self._waiting.add(key)
            self._workers.run(functools.partial(_read, key, work),
                              lambda result: self._done(key, result), receiver=self)
        return None

    def known(self, key: tuple):
        return self._cache.get(key)

    def _done(self, key: tuple, result) -> None:
        self._waiting.discard(key)
        if isinstance(result, QImage):
            result = QPixmap.fromImage(result)
        self._cache[key] = result if result is not None else ""
        while len(self._cache) > CACHE_LIMIT:
            self._cache.popitem(last=False)
        if self.on_result is not None:
            self.on_result(key, self._cache[key])
        self.ready.emit(key[1])

    def busy(self) -> bool:
        return bool(self._waiting)

    def shutdown(self) -> None:
        self._workers.clear()
        self._workers.pool.waitForDone(3000)


# -- dates ---------------------------------------------------------------------

def _day(moment: float) -> QDate:
    return QDateTime.fromSecsSinceEpoch(int(moment)).date()


def date_group(mtime: float, now: Optional[float] = None) -> Tuple[tuple, str]:
    """The group a file changed at ``mtime`` is listed under, as Apple
    Notes groups: ``(sort key, title)``."""
    today = _day(now if now is not None else time.time())
    day = _day(mtime)
    days = day.daysTo(today)
    if days <= 0:
        return (0,), _("Today")
    if days == 1:
        return (1,), _("Yesterday")
    if days <= 7:
        return (2,), _("Previous 7 Days")
    if days <= 30:
        return (3,), _("Previous 30 Days")
    if day.year() == today.year():
        month = i18n.display_locale().standaloneMonthName(day.month(),
                                                          QLocale.FormatType.LongFormat)
        return (4, -day.month()), month
    return (5, -day.year()), str(day.year())


def when(mtime: float, now: Optional[float] = None) -> str:
    """When a file changed, as a list row says it: the time today,
    "Yesterday", else the date."""
    today = _day(now if now is not None else time.time())
    moment = QDateTime.fromSecsSinceEpoch(int(mtime))
    locale = i18n.display_locale()
    days = moment.date().daysTo(today)
    if days <= 0:
        return locale.toString(moment.time(), locale.timeFormat(QLocale.FormatType.ShortFormat))
    if days == 1:
        return _("Yesterday")
    return locale.toString(moment.date(), locale.dateFormat(QLocale.FormatType.ShortFormat))


def display_name(entry: Entry) -> str:
    """A file's name as the list shows it: a note without its extension."""
    if entry.kind == file_kinds.NOTE:
        return os.path.splitext(entry.name)[0] or entry.name
    return entry.name


# -- the model -------------------------------------------------------------------

@dataclass(frozen=True)
class FileRow:
    title: str = ""                       # a header's
    entry: Optional[Entry] = None         # a file's or folder's

    @property
    def is_header(self) -> bool:
        return self.entry is None


RowRole = Qt.ItemDataRole.UserRole + 1


class FileModel(QAbstractListModel):
    def __init__(self, pane: "FileListPane") -> None:
        super().__init__(pane)
        self._pane = pane
        self._rows: List[FileRow] = []
        self._by_path: Dict[str, int] = {}

    def set_rows(self, rows: List[FileRow]) -> None:
        self.beginResetModel()
        self._rows = rows
        self._by_path = {r.entry.path: i for i, r in enumerate(rows) if r.entry is not None}
        self.endResetModel()

    def rows(self) -> List[FileRow]:
        return self._rows

    def row(self, number: int) -> Optional[FileRow]:
        return self._rows[number] if 0 <= number < len(self._rows) else None

    def row_of(self, path: str) -> int:
        return self._by_path.get(path, -1)

    def entries(self) -> List[Entry]:
        return [r.entry for r in self._rows if r.entry is not None]

    def path_changed(self, path: str) -> None:
        number = self._by_path.get(path)
        if number is not None:
            index = self.index(number)
            self.dataChanged.emit(index, index)

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def flags(self, index):
        row = self.row(index.row())
        if row is None or row.is_header:
            return Qt.ItemFlag.NoItemFlags
        return (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsDragEnabled)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        row = self.row(index.row())
        if row is None:
            return None
        if role == RowRole:
            return row
        if role == Qt.ItemDataRole.DisplayRole:
            return row.title if row.is_header else display_name(row.entry)
        if role == Qt.ItemDataRole.ToolTipRole and row.entry is not None:
            return row.entry.path
        if role == Qt.ItemDataRole.AccessibleTextRole:
            if row.is_header:
                return row.title
            parts = [display_name(row.entry), self._pane.when_text(row.entry),
                     self._pane.fact(row.entry, request=False)]
            if self._pane.spans_folders():
                parts.append(os.path.basename(os.path.dirname(row.entry.path)))
            return ", ".join(p for p in parts if p)
        return None


# -- painting ----------------------------------------------------------------------

def _tile(painter: QPainter, rect: QRectF, entry: Entry, radius: float, glyph_size: float) -> None:
    """A rounded tile in the kind's colour with its glyph in white."""
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    if entry.is_dir:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # The bright accent, as the Finder's folders (the selection fill
        # is too dark for them in the dark theme).
        library_actions.paint_folder(painter, rect, theme.accent_icon_color(
            theme.is_dark_active()), filled=True)
        painter.restore()
        return
    painter.setBrush(file_kinds.format_tint(entry.path))
    painter.drawRoundedRect(rect, radius, radius)
    glyph = QRectF(rect.center().x() - glyph_size / 2, rect.center().y() - glyph_size / 2,
                   glyph_size, glyph_size)
    format_icons.paint(file_kinds.kind_icon_name(entry.kind, entry.path), painter, glyph,
                       QColor("#FFFFFF"))
    painter.restore()


def _cover(painter: QPainter, rect: QRectF, pixmap: QPixmap, radius: float) -> None:
    """``pixmap`` filling ``rect``, cut to it, with rounded corners."""
    painter.save()
    clip = QPainterPath()
    clip.addRoundedRect(rect, radius, radius)
    painter.setClipPath(clip)
    size = pixmap.size() / max(1.0, pixmap.devicePixelRatio())
    scale = max(rect.width() / max(1, size.width()), rect.height() / max(1, size.height()))
    w, h = size.width() * scale, size.height() * scale
    target = QRectF(rect.center().x() - w / 2, rect.center().y() - h / 2, w, h)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
    painter.restore()


def _page(painter: QPainter, box: QRectF, palette: QPalette, title: str, tag: str = "") -> None:
    """A small page: a title (or a red tag) and grey lines of text."""
    painter.save()
    height = box.height() * 0.84
    width = min(box.width() * 0.62, height * 0.78)
    page = QRectF(box.center().x() - width / 2, box.center().y() - height / 2, width, height)
    painter.setPen(QPen(palette.color(QPalette.ColorRole.Mid), 1))
    painter.setBrush(QColor("#FFFFFF"))
    painter.drawRoundedRect(page, 2, 2)
    x, y = page.left() + 5, page.top() + 6
    inner = page.width() - 10
    if tag:
        font = QFont(painter.font())
        font.setPixelSize(7)
        font.setBold(True)
        painter.setFont(font)
        tag_rect = QRectF(x, y, QFontMetrics(font).horizontalAdvance(tag) + 6, 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FF3B30"))
        painter.drawRoundedRect(tag_rect, 2, 2)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, tag)
        y += 14
    elif title:
        font = QFont(painter.font())
        font.setPixelSize(7)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#1D1D1F"))
        line = QFontMetrics(font).elidedText(title, Qt.TextElideMode.ElideRight, int(inner))
        painter.drawText(QRectF(x, y, inner, 10), Qt.AlignmentFlag.AlignLeft, line)
        y += 13
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 34))
    for share in (0.9, 0.8, 0.86, 0.5, 0.84, 0.7):
        if y + 3 > page.bottom() - 4:
            break
        painter.drawRoundedRect(QRectF(x, y, inner * share, 3), 1.5, 1.5)
        y += 7
    painter.restore()


def _grid(painter: QPainter, box: QRectF, tint: QColor) -> None:
    """A small spreadsheet: a header row in the format's colour and a grid."""
    painter.save()
    width, height = box.width() * 0.7, box.height() * 0.74
    grid = QRectF(box.center().x() - width / 2, box.center().y() - height / 2, width, height)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#FFFFFF"))
    painter.drawRect(grid)
    head = QColor(tint)
    head.setAlpha(200)
    painter.setBrush(head)
    painter.drawRect(QRectF(grid.left(), grid.top(), grid.width(), grid.height() / 6))
    line = QColor(tint)
    line.setAlpha(90)
    painter.setPen(QPen(line, 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for i in range(1, 6):
        y = grid.top() + grid.height() * i / 6
        painter.drawLine(QPointF(grid.left(), y), QPointF(grid.right(), y))
    for i in range(1, 4):
        x = grid.left() + grid.width() * i / 4
        painter.drawLine(QPointF(x, grid.top()), QPointF(x, grid.bottom()))
    painter.drawRect(grid)
    painter.restore()


class FileDelegate(QStyledItemDelegate):
    """Paints a row of the list or a cell of the gallery."""

    def __init__(self, pane: "FileListPane") -> None:
        super().__init__(pane)
        self._pane = pane

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        row: FileRow = index.data(RowRole)
        if self._pane.view_mode() == GALLERY:
            return self._pane.view.gridSize()
        if row is None or row.is_header:
            return QSize(option.rect.width(), HEADER_HEIGHT)
        height = LIST_ROW_FOLDER_HEIGHT if self._pane.spans_folders() else LIST_ROW_HEIGHT
        return QSize(option.rect.width(), height)

    def _colors(self, option, selected: bool):
        palette = option.palette
        dark = self._pane.is_dark()
        focused = self._pane.view.hasFocus()
        text = palette.color(QPalette.ColorRole.Text)
        muted = palette.color(QPalette.ColorRole.PlaceholderText)
        fill = None
        if selected:
            fill = theme.list_selection_color(dark) if focused else theme.inactive_selection_color(dark)
            if focused:
                text = muted = QColor("#FFFFFF")
        return fill, text, muted

    def paint(self, painter: QPainter, option, index) -> None:
        row: FileRow = index.data(RowRole)
        if row is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if row.is_header:
            font = QFont(option.font)
            font.setBold(True)
            font.setPointSizeF(max(8.0, option.font.pointSizeF() - 1.0))
            painter.setFont(font)
            painter.setPen(option.palette.color(QPalette.ColorRole.Text))
            painter.drawText(option.rect.adjusted(16, 8, -8, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, row.title)
        elif self._pane.view_mode() == GALLERY:
            self._paint_cell(painter, option, row.entry)
        else:
            self._paint_row(painter, option, row.entry)
        painter.restore()

    def _paint_row(self, painter: QPainter, option, entry: Entry) -> None:
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        fill, text, muted = self._colors(option, selected)
        card = QRectF(rect.adjusted(8, 1, -8, -1))
        if fill is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(card, 8, 8)
        else:
            painter.setPen(QPen(option.palette.color(QPalette.ColorRole.Mid), 0.6))
            painter.drawLine(QPointF(card.left() + 8, rect.bottom() + 0.5),
                             QPointF(card.right() - 8, rect.bottom() + 0.5))
        thumb = QRectF(card.left() + 8, rect.top() + (LIST_ROW_HEIGHT - THUMB) / 2, THUMB, THUMB)
        self._paint_thumb(painter, thumb, entry, option, small=True)

        left = int(thumb.right()) + 10
        width = max(0, int(card.right()) - 8 - left)
        bold = QFont(option.font)
        bold.setBold(True)
        small = QFont(option.font)
        small.setPointSizeF(max(8.0, option.font.pointSizeF() - 1.0))
        painter.setFont(bold)
        painter.setPen(text)
        name = QFontMetrics(bold).elidedText(display_name(entry), Qt.TextElideMode.ElideMiddle, width)
        painter.drawText(QRect(left, rect.top() + 9, width, 18),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        painter.setFont(small)
        metrics = QFontMetrics(small)
        moment = self._pane.when_text(entry)
        moment_width = metrics.horizontalAdvance(moment)
        painter.setPen(text)
        painter.drawText(QRect(left, rect.top() + 29, width, 16),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, moment)
        fact = self._pane.fact(entry)
        if fact:
            painter.setPen(muted)
            room = max(0, width - moment_width - 8)
            painter.drawText(QRect(left + moment_width + 8, rect.top() + 29, room, 16),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             metrics.elidedText(fact, Qt.TextElideMode.ElideRight, room))
        if self._pane.spans_folders():
            folder_rect = QRectF(left, rect.top() + 49, 12, 12)
            library_actions.paint_folder(painter, folder_rect, muted)
            painter.setPen(muted)
            folder = os.path.basename(os.path.dirname(entry.path))
            painter.drawText(QRect(left + 16, rect.top() + 47, width - 16, 16),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             metrics.elidedText(folder, Qt.TextElideMode.ElideMiddle, width - 16))

    def _paint_cell(self, painter: QPainter, option, entry: Entry) -> None:
        rect = QRectF(option.rect).adjusted(4, 4, -4, -4)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        fill, text, muted = self._colors(option, selected)
        if fill is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, 9, 9)
        box = QRectF(rect.left() + 6, rect.top() + 6, rect.width() - 12, GALLERY_THUMB_HEIGHT)
        self._paint_thumb(painter, box, entry, option, small=False)
        font = QFont(option.font)
        font.setPointSizeF(max(8.0, option.font.pointSizeF() - 1.0))
        painter.setFont(font)
        metrics = QFontMetrics(font)
        painter.setPen(text)
        name_rect = QRectF(rect.left() + 4, box.bottom() + 6, rect.width() - 8, metrics.height())
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignHCenter,
                         metrics.elidedText(display_name(entry), Qt.TextElideMode.ElideMiddle,
                                            int(name_rect.width())))
        painter.setPen(muted)
        painter.drawText(QRectF(name_rect.left(), name_rect.bottom() + 1, name_rect.width(),
                                metrics.height()),
                         Qt.AlignmentFlag.AlignHCenter, self._pane.when_text(entry))

    def _paint_thumb(self, painter: QPainter, box: QRectF, entry: Entry, option,
                     small: bool) -> None:
        pixmap = self._pane.thumbnail(entry)
        if pixmap is not None:
            _cover(painter, box, pixmap, 6)
            return
        if small or entry.is_dir or entry.kind not in (
                file_kinds.NOTE, file_kinds.TEXT, file_kinds.PDF, file_kinds.SHEET):
            if small:
                _tile(painter, box, entry, 6, 18)
            else:
                painter.save()
                painter.setPen(QPen(option.palette.color(QPalette.ColorRole.Mid), 1))
                painter.setBrush(option.palette.color(QPalette.ColorRole.Base))
                painter.drawRoundedRect(box, 6, 6)
                painter.restore()
                side = 48
                _tile(painter, QRectF(box.center().x() - side / 2, box.center().y() - side / 2,
                                      side, side), entry, 10, 26)
            return
        painter.save()
        painter.setPen(QPen(option.palette.color(QPalette.ColorRole.Mid), 1))
        painter.setBrush(option.palette.color(QPalette.ColorRole.Base))
        painter.drawRoundedRect(box, 6, 6)
        painter.restore()
        if entry.kind == file_kinds.SHEET:
            _grid(painter, box, file_kinds.format_tint(entry.path))
        elif entry.kind == file_kinds.PDF:
            _page(painter, box, option.palette, "", tag="PDF")
        else:
            _page(painter, box, option.palette, display_name(entry))


# -- the view ---------------------------------------------------------------------

class _FileView(QListView):
    """The list (or gallery): keys, drags and drops go to the pane."""

    def __init__(self, pane: "FileListPane") -> None:
        super().__init__(pane)
        self._pane = pane

    def event(self, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ShortcutOverride and self._pane._own_key(event):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self._pane._key(event):
            super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        index = self.indexAt(event.position().toPoint())
        row = self.model().row(index.row()) if index.isValid() else None
        if row is not None and row.entry is not None:
            self._pane.activate(row.entry.path)
            return
        super().mouseDoubleClickEvent(event)

    def startDrag(self, actions) -> None:  # noqa: N802
        paths = self._pane.selected_paths()
        if not paths:
            return
        drag = QDrag(self)
        drag.setMimeData(library_actions.mime_for(paths))
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        self._pane._drag_move(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        self._pane._drag_move(event)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._pane._set_drop_highlight(False)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._pane._drop(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._pane._drop_highlight:
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(theme.list_selection_color(self._pane.is_dark()), 2, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.viewport().rect()).adjusted(4, 4, -4, -4), 8, 8)
            painter.end()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._pane._layout_gallery()
        self._pane._place_empty_label()

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.viewport().update()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.viewport().update()


STYLE = """
QWidget#file_list_pane { background: palette(base); }
QListView#file_list_view { background: palette(base); border: none; }
QLabel#file_list_title { font-weight: 600; font-size: 14px; }
QLabel#file_list_subtitle, QLabel#file_list_empty { color: palette(placeholder-text); }
QToolButton#file_list_button { border: none; border-radius: 5px; padding: 3px; }
QToolButton#file_list_button:checked { background: palette(alternate-base); }
QLineEdit#file_list_search {
    background: palette(alternate-base); border: 1px solid transparent;
    border-radius: 7px; padding: 3px 6px;
}
QLineEdit#file_list_search:focus { border-color: palette(highlight); }
"""


class FileListPane(QWidget):
    """The middle column: the files of a folder or a Library section.

    Signals:
      file_selected(str path)               a file was chosen (show it)
      file_activated(str path)              double click or Return (open it)
      quick_look_requested(str path)        Space
      new_note_requested(str folder)        the New Note button
      insert_into_note_requested(str path)  Insert into Note (the real path)
      import_as_note_requested(str path)    Import as Note (.docx, .odt)
      folder_opened(str path)               the list went into a folder
      status_message(str)                   a quiet word after a change
    """

    MIN_WIDTH = 240
    DEFAULT_WIDTH = 300
    MAX_WIDTH = 480

    file_selected = Signal(str)
    file_activated = Signal(str)
    quick_look_requested = Signal(str)
    new_note_requested = Signal(str)
    insert_into_note_requested = Signal(str)
    import_as_note_requested = Signal(str)
    # A text file MyEditor shows another way (a CSV table, an SVG
    # picture), to be opened as its text.
    open_as_text_requested = Signal(str)
    folder_opened = Signal(str)
    status_message = Signal(str)

    def __init__(self, library: LibrarySet, is_dark: Optional[bool] = None, parent=None) -> None:
        super().__init__(parent)
        self.library = library
        self._dark = theme.is_dark_active() if is_dark is None else bool(is_dark)
        # The open notes with unsaved changes; the host sets this, and
        # their links are left to the editor.
        self.unsaved_notes: Callable[[], Set[str]] = library_actions.no_unsaved_notes
        self._mode = RECENTS            # a section, or FOLDER_MODE
        self._folder: Optional[str] = None
        self._view_mode = LIST
        self._scope = ALL
        self._query = ""
        self._entries: List[Entry] = []
        self._spans = True
        self._quiet = False
        self._drop_highlight = False
        self._now: Optional[float] = None          # tests fix the clock

        self.setObjectName("file_list_pane")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setMaximumWidth(self.MAX_WIDTH)
        self.setStyleSheet(STYLE)

        self.reader = _Reader(self)
        self.reader.ready.connect(self._read)
        # Every note's first line read, by its path and when it changed,
        # for the search (SEARCH_STORE_LIMIT).
        self._search_words: "OrderedDict[tuple, str]" = OrderedDict()
        self.reader.on_result = self._remember_for_search

        self.title_label = QLabel()
        self.title_label.setObjectName("file_list_title")
        self.title_label.setWordWrap(True)
        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("file_list_subtitle")
        self.subtitle_label.setWordWrap(True)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)

        self.list_button = self._button(_("List"), _("Show as List"))
        self.gallery_button = self._button(_("Gallery"), _("Show as Gallery"))
        self.list_button.setCheckable(True)
        self.gallery_button.setCheckable(True)
        self.list_button.setChecked(True)
        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        self._view_group.addButton(self.list_button)
        self._view_group.addButton(self.gallery_button)
        self.list_button.clicked.connect(lambda: self.set_view_mode(LIST))
        self.gallery_button.clicked.connect(lambda: self.set_view_mode(GALLERY))
        self.new_note_button = self._button(_("New Note"), _("New Note"))
        self.new_note_button.clicked.connect(self._new_note)

        head = QHBoxLayout()
        head.setContentsMargins(16, 10, 8, 6)
        head.setSpacing(4)
        head.addLayout(titles, 1)
        head.addWidget(self.list_button, 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(self.gallery_button, 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(self.new_note_button, 0, Qt.AlignmentFlag.AlignTop)

        self.search = QLineEdit()
        self.search.setObjectName("file_list_search")
        self.search.setPlaceholderText(_("Search"))
        self.search.setAccessibleName(_("Search Files"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._search_changed)
        self.search.installEventFilter(self)

        self.scope = SegmentedControl(dark=self._dark, parent=self)
        self.scope.set_segments([
            (ALL, _("All"), _("every file")),
            (SCOPE_NOTES, _("Notes"), _("notes and text")),
            (SCOPE_MEDIA, _("Media"), _("pictures, films and sound")),
            # The sidebar's word for the same files.
            (SCOPE_FILES, _("Documents"), _("PDFs, spreadsheets, documents and the rest")),
        ])
        self.scope.set_current(ALL)
        self.scope.chosen.connect(self.set_scope)
        self.scope.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Every label whole, as Apple's segmented controls show them: the
        # control never narrower than its labels side by side (each segment
        # then keeps its label's width), the list never narrower than the
        # control and its margins.
        for segment in self.scope.segments:
            segment.setMinimumWidth(segment.label_width())
        labels = sum(segment.label_width() for segment in self.scope.segments) + 4
        self.scope.setMinimumWidth(labels)
        self.setMinimumWidth(max(self.MIN_WIDTH, labels + 24))

        self.model_ = FileModel(self)
        self.view = _FileView(self)
        self.view.setObjectName("file_list_view")
        self.view.setAccessibleName(_("Files"))
        self.view.setModel(self.model_)
        self.view.setItemDelegate(FileDelegate(self))
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setFrameShape(QListView.Shape.NoFrame)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setLayoutMode(QListView.LayoutMode.Batched)
        self.view.setBatchSize(200)
        self.view.setDragEnabled(True)
        self.view.setAcceptDrops(True)
        self.view.viewport().setAcceptDrops(True)
        self.view.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.view.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.selectionModel().currentChanged.connect(self._current_changed)

        self.empty_label = QLabel(self.view.viewport())
        self.empty_label.setObjectName("file_list_empty")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.empty_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.empty_label.hide()
        # Offered in an empty folder: the next thing to do there.
        self.empty_new_note = QPushButton(_("New Note"), self.view.viewport())
        self.empty_new_note.setAccessibleName(_("New Note"))
        self.empty_new_note.clicked.connect(self._new_note)
        self.empty_new_note.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # The titles and buttons: the band lined up with the window's
        # toolbar beside it (align_bands).
        self.head_band = QWidget(self)
        self.head_band.setLayout(head)
        layout.addWidget(self.head_band)
        filters = QVBoxLayout()
        filters.setContentsMargins(12, 0, 12, 6)
        filters.setSpacing(6)
        filters.addWidget(self.search)
        filters.addWidget(self.scope)
        layout.addLayout(filters)
        # Tab goes along the head's buttons, then to the search field, the
        # scope and the list, as they are laid out.
        self.setTabOrder(self.list_button, self.gallery_button)
        self.setTabOrder(self.gallery_button, self.new_note_button)
        self.setTabOrder(self.new_note_button, self.search)
        self.setTabOrder(self.search, self.scope.segments[0])
        for first, second in zip(self.scope.segments, self.scope.segments[1:]):
            self.setTabOrder(first, second)
        self.setTabOrder(self.scope.segments[-1], self.view)
        layout.addWidget(self.view, 1)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self.refresh)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._apply)
        library.changed.connect(self._library_changed)
        library.index_ready.connect(self._index_ready)
        library.roots_changed.connect(self._refresh_timer.start)

        self._paint_icons()
        self.show_section(RECENTS)

    # -- building ----------------------------------------------------------------

    def _button(self, name: str, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("file_list_button")
        button.setAccessibleName(name)
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.setIconSize(QSize(18, 18))
        return button

    def _paint_icons(self) -> None:
        ink = self.palette().color(QPalette.ColorRole.Text)
        self.list_button.setIcon(format_icons.bulleted_list(ink))
        self.gallery_button.setIcon(format_icons.table(ink))
        self.new_note_button.setIcon(format_icons.plus(ink))

    # -- public --------------------------------------------------------------------

    def align_bands(self, toolbar_height: int, _search_height: int) -> None:
        """The head as tall as the window's toolbar beside it, so the line
        under them runs straight across (window_grid.BandAligner); 0
        gives it its own height."""
        self.head_band.setMinimumHeight(max(0, int(toolbar_height)))

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.DEFAULT_WIDTH, super().sizeHint().height())

    def is_dark(self) -> bool:
        return self._dark

    def update_theme(self, is_dark: bool) -> None:
        self._dark = bool(is_dark)
        self.scope.set_dark(self._dark)
        self._paint_icons()
        self.view.viewport().update()

    def view_mode(self) -> str:
        return self._view_mode

    def spans_folders(self) -> bool:
        """Whether the list holds files of many folders (a Library section),
        so each row names its folder."""
        return self._spans

    def current_folder(self) -> Optional[str]:
        """The folder shown, or None for a Library section."""
        return self._folder if self._mode == FOLDER_MODE else None

    def current_section(self) -> Optional[str]:
        return None if self._mode == FOLDER_MODE else self._mode

    def show_folder(self, path: str) -> None:
        """List the files of the folder ``path``."""
        path = os.path.abspath(path)
        changed = (self._mode, self._folder) != (FOLDER_MODE, path)
        self._mode, self._folder = FOLDER_MODE, path
        self.refresh(keep=not changed)

    def show_section(self, kind: str) -> None:
        """List a Library section across every folder: "recents",
        "notes", "media" or "documents"."""
        if kind not in (RECENTS, NOTES, MEDIA, DOCUMENTS):
            raise ValueError(kind)
        changed = self._mode != kind
        self._mode, self._folder = kind, None
        self.refresh(keep=not changed)

    def set_view_mode(self, mode: str) -> None:
        """LIST or GALLERY."""
        if mode not in (LIST, GALLERY):
            raise ValueError(mode)
        self.list_button.setChecked(mode == LIST)
        self.gallery_button.setChecked(mode == GALLERY)
        if mode == self._view_mode:
            return
        self._view_mode = mode
        if mode == GALLERY:
            self.view.setViewMode(QListView.ViewMode.IconMode)
            self.view.setMovement(QListView.Movement.Static)
            self.view.setResizeMode(QListView.ResizeMode.Adjust)
            self.view.setWrapping(True)
            self.view.setUniformItemSizes(True)
            self._layout_gallery()
        else:
            self.view.setViewMode(QListView.ViewMode.ListMode)
            self.view.setGridSize(QSize())
            self.view.setUniformItemSizes(False)
        self.view.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self._apply()

    def set_scope(self, scope: str) -> None:
        if scope not in (ALL, SCOPE_NOTES, SCOPE_MEDIA, SCOPE_FILES):
            raise ValueError(scope)
        self._scope = scope
        if self.scope.current() != scope:
            self.scope.set_current(scope)
        self._apply()

    def selected_paths(self) -> List[str]:
        """The chosen files and folders, in list order."""
        rows = sorted(index.row() for index in self.view.selectionModel().selectedIndexes())
        return [self.model_.row(r).entry.path for r in rows
                if self.model_.row(r) is not None and self.model_.row(r).entry is not None]

    def select_path(self, path: str) -> bool:
        """Choose ``path`` quietly (no ``file_selected``), going to its
        folder when the list does not hold it. False when it is nowhere."""
        path = os.path.abspath(path)
        if self.model_.row_of(path) < 0:
            folder = os.path.dirname(path)
            if not os.path.isdir(folder) or self.library.library_for(folder) is None:
                return False
            if self.current_folder() != folder:
                self._mode, self._folder = FOLDER_MODE, folder
                self.refresh(keep=False)
            if self.model_.row_of(path) < 0 and (self._scope != ALL or self._query):
                self.search.clear()
                self.set_scope(ALL)
        number = self.model_.row_of(path)
        if number < 0:
            return False
        self._select_rows([number], quiet=True)
        return True

    def refresh(self, keep: bool = True) -> None:
        """Read the folder or section again; with ``keep``, the chosen
        files and the scroll position stay."""
        self._refresh_timer.stop()
        if self._mode == FOLDER_MODE:
            folder = self._folder or ""
            self._spans = False
            # Its subfolders too, as rows to open (the Finder's way): also a
            # sidebar folder's, which the sidebar may show closed.
            self._entries = self.library.entries(folder) if os.path.isdir(folder) else []
            title = os.path.basename(folder) or folder
            watched = [folder]
        else:
            self._spans = True
            if self._mode == RECENTS:
                self._entries = self.library.recent(RECENTS_LIMIT)
            else:
                self._entries = self.library.all_files(set(SECTION_KINDS[self._mode]))
            title = {RECENTS: _("Recents"), NOTES: _("Notes"), MEDIA: _("Media"),
                     DOCUMENTS: _("Documents")}[self._mode]
            watched = []
        self.title_label.setText(title)
        self.library.watch(watched)
        self._apply(keep=keep)

    def shutdown(self) -> None:
        """Wait for the reading threads (before the pane goes)."""
        self.reader.shutdown()

    # -- texts the rows show -----------------------------------------------------------

    def when_text(self, entry: Entry) -> str:
        return when(entry.mtime, self._now)

    def fact(self, entry: Entry, request: bool = True) -> str:
        """The second line's fact: a note's first line, a picture's size
        in pixels, else the size on the disk."""
        if entry.is_dir:
            return _("Folder")
        if entry.kind in (file_kinds.NOTE, file_kinds.TEXT):
            key = ("snippet", entry.path, entry.mtime)
            title = display_name(entry)
            found = (self.reader.get(key, lambda p=entry.path, t=title: note_snippet(p, t))
                     if request else self.reader.known(key))
            return found or ""
        if entry.kind == file_kinds.IMAGE:
            key = ("pixels", entry.path, entry.mtime)
            found = (self.reader.get(key, lambda p=entry.path: picture_size(p))
                     if request else self.reader.known(key))
            if found:
                return found
        if entry.size == UNMEASURED:
            # A package in a folder's listing: measured on a worker, once.
            key = ("size", entry.path, entry.mtime)
            found = (self.reader.get(key, lambda p=entry.path: media_files.format_size(
                         file_kinds.package_size(p)))
                     if request else self.reader.known(key))
            return found or ""
        return media_files.format_size(entry.size)

    def thumbnail(self, entry: Entry) -> Optional[QPixmap]:
        if entry.is_dir and not file_kinds.is_package(entry.path):
            return None
        if entry.kind == file_kinds.IMAGE:
            work = lambda p=entry.path: scaled_picture(p)      # noqa: E731
        elif entry.kind in OFFICE_PREVIEW_KINDS:
            # The preview picture an office app stores in its file.
            work = lambda p=entry.path: office_thumbnail(p)    # noqa: E731
        else:
            return None
        key = ("thumb", entry.path, entry.mtime)
        found = self.reader.get(key, work)
        return found if isinstance(found, QPixmap) and not found.isNull() else None

    def _read(self, path: str) -> None:
        self.model_.path_changed(path)
        if self._query:
            self._search_timer.start()

    # -- filtering and grouping -------------------------------------------------------

    def _remember_for_search(self, key: tuple, result) -> None:
        """Keep a note's first line, read for any reason, for searching."""
        if key[0] != "snippet" or not isinstance(result, str):
            return
        store = self._search_words
        store[(key[1], key[2])] = result.casefold()
        store.move_to_end((key[1], key[2]))
        while len(store) > SEARCH_STORE_LIMIT:
            store.popitem(last=False)

    def _matches(self, entry: Entry, query: str) -> bool:
        if query in display_name(entry).casefold() or query in entry.name.casefold():
            return True
        if entry.kind in (file_kinds.NOTE, file_kinds.TEXT):
            words = self._search_words.get((entry.path, entry.mtime))
            return bool(words) and query in words
        return False

    def _filtered(self) -> List[Entry]:
        entries = self._entries
        if self._scope != ALL:
            kinds = SCOPE_KINDS[self._scope]
            entries = [e for e in entries if not e.is_dir and e.kind in kinds]
        if self._query:
            query = self._query.casefold()
            self._read_for_search(entries)
            entries = [e for e in entries if self._matches(e, query)]
        return entries

    def _read_for_search(self, entries: Iterable[Entry]) -> None:
        """Ask for the first lines of the notes searched, so a search
        finds words inside notes too (the results follow as they come)."""
        asked = 0
        for entry in entries:
            if entry.kind not in (file_kinds.NOTE, file_kinds.TEXT):
                continue
            if (entry.path, entry.mtime) in self._search_words:
                continue                         # read already: never again
            key = ("snippet", entry.path, entry.mtime)
            known = self.reader.known(key)
            if known is not None:
                self._remember_for_search(key, known)
                continue
            self.fact(entry)
            asked += 1
            if asked >= SEARCH_READ_LIMIT:
                break

    def _rows_for(self, entries: List[Entry]) -> List[FileRow]:
        folders = sorted((e for e in entries if e.is_dir), key=lambda e: natural_key(e.name))
        files = sorted((e for e in entries if not e.is_dir), key=lambda e: e.mtime, reverse=True)
        if self._view_mode == GALLERY:
            return [FileRow(entry=e) for e in folders + files]
        rows: List[FileRow] = []
        if folders:
            rows.append(FileRow(title=_("Folders")))
            rows += [FileRow(entry=e) for e in folders]
        group = None
        for entry in files:
            key, title = date_group(entry.mtime, self._now)
            if key != group:
                group = key
                rows.append(FileRow(title=title))
            rows.append(FileRow(entry=entry))
        return rows

    def _apply(self, keep: bool = True) -> None:
        """Show the entries through the scope and the search, grouped."""
        self._search_timer.stop()
        chosen = self.selected_paths() if keep else []
        current = self._current_path() if keep else None
        scroll = self.view.verticalScrollBar().value() if keep else 0
        entries = self._filtered()
        self.model_.set_rows(self._rows_for(entries))
        count = len(entries)
        # The files, as the sidebar counts them; folders are rows to open.
        files = sum(1 for e in entries if not e.is_dir)
        self.subtitle_label.setText(ngettext("{n} file", "{n} files", files).format(n=files))
        self._update_empty(count)
        rows = [self.model_.row_of(p) for p in chosen]
        rows = [r for r in rows if r >= 0]
        if rows:
            current_row = self.model_.row_of(current) if current else -1
            self._select_rows(rows, quiet=True, current=current_row if current_row >= 0 else None)
        self.view.doItemsLayout()
        self.view.verticalScrollBar().setValue(scroll)

    def _update_empty(self, count: int) -> None:
        """What an empty list says: no results for a search, nothing of
        the kind chosen when the scope hides what is there, an empty
        folder (with New Note, the next thing to do there), or how to
        begin without a folder."""
        offer_note = False
        if count:
            self.empty_label.hide()
            self.empty_new_note.hide()
            return
        if self._query:
            text = _("No results for “{query}”.").format(query=self._query)
        elif self._scope != ALL and self._entries:
            text = _("Nothing of this kind is here.")
        elif self._mode == FOLDER_MODE and self._folder:
            text = _("This folder is empty.")
            offer_note = True
        elif self.library.roots():
            text = _("Nothing of this kind is here.")
        else:
            text = _("Add a folder to the sidebar to see its files here.")
        self.empty_label.setText(text)
        self.empty_new_note.setVisible(offer_note)
        self._place_empty_label()
        self.empty_label.show()

    def _place_empty_label(self) -> None:
        width = max(0, self.view.viewport().width() - 32)
        self.empty_label.setFixedWidth(width)
        self.empty_label.adjustSize()
        self.empty_label.move(16, 20)
        self.empty_new_note.adjustSize()
        self.empty_new_note.move(16, 20 + self.empty_label.height() + 10)

    def _layout_gallery(self) -> None:
        if self._view_mode != GALLERY:
            return
        # Qt lays an icon grid out leaving room for a scroll bar that may
        # come (QListView, a flow from left to right with the bar as
        # needed): the columns are measured in the same room, or the last
        # one folds under the first while there is no bar.
        room = self.view.viewport().width()
        bar = self.view.verticalScrollBar()
        if not bar.isVisible():
            room -= bar.sizeHint().width()
        width = max(GALLERY_MIN_CELL, room - 8)
        columns = max(1, width // GALLERY_MIN_CELL)
        cell = max(GALLERY_MIN_CELL, width // columns)
        size = QSize(cell, GALLERY_CELL_HEIGHT)
        if self.view.gridSize() != size:
            self.view.setGridSize(size)

    # -- selection ------------------------------------------------------------------

    def _current_path(self) -> Optional[str]:
        row = self.model_.row(self.view.currentIndex().row())
        return row.entry.path if row is not None and row.entry is not None else None

    def _select_rows(self, rows: List[int], quiet: bool, current: Optional[int] = None) -> None:
        selection = QItemSelection()
        for number in rows:
            index = self.model_.index(number)
            selection.select(index, index)
        was, self._quiet = self._quiet, quiet
        try:
            model = self.view.selectionModel()
            model.setCurrentIndex(self.model_.index(current if current is not None else rows[0]),
                                  QItemSelectionModel.SelectionFlag.NoUpdate)
            model.select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            self.view.scrollTo(self.model_.index(current if current is not None else rows[0]))
        finally:
            self._quiet = was

    def _current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if self._quiet:
            return
        row = self.model_.row(current.row())
        if row is not None and row.entry is not None and not row.entry.is_dir:
            self.file_selected.emit(row.entry.path)

    def activate(self, path: str) -> None:
        """Open ``path``: go into a folder, else ask the host to open the file."""
        if os.path.isdir(path) and not file_kinds.is_package(path):
            self.show_folder(path)
            self.folder_opened.emit(path)
        else:
            self.file_activated.emit(path)

    # -- keys -----------------------------------------------------------------------

    def _is_trash_key(self, event) -> bool:
        key, mods = event.key(), event.modifiers()
        if key == Qt.Key.Key_Delete:
            return True
        return key == Qt.Key.Key_Backspace and bool(mods & Qt.KeyboardModifier.ControlModifier)

    def _own_key(self, event) -> bool:
        return (event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)
                or self._is_trash_key(event))

    def _key(self, event) -> bool:
        path = self._current_path()
        if event.key() == Qt.Key.Key_Space and not event.modifiers():
            if path and (not os.path.isdir(path) or file_kinds.is_package(path)):
                self.quick_look_requested.emit(path)
            return True
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if path:
                self.activate(path)
            return True
        if self._is_trash_key(event):
            self.trash_selected()
            return True
        return False

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape and self.search.text():
                self.search.clear()
                return True
            if event.key() == Qt.Key.Key_Down:
                self.view.setFocus()
                if self.model_.entries() and not self.selected_paths():
                    first = next(i for i, r in enumerate(self.model_.rows()) if r.entry)
                    self.view.setCurrentIndex(self.model_.index(first))
                return True
        return super().eventFilter(watched, event)

    def _search_changed(self, text: str) -> None:
        self._query = text.strip()
        self._apply()

    # -- operations ----------------------------------------------------------------

    def _target_folder(self) -> Optional[str]:
        """Where new and dropped files go: the folder shown, or the first
        sidebar folder for a Library section."""
        folder = self.current_folder()
        if folder:
            return folder
        roots = self.library.roots()
        return roots[0] if roots else None

    def _new_note(self) -> None:
        folder = self._target_folder()
        if folder:
            self.new_note_requested.emit(folder)

    def _say(self, text: str) -> None:
        self.status_message.emit(text)

    def _after(self, select: Optional[str]) -> None:
        self.refresh()
        if select:
            self.select_path(select)

    def rename_path(self, path: str) -> Optional[str]:
        """Rename ``path``; the list shows and selects it under its new name
        when the rename is made (at once, or after the library's earlier
        operations). The new path when known at once."""
        return library_actions.rename(self, self.library, path, self.unsaved_notes, self._say,
                                      done=self._after)

    def duplicate_paths(self, paths: List[str]) -> List[str]:
        made: List[str] = []
        for path in paths:
            made += library_actions.copy_in(self, self.library, [path], os.path.dirname(path))
        if made:
            self._after(made[-1])
        return made

    def move_paths(self, paths: List[str], folder: Optional[str] = None) -> List[str]:
        if folder is None:
            start = self.library.root_of(paths[0]) or os.path.dirname(paths[0])
            folder = library_actions.choose_folder(self, start)
            if not folder:
                return []
        return library_actions.move(self, self.library, paths, folder, self.unsaved_notes,
                                    self._say, done=lambda _where: self.refresh())

    def trash_selected(self) -> bool:
        return self.trash_paths(self.selected_paths())

    def trash_paths(self, paths: List[str]) -> bool:
        if not paths:
            return False
        done = library_actions.trash(self, self.library, paths)
        if done:
            self.refresh(keep=False)
        return done

    # -- the context menu ------------------------------------------------------------

    def menu_for(self, paths: List[str]) -> QMenu:
        """The context menu for ``paths`` (the first one decides the kind)."""
        menu = QMenu(self)
        if not paths:
            folder = self._target_folder()
            if folder:
                act = menu.addAction(_("New Note"))
                act.setObjectName("new_note")
                act.triggered.connect(lambda: self.new_note_requested.emit(folder))
            return menu
        path = paths[0]
        one = len(paths) == 1
        is_folder = os.path.isdir(path) and not file_kinds.is_package(path)
        kind = file_kinds.FOLDER if is_folder else file_kinds.kind_of_path(path)

        def action(name: str, text: str, slot, shortcut: str = "", enabled: bool = True):
            act = menu.addAction(text)
            act.setObjectName(name)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            act.setEnabled(enabled)
            act.triggered.connect(slot)
            return act

        action("open", _("Open"), lambda: self.activate(path), enabled=one)
        if not is_folder:
            action("quick_look", _("Quick Look"), lambda: self.quick_look_requested.emit(path),
                   "Space", enabled=one)
        if kind not in (file_kinds.FOLDER, file_kinds.NOTE, file_kinds.TEXT):
            app = default_apps.default_app_name(path)
            if app:
                action("open_in_app", _("Open in {app}").format(app=app),
                       lambda: default_apps.open_with_default(path), enabled=one)
            submenu = menu.addMenu(_("Open With"))
            submenu.setObjectName("open_with")
            submenu.setEnabled(one)
            submenu.aboutToShow.connect(
                lambda m=submenu: library_actions.fill_open_with(m, path, self))
        if kind in (file_kinds.IMAGE, file_kinds.VIDEO, file_kinds.AUDIO, file_kinds.PDF):
            action("insert_into_note", _("Insert into Note"),
                   lambda: self.insert_into_note_requested.emit(path), enabled=one)
        if (not is_folder and kind not in (file_kinds.NOTE, file_kinds.TEXT, file_kinds.PDF)
                and regular_files.reads_as_text(path)):
            action("open_as_text", _("Open as Text"),
                   lambda: self.open_as_text_requested.emit(path), enabled=one)
        if file_kinds.extension(path) in file_kinds.READING_VIEW_EXTS:
            action("import_as_note", _("Import as Note"),
                   lambda: self.import_as_note_requested.emit(path), enabled=one)
        menu.addSeparator()
        if not is_folder:
            action("duplicate", _("Duplicate"), lambda: self.duplicate_paths(list(paths)))
        action("move_to", _("Move to…"), lambda: self.move_paths(list(paths)))
        menu.addSeparator()
        action("reveal", default_apps.reveal_label(), lambda: library_actions.reveal(path),
               enabled=one)
        action("copy_path", _("Copy Path"), lambda: library_actions.copy_paths(paths))
        menu.addSeparator()
        action("rename", _("Rename…"), lambda: self.rename_path(path), enabled=one)
        action("trash", _("Move to Trash"), lambda: self.trash_paths(list(paths)), "Ctrl+Backspace")
        return menu

    def _context_menu(self, pos: QPoint) -> None:
        index = self.view.indexAt(pos)
        row = self.model_.row(index.row()) if index.isValid() else None
        if row is not None and row.entry is not None:
            if row.entry.path not in self.selected_paths():
                self._select_rows([index.row()], quiet=False)
            paths = self.selected_paths()
        else:
            paths = []
        self.menu_for(paths).exec(self.view.viewport().mapToGlobal(pos))

    # -- drops ------------------------------------------------------------------------

    def _set_drop_highlight(self, on: bool) -> None:
        if on != self._drop_highlight:
            self._drop_highlight = on
            self.view.viewport().update()

    def _drop_plan(self, mime) -> Tuple[Optional[str], List[str], List[str]]:
        """``(folder, to move, to copy)`` for a drop on the list."""
        folder = self._target_folder()
        inside, outside = library_actions.dropped(mime)
        if folder is None:
            return None, [], []
        # Files from the list itself, already in this folder, stay.
        inside = [p for p in inside
                  if os.path.dirname(os.path.abspath(p)) != os.path.abspath(folder)]
        if self.current_folder() is None:
            inside = []            # a Library section is no place to move to
        if library_actions.goes_into_itself(inside + outside, folder):
            return None, [], []    # a folder never goes into itself
        return folder, inside, outside

    def _drag_move(self, event) -> None:
        folder, inside, outside = self._drop_plan(event.mimeData())
        if folder is None or not (inside or outside):
            self._set_drop_highlight(False)
            event.ignore()
            return
        self._set_drop_highlight(True)
        event.setDropAction(Qt.DropAction.MoveAction if inside else Qt.DropAction.CopyAction)
        event.accept()

    def _drop(self, event) -> None:
        self._set_drop_highlight(False)
        folder, inside, outside = self._drop_plan(event.mimeData())
        if folder is None or not (inside or outside):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.MoveAction if inside else Qt.DropAction.CopyAction)
        event.accept()
        self.drop_paths(folder, inside, outside)

    def drop_paths(self, folder: str, from_library: List[str],
                   from_outside: List[str]) -> List[str]:
        """Move ``from_library`` and copy ``from_outside`` into ``folder``."""
        done: List[str] = []
        if from_library:
            done += library_actions.move(self, self.library, from_library, folder,
                                         self.unsaved_notes, self._say,
                                         done=lambda where: self._after(where[-1]))
        if from_outside:
            copied = library_actions.copy_in(self, self.library, from_outside, folder)
            if copied:
                self._after(copied[-1])
            done += copied
        return done

    # -- live updates -------------------------------------------------------------------

    def _library_changed(self, folder: str) -> None:
        if self._mode == FOLDER_MODE:
            if not folder or os.path.abspath(folder) == self._folder:
                self._refresh_timer.start()
        elif not folder:
            self._refresh_timer.start()

    def _index_ready(self) -> None:
        if self._mode != FOLDER_MODE:
            self._refresh_timer.start()
