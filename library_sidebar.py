# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The folder sidebar: the Library across every folder, then the folders.

Two levels at most, as Apple's guidelines ask of a sidebar:

    Library
      Recents                     the files changed most lately
      Notes             42        notes and plain text
      Media             17        pictures, films and sound
      Documents          9        PDFs, spreadsheets, documents, the rest

    Folders
    v Writing           12        a folder the person added (its own files)
        Drafts          30        its subfolders (every file in them)
        Research         4
    > Projects           3

Deeper folders are reached through the file list beside the sidebar.
The counts come from the libraries' indexes, so they appear once a
folder has been read (off the GUI thread). Folder icons wear the
system's accent colour, as in the Finder.

Folders take files: dragged from the file list they move (links follow,
library_actions.py), dragged in from the Finder or Explorer they are
copied. A right click offers what can be done to a folder; the top
level adds Remove from Sidebar, which only forgets the folder here.
Nothing critical sits at the bottom edge: with no folder yet, the
sidebar says so at the top and offers Add Folder there.

The widget knows nothing about the window: ``selection_changed`` says
what to show, ``add_folder_requested`` asks the host to pick a folder
(and to remember the list, settings key ``library_folders``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, List, Optional, Set, Tuple

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QListView,
    QMenu,
    QPushButton,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

import file_kinds
import format_icons
import library_actions
import theme
from default_apps import reveal_label
from i18n import _
from library_set import LibrarySet

HEADER, LIBRARY, FOLDER = "header", "library", "folder"
RECENTS, NOTES, MEDIA, DOCUMENTS = "recents", "notes", "media", "documents"

# The kinds each Library row gathers (file_kinds.KINDS).
SECTION_KINDS = {
    NOTES: frozenset({file_kinds.NOTE, file_kinds.TEXT}),
    MEDIA: frozenset({file_kinds.IMAGE, file_kinds.VIDEO, file_kinds.AUDIO}),
    DOCUMENTS: frozenset({file_kinds.PDF, file_kinds.SHEET, file_kinds.DOCUMENT,
                          file_kinds.SLIDES, file_kinds.OTHER}),
}
RECENTS_LIMIT = 50

ROW_HEIGHT = 30
HEADER_HEIGHT = 30
ICON = 16
INDENT = 16
DISCLOSURE = 10


@dataclass(frozen=True)
class Row:
    """One row of the sidebar."""

    kind: str                  # HEADER | LIBRARY | FOLDER
    key: str                   # a section's name, or a folder's path
    title: str
    glyph: str = ""
    count: Optional[int] = None
    level: int = 1             # a folder: 1 for a sidebar folder, 2 for its subfolder
    expandable: bool = False
    expanded: bool = False

    @property
    def selectable(self) -> bool:
        return self.kind != HEADER

    @property
    def is_root(self) -> bool:
        return self.kind == FOLDER and self.level == 1


RowRole = Qt.ItemDataRole.UserRole + 1


class SidebarModel(QAbstractListModel):
    """The rows. The same rows in the same order only repaint; anything
    else rebuilds (the view keeps the choice, LibrarySidebar._restore)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: List[Row] = []

    def set_rows(self, rows: List[Row]) -> None:
        if [(r.kind, r.key) for r in rows] == [(r.kind, r.key) for r in self._rows]:
            changed = [i for i, (old, new) in enumerate(zip(self._rows, rows)) if old != new]
            self._rows = list(rows)
            for row in changed:
                self.dataChanged.emit(self.index(row), self.index(row))
            return
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rows(self) -> List[Row]:
        return list(self._rows)

    def row(self, number: int) -> Optional[Row]:
        return self._rows[number] if 0 <= number < len(self._rows) else None

    def row_of(self, kind: str, key: str) -> int:
        for number, row in enumerate(self._rows):
            if row.kind == kind and row.key == key:
                return number
        return -1

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def flags(self, index):
        row = self.row(index.row())
        if row is None or not row.selectable:
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        row = self.row(index.row())
        if row is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return row.title
        if role == RowRole:
            return row
        if role == Qt.ItemDataRole.ToolTipRole and row.kind == FOLDER:
            return row.key
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return accessible_text(row)
        return None


def accessible_text(row: Row) -> str:
    """What a screen reader says of a row: its name, how many items it
    holds, and whether its subfolders show, as one text a translation
    may order as its language does."""
    name, count = row.title, row.count
    if not row.expandable:
        return _("{name}, {count}").format(name=name, count=count) if count else name
    if row.expanded:
        if count:
            return _("{name}, {count}, expanded").format(name=name, count=count)
        return _("{name}, expanded").format(name=name)
    if count:
        return _("{name}, {count}, collapsed").format(name=name, count=count)
    return _("{name}, collapsed").format(name=name)


class SidebarDelegate(QStyledItemDelegate):
    """Paints the rows as the Imports window's sidebar does: a glyph in
    the accent colour, the title, a quiet count; a disclosure triangle
    before a folder with subfolders."""

    def __init__(self, sidebar: "LibrarySidebar") -> None:
        super().__init__(sidebar)
        self._sidebar = sidebar

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        row = index.data(RowRole)
        height = HEADER_HEIGHT if row is not None and row.kind == HEADER else ROW_HEIGHT
        return QSize(option.rect.width(), height)

    def paint(self, painter: QPainter, option, index) -> None:
        row: Row = index.data(RowRole)
        if row is None:
            return
        view = self._sidebar.view
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = option.palette
        rect = option.rect
        if row.kind == HEADER:
            font = QFont(option.font)
            font.setBold(True)
            font.setPointSizeF(max(8.0, option.font.pointSizeF() - 1.5))
            painter.setFont(font)
            painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
            painter.drawText(rect.adjusted(14, 8, -8, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             row.title)
            painter.restore()
            return

        dark = self._sidebar.is_dark()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        focused = view.hasFocus()
        text_color = palette.color(QPalette.ColorRole.Text)
        muted = palette.color(QPalette.ColorRole.PlaceholderText)
        accent = theme.list_selection_color(dark)
        # Glyphs in the bright accent, as the Finder's (the selection fill
        # is too dark for them in the dark theme).
        icon_color = theme.accent_icon_color(dark)
        if selected:
            fill = accent if focused else theme.inactive_selection_color(dark)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(rect.adjusted(6, 1, -6, -1)), 6, 6)
            if focused:
                text_color = muted = icon_color = QColor("#FFFFFF")
        if row.kind == FOLDER and self._sidebar.drop_target() == row.key:
            painter.setPen(QPen(accent, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(rect.adjusted(7, 2, -7, -2)), 6, 6)

        indent = 18 + (INDENT if row.level == 2 else 0)
        if row.expandable:
            _paint_disclosure(painter, QRectF(rect.left() + 5, rect.center().y() - DISCLOSURE / 2 + 1,
                                              DISCLOSURE, DISCLOSURE), muted, row.expanded)
        icon_rect = QRectF(rect.left() + indent, rect.center().y() - ICON / 2 + 1, ICON, ICON)
        if row.kind == FOLDER:
            library_actions.paint_folder(painter, icon_rect, icon_color)
        else:
            format_icons.paint(row.glyph, painter, icon_rect, icon_color)

        font = QFont(option.font)
        trailing = "" if row.count is None or row.count == 0 else str(row.count)
        trailing_width = QFontMetrics(font).horizontalAdvance(trailing) + 4 if trailing else 0
        text_left = int(icon_rect.right()) + 9
        text_rect = QRect(text_left, rect.top(),
                          max(0, rect.right() - 14 - trailing_width - 6 - text_left), rect.height())
        painter.setFont(font)
        painter.setPen(text_color)
        title = QFontMetrics(font).elidedText(row.title, Qt.TextElideMode.ElideMiddle,
                                              text_rect.width())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         title)
        if trailing:
            painter.setPen(muted)
            painter.drawText(QRect(rect.right() - 14 - trailing_width, rect.top(),
                                   trailing_width, rect.height()),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, trailing)
        painter.restore()


def _paint_disclosure(painter: QPainter, rect: QRectF, color: QColor, expanded: bool) -> None:
    """A small chevron: pointing right when closed, down when open."""
    painter.save()
    pen = QPen(color, 1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    c = rect.center()
    h = rect.width() / 4
    path = QPainterPath()
    if expanded:
        path.moveTo(QPointF(c.x() - 2 * h, c.y() - h))
        path.lineTo(QPointF(c.x(), c.y() + h))
        path.lineTo(QPointF(c.x() + 2 * h, c.y() - h))
    else:
        path.moveTo(QPointF(c.x() - h, c.y() - 2 * h))
        path.lineTo(QPointF(c.x() + h, c.y()))
        path.lineTo(QPointF(c.x() - h, c.y() + 2 * h))
    painter.drawPath(path)
    painter.restore()


class _SidebarView(QListView):
    """The list itself: keys and drops go to the sidebar."""

    def __init__(self, sidebar: "LibrarySidebar") -> None:
        super().__init__(sidebar)
        self._sidebar = sidebar

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self._sidebar._key(event):
            super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self._sidebar._press(event.position().toPoint()):
            super().mousePressEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        self._sidebar._drag_move(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        self._sidebar._drag_move(event)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._sidebar._set_drop_target(None)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._sidebar._drop(event)

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.viewport().update()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self.viewport().update()


STYLE = """
QWidget#library_sidebar, QListView#library_sidebar_list, QWidget#library_sidebar_empty {
    background: palette(alternate-base); border: none;
}
QLabel#library_sidebar_empty_text { color: palette(placeholder-text); }
"""


class LibrarySidebar(QWidget):
    """The sidebar of the folder library.

    Signals:
      selection_changed(str kind, str path)  "recents" | "notes" | "media" |
                                             "documents" (path "") or
                                             "folder" (its path)
      activated(str kind, str path)          Return on a row
      add_folder_requested()                 Add Folder… (host picks one)
      note_created(str path)                 New Note made a note
      status_message(str)                    a quiet word after a change
    """

    MIN_WIDTH = 180
    DEFAULT_WIDTH = 232
    MAX_WIDTH = 360

    selection_changed = Signal(str, str)
    activated = Signal(str, str)
    add_folder_requested = Signal()
    note_created = Signal(str)
    status_message = Signal(str)

    def __init__(self, library: LibrarySet, is_dark: Optional[bool] = None, parent=None) -> None:
        super().__init__(parent)
        self.library = library
        self._dark = theme.is_dark_active() if is_dark is None else bool(is_dark)
        # The open notes with unsaved changes; the host sets this, and
        # their links are left to the editor.
        self.unsaved_notes: Callable[[], Set[str]] = library_actions.no_unsaved_notes
        self._collapsed: Set[str] = set()
        self._chosen: Tuple[str, str] = ("", "")
        self._drop_target: Optional[str] = None

        self.setObjectName("library_sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setMaximumWidth(self.MAX_WIDTH)
        self.setStyleSheet(STYLE)

        self.model_ = SidebarModel(self)
        self.view = _SidebarView(self)
        self.view.setObjectName("library_sidebar_list")
        self.view.setAccessibleName(_("Library and folders"))
        self.view.setModel(self.model_)
        self.view.setItemDelegate(SidebarDelegate(self))
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setFrameShape(QListView.Shape.NoFrame)
        self.view.setUniformItemSizes(False)
        self.view.setMouseTracking(True)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setAcceptDrops(True)
        self.view.viewport().setAcceptDrops(True)
        self.view.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.selectionModel().currentChanged.connect(self._on_current)

        self.empty = QWidget(self)
        self.empty.setObjectName("library_sidebar_empty")
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(16, 20, 16, 16)
        empty_layout.setSpacing(10)
        self.empty_label = QLabel(_("Add a folder to keep your notes and files here."))
        self.empty_label.setObjectName("library_sidebar_empty_text")
        self.empty_label.setWordWrap(True)
        self.empty_button = QPushButton(_("Add Folder…"))
        self.empty_button.setAccessibleName(_("Add Folder"))
        self.empty_button.clicked.connect(self.add_folder_requested)
        empty_layout.addWidget(self.empty_label)
        empty_layout.addWidget(self.empty_button, 0, Qt.AlignmentFlag.AlignLeft)
        empty_layout.addStretch(1)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.view)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

        # A burst of changes is one rebuild.
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(0)
        self._rebuild_timer.timeout.connect(self.rebuild)
        library.changed.connect(lambda _folder: self._rebuild_timer.start())
        library.index_ready.connect(self._rebuild_timer.start)
        library.roots_changed.connect(self.rebuild)
        # The first rows are chosen quietly: nobody listens yet. The host
        # asks current() once it is wired.
        self._quiet_start = True
        self.rebuild()
        self._quiet_start = False
        # A click on the row already chosen says it again (the host may
        # show something else meanwhile).
        self.view.clicked.connect(self._on_clicked)

    # -- public ----------------------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.DEFAULT_WIDTH, super().sizeHint().height())

    def is_dark(self) -> bool:
        return self._dark

    def update_theme(self, is_dark: bool) -> None:
        self._dark = bool(is_dark)
        self.view.viewport().update()

    def current(self) -> Tuple[str, str]:
        """The row chosen now: ``(kind, path)``."""
        kind, key = self._chosen
        return (FOLDER, key) if kind == FOLDER else (key, "")

    def select(self, kind: str, path: str = "", *, announce: bool = False) -> bool:
        """Choose the row for ``kind`` (and ``path`` for a folder). A
        folder deeper than the sidebar shows chooses the subfolder that
        holds it, and a closed folder opens to show its subfolder. Says
        ``selection_changed`` only with ``announce``. False when there is
        no such row."""
        if kind == FOLDER:
            target = self._visible_folder_for(path)
            if target is None:
                return False
            return self._choose(FOLDER, target, announce)
        if kind not in (RECENTS, NOTES, MEDIA, DOCUMENTS):
            return False
        return self._choose(LIBRARY, kind, announce)

    def drop_target(self) -> Optional[str]:
        """The folder a drag is over now, highlighted."""
        return self._drop_target

    def expand(self, root: str, expanded: bool = True) -> None:
        root = os.path.abspath(root)
        if expanded:
            self._collapsed.discard(root)
        else:
            self._collapsed.add(root)
        self.rebuild()

    def drop_paths(self, folder: str, from_library: List[str], from_outside: List[str],
                   copy: bool = False) -> List[str]:
        """Put dropped files into ``folder``: those from the app's lists
        move (or are copied with ``copy``, the Option key), those from
        elsewhere are copied in. Returns where they are now."""
        done: List[str] = []
        if from_library:
            if copy:
                done += library_actions.copy_in(self, self.library, from_library, folder)
            else:
                done += library_actions.move(self, self.library, from_library, folder,
                                             self.unsaved_notes, self.status_message.emit)
        if from_outside:
            done += library_actions.copy_in(self, self.library, from_outside, folder)
        return done

    # -- rows ------------------------------------------------------------------

    def rebuild(self) -> None:
        """Read the folders and counts again and keep the choice."""
        self._rebuild_timer.stop()
        roots = self.library.roots()
        self.stack.setCurrentWidget(self.view if roots else self.empty)
        counts = self.library.counts()
        direct = self._folder_counts()

        def total(kinds) -> int:
            return sum(counts.get(kind, 0) for kind in kinds)

        rows = [
            Row(HEADER, "library", _("Library")),
            Row(LIBRARY, RECENTS, _("Recents"), "clock",
                min(RECENTS_LIMIT, sum(counts.values())) or None),
            Row(LIBRARY, NOTES, _("Notes"), "article", total(SECTION_KINDS[NOTES])),
            Row(LIBRARY, MEDIA, _("Media"), "photo", total(SECTION_KINDS[MEDIA])),
            Row(LIBRARY, DOCUMENTS, _("Documents"), "document", total(SECTION_KINDS[DOCUMENTS])),
            Row(HEADER, "folders", _("Folders")),
        ]
        for root in roots:
            subs = self.library.subfolders(root)
            expanded = bool(subs) and root not in self._collapsed
            rows.append(Row(FOLDER, root, os.path.basename(root) or root, count=direct.get(root, 0),
                            level=1, expandable=bool(subs), expanded=expanded))
            if expanded:
                for sub in subs:
                    rows.append(Row(FOLDER, sub, os.path.basename(sub), count=direct.get(sub, 0),
                                    level=2))
        self.model_.set_rows(rows)
        self._restore()

    def _folder_counts(self):
        """The files directly in each folder (its own, not its subfolders'),
        from the indexes: what the file list shows as files when the
        folder is chosen, so a count means one thing everywhere."""
        direct = {}
        for library in self.library.libraries():
            if not library.root():
                continue
            for entry in library.all_files():
                folder = os.path.dirname(entry.path)
                direct[folder] = direct.get(folder, 0) + 1
        return direct

    def _visible_folder_for(self, path: str) -> Optional[str]:
        """The sidebar row that stands for the folder ``path``: itself, or
        the subfolder of a sidebar folder that holds it (opening that
        sidebar folder when it is closed)."""
        if not path:
            return None
        path = os.path.abspath(path)
        root = self.library.root_of(path)
        if root is None:
            return None
        if path == root:
            return root
        first = os.path.relpath(path, root).split(os.sep)[0]
        sub = os.path.join(root, first)
        if self.model_.row_of(FOLDER, sub) < 0 and root in self._collapsed:
            self._collapsed.discard(root)
            self.rebuild()
        return sub if self.model_.row_of(FOLDER, sub) >= 0 else root

    def _choose(self, kind: str, key: str, announce: bool) -> bool:
        number = self.model_.row_of(kind, key)
        if number < 0:
            return False
        changed = (kind, key) != self._chosen
        self._chosen = (kind, key)
        index = self.model_.index(number)
        if self.view.currentIndex() != index:
            blocked = self.view.selectionModel().blockSignals(True)
            self.view.setCurrentIndex(index)
            self.view.selectionModel().blockSignals(blocked)
            self.view.viewport().update()
        if changed and announce:
            self.selection_changed.emit(*self.current())
        return True

    def _restore(self) -> None:
        """After the rows changed: the same choice, quietly; or, when it is
        gone (a folder renamed or removed), the nearest folder still
        there, and the host is told."""
        if not self.library.roots():
            if self._chosen != ("", ""):
                self._chosen = ("", "")
                self.view.clearSelection()
            return
        kind, key = self._chosen
        if kind and self._choose(kind, key, announce=False):
            return
        if not kind:
            self._choose(FOLDER, self.library.roots()[0], announce=False)
            if not getattr(self, "_quiet_start", False):
                self.selection_changed.emit(*self.current())
            return
        target = None
        if kind == FOLDER:
            probe = key
            while probe and os.path.dirname(probe) != probe and target is None:
                probe = os.path.dirname(probe)
                if os.path.isdir(probe):
                    target = self._visible_folder_for(probe)
        target = target or self.library.roots()[0]
        self._choose(FOLDER, target, announce=True)

    def _on_clicked(self, index: QModelIndex) -> None:
        row = self.model_.row(index.row())
        if row is not None and row.selectable and (row.kind, row.key) == self._chosen:
            self.selection_changed.emit(*self.current())

    def _on_current(self, current: QModelIndex, _previous: QModelIndex) -> None:
        row = self.model_.row(current.row())
        if row is None or not row.selectable or (row.kind, row.key) == self._chosen:
            return
        self._chosen = (row.kind, row.key)
        self.selection_changed.emit(*self.current())

    # -- keys and clicks -----------------------------------------------------------

    def _current_row(self) -> Optional[Row]:
        return self.model_.row(self.view.currentIndex().row())

    def _key(self, event) -> bool:
        row = self._current_row()
        key = event.key()
        if row is None:
            return False
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activated.emit(*self.current())
            return True
        if row.kind == FOLDER and key == Qt.Key.Key_Right and row.expandable and not row.expanded:
            self.expand(row.key, True)
            return True
        if row.kind == FOLDER and key == Qt.Key.Key_Left:
            if row.expandable and row.expanded:
                self.expand(row.key, False)
                return True
            if row.level == 2:
                self._choose(FOLDER, os.path.dirname(row.key), announce=True)
                return True
        return False

    def _press(self, pos: QPoint) -> bool:
        """A click on a disclosure triangle opens or closes the folder."""
        index = self.view.indexAt(pos)
        row = self.model_.row(index.row())
        if row is None or not row.expandable:
            return False
        rect = self.view.visualRect(index)
        if pos.x() - rect.left() > 18:
            return False
        self.expand(row.key, not row.expanded)
        return True

    # -- the context menu ------------------------------------------------------

    def menu_for(self, row: Optional[Row]) -> QMenu:
        """The context menu for ``row`` (None or a header: the empty part
        of the sidebar)."""
        menu = QMenu(self)
        if row is None or row.kind != FOLDER:
            add = menu.addAction(_("Add Folder…"))
            add.setObjectName("add_folder")
            add.triggered.connect(self.add_folder_requested)
            return menu
        path = row.key

        def action(name: str, text: str, slot) -> None:
            act = menu.addAction(text)
            act.setObjectName(name)
            act.triggered.connect(slot)

        action("new_note", _("New Note"), lambda: self._new_note(path))
        action("new_folder", _("New Folder"), lambda: self._new_folder(path))
        action("add_files", _("Add Files…"),
               lambda: library_actions.add_files(self, self.library, path))
        menu.addSeparator()
        action("reveal", reveal_label(), lambda: library_actions.reveal(path))
        action("copy_path", _("Copy Path"), lambda: library_actions.copy_paths([path]))
        menu.addSeparator()
        if row.is_root:
            action("remove_root", _("Remove from Sidebar"), lambda: self.library.remove_root(path))
        else:
            action("rename", _("Rename…"), lambda: self.rename_folder(path))
            action("trash", _("Move to Trash"), lambda: self.trash_folder(path))
        return menu

    def _context_menu(self, pos: QPoint) -> None:
        row = self.model_.row(self.view.indexAt(pos).row())
        self.menu_for(row).exec(self.view.viewport().mapToGlobal(pos))

    def _new_note(self, folder: str) -> None:
        path = library_actions.new_note(self, self.library, folder)
        if path:
            self.note_created.emit(path)

    def _new_folder(self, folder: str) -> None:
        made = library_actions.new_folder(self, self.library, folder)
        if made:
            self.rebuild()
            self.select(FOLDER, made, announce=True)

    def rename_folder(self, path: str) -> Optional[str]:
        """Rename the folder ``path``; the sidebar shows it under its new
        name (still chosen, when it was) once the rename is made."""
        chosen = self._chosen == (FOLDER, path)

        def renamed(new_path: str) -> None:
            if chosen and self._chosen == (FOLDER, path):
                self._chosen = (FOLDER, os.path.abspath(new_path))
            self.rebuild()
            if chosen:
                self.selection_changed.emit(*self.current())

        return library_actions.rename(self, self.library, path, self.unsaved_notes,
                                      self.status_message.emit, done=renamed)

    def trash_folder(self, path: str) -> bool:
        done = library_actions.trash(self, self.library, [path])
        if done:
            self.rebuild()
        return done

    # -- drops -----------------------------------------------------------------

    def _set_drop_target(self, path: Optional[str]) -> None:
        if path != self._drop_target:
            self._drop_target = path
            self.view.viewport().update()

    def _folder_at(self, pos: QPoint) -> Optional[str]:
        row = self.model_.row(self.view.indexAt(pos).row())
        return row.key if row is not None and row.kind == FOLDER else None

    def _drop_refused(self, folder: Optional[str], mime) -> bool:
        """Whether a drop on ``folder`` is no drop at all: no folder there,
        nothing to drop, or a folder dropped into itself (or into one of
        its own folders), which is never made."""
        if folder is None or not library_actions.accepts(mime):
            return True
        inside, outside = library_actions.dropped(mime)
        return library_actions.goes_into_itself(inside + outside, folder)

    def _drag_move(self, event) -> None:
        folder = self._folder_at(event.position().toPoint())
        if self._drop_refused(folder, event.mimeData()):
            self._set_drop_target(None)
            event.ignore()
            return
        inside, _outside = library_actions.dropped(event.mimeData())
        copy = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        self._set_drop_target(folder)
        event.setDropAction(Qt.DropAction.MoveAction if inside and not copy
                            else Qt.DropAction.CopyAction)
        event.accept()

    def _drop(self, event) -> None:
        folder = self._folder_at(event.position().toPoint())
        self._set_drop_target(None)
        if self._drop_refused(folder, event.mimeData()):
            event.ignore()
            return
        inside, outside = library_actions.dropped(event.mimeData())
        copy = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        event.setDropAction(Qt.DropAction.MoveAction if inside and not copy
                            else Qt.DropAction.CopyAction)
        event.accept()
        self.drop_paths(folder, inside, outside, copy)
