# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Published view: what the account published, and taking it back.

  ┌──────────────────────────────────────────┐
  │  Why self-custody still matters          │  an article's title, or a
  │  Article · 7 October 2026                │  note's first line; what it is
  │  Hello from MyEditor                     │  and when it first went out
  │  Note · 6 October 2026                   │
  │             Load More Notes              │  while older notes may be there
  └──────────────────────────────────────────┘

The view owns its list, its states and its actions, and fits in any
container (the Drafts panel shows it as its second view). Its host gives
it an account and hears from it through two signals:

    set_account(profile)     whose items to show (None: nobody's)
    refresh()                read again (the panel's refresh button)
    apply_theme(is_dark)
    open_requested(object)   an article to edit in a tab (a PublishedItem):
                             bound to the article's identifier, so that
                             publishing it again replaces it and keeps its
                             first publication date
    status_message(str)      a sentence for the host's status line ("" to
                             clear it)

What is read and how an item is deleted is nostr/published.py
(PublishedList); the view shows that and passes on what the person asks.

The actions are on the context menu (also from the Menu key): Edit (an
article), Open in Browser, Copy Link, Delete from Nostr…. Return opens a
row (an article for editing, a note in the browser), Delete or Backspace
deletes it, the arrow keys move. Deleting asks first, with Cancel as the
default button, as everything that cannot be undone does; a deletion no
relay took is said in an alert that offers Try Again.

States: no account, the first read under way, the list, nothing
published yet, and could not be read (with Try Again).
"""

from __future__ import annotations

import sys
from typing import List, Optional

from PySide6.QtCore import (
    QAbstractListModel, QEvent, QModelIndex, QPoint, QRect, QSize, Qt, QUrl, Signal,
)
from PySide6.QtGui import (
    QColor, QContextMenuEvent, QDesktopServices, QFontMetrics, QGuiApplication, QKeySequence,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QLabel, QListView, QMenu, QProgressBar,
    QPushButton, QStackedLayout, QStyle, QStyledItemDelegate, QStyleOptionFocusRect,
    QVBoxLayout, QWidget,
)

import alerts
import theme
from i18n import _, ngettext

from ..bunker import humanize_failure, is_signer_silent
from ..published import (
    DELETING, LOADING, READY, SIGNED_OUT, UNREACHABLE, PublishedItem, PublishedList,
)
from .drafts_common import (
    LIST_GUTTER, MIN_CONTROL_PX, ROW_LINE_GAP, ROW_PAD_V, SELECTION_BAR_W, THEME_TOKENS,
    format_absolute_date, format_absolute_time, format_short_date, secondary_font,
    title_font,
)

ITEM_ROLE = Qt.ItemDataRole.UserRole + 1     # the PublishedItem, None for Load More
META_ROLE = Qt.ItemDataRole.UserRole + 2     # line 2 of a row
SHORT_META_ROLE = Qt.ItemDataRole.UserRole + 3   # line 2 with the date in digits
STATE_ROLE = Qt.ItemDataRole.UserRole + 4    # the item's deletion state
PROBLEM_ROLE = Qt.ItemDataRole.UserRole + 5  # Load More: what went wrong, or ""

# Where a long title is cut in an alert's title.
ALERT_TITLE_CHARS = 60


# --------------------------------------------------------------------------- #
# Words                                                                       #
# --------------------------------------------------------------------------- #

def kind_word(item: PublishedItem) -> str:
    return _("Article") if item.is_article else _("Note")


def display_title(item: PublishedItem) -> str:
    return item.title or _("Untitled")


def short_title(item: PublishedItem, limit: int = ALERT_TITLE_CHARS) -> str:
    title = display_title(item)
    return title if len(title) <= limit else title[:limit - 1].rstrip() + "…"


def meta_text(item: PublishedItem, deletion: str = "", *, short: bool = False) -> str:
    """Line 2 of a row: what it is and when it first went out; ``short``
    writes the date in digits, for a line too narrow for the month's name."""
    if deletion == DELETING:
        return _("Deleting…")
    when = (format_short_date if short else format_absolute_date)(item.first_published)
    return _("{kind} · {date}").format(kind=kind_word(item), date=when)


def accessible_text(item: PublishedItem, deletion: str = "") -> str:
    """What a screen reader says for a row, in reading order."""
    sentences = [display_title(item), kind_word(item),
                 _("First published {time}").format(
                     time=format_absolute_time(item.first_published))]
    if deletion == DELETING:
        sentences.append(_("Deleting"))
    return ". ".join(s.rstrip(". ") for s in sentences) + "."


def tooltip_text(item: PublishedItem) -> str:
    """The whole title, and the exact time it first went out."""
    return "\n".join((display_title(item), _("{kind}, first published {time}").format(
        kind=kind_word(item), time=format_absolute_time(item.first_published))))


def more_label(model: PublishedList) -> str:
    """The Load More row's words, which stay its action after a failure:
    pressing it again is trying again."""
    return _("Loading…") if model.loading_more else _("Load More Notes")


def more_problem(model: PublishedList) -> str:
    """Under the action, after a Load More that reached no relay. Short,
    so it fits the narrowest panel; the status line says the rest."""
    return _("Couldn’t load.") if model.more_failed and not model.loading_more else ""


def more_accessible(model: PublishedList) -> str:
    if model.loading_more:
        return _("Loading more notes")
    if model.more_failed:
        return _("Load More Notes. Couldn’t load more notes. Press Return to try again.")
    return _("Load More Notes")


# --------------------------------------------------------------------------- #
# The rows                                                                    #
# --------------------------------------------------------------------------- #

class _Rows(QAbstractListModel):
    """The list's rows: the items, then Load More while older notes may
    be there. ``reload`` keeps the selection and the scroll position."""

    _MORE = "\x00more"

    def __init__(self, model: PublishedList, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self._items: List[PublishedItem] = []
        self._more = False

    def keys(self) -> List[str]:
        return [item.id for item in self._items] + ([self._MORE] if self._more else [])

    def reload(self) -> None:
        items = self._model.items() if self._model.state == READY else []
        more = self._model.state == READY and bool(items) and (
            self._model.has_more or self._model.loading_more or self._model.more_failed)
        old_keys = self.keys()
        new_keys = [item.id for item in items] + ([self._MORE] if more else [])
        if new_keys == old_keys:
            self._items = items
            if new_keys:
                self.dataChanged.emit(self.index(0), self.index(len(new_keys) - 1))
            return
        self.layoutAboutToBeChanged.emit()
        persistent = self.persistentIndexList()
        moved = [old_keys[index.row()] if 0 <= index.row() < len(old_keys) else None
                 for index in persistent]
        self._items, self._more = items, more
        rows = {key: row for row, key in enumerate(new_keys)}
        for index, key in zip(persistent, moved):
            row = rows.get(key, -1)
            self.changePersistentIndex(index, self.index(row) if row >= 0 else QModelIndex())
        self.layoutChanged.emit()

    def item_at(self, row: int) -> Optional[PublishedItem]:
        return self._items[row] if 0 <= row < len(self._items) else None

    def is_more(self, row: int) -> bool:
        return self._more and row == len(self._items)

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items) + (1 if self._more else 0)

    def flags(self, index):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self.item_at(index.row())
        if item is None:
            if not self.is_more(index.row()):
                return None
            if role == Qt.ItemDataRole.DisplayRole:
                return more_label(self._model)
            if role == Qt.ItemDataRole.AccessibleTextRole:
                return more_accessible(self._model)
            if role == PROBLEM_ROLE:
                return more_problem(self._model)
            return None
        state = self._model.deletion_state(item.id)
        if role == Qt.ItemDataRole.DisplayRole:
            return display_title(item)
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return accessible_text(item, state)
        if role == Qt.ItemDataRole.ToolTipRole:
            return tooltip_text(item)
        if role == ITEM_ROLE:
            return item
        if role == META_ROLE:
            return meta_text(item, state)
        if role == SHORT_META_ROLE:
            return meta_text(item, state, short=True)
        if role == STATE_ROLE:
            return state
        return None


def row_height() -> int:
    """Two lines and their padding, from the fonts in use, so the rows
    grow with the text size (the same box as the drafts list)."""
    return (ROW_PAD_V + QFontMetrics(title_font()).height() + ROW_LINE_GAP
            + QFontMetrics(secondary_font()).height() + ROW_PAD_V)


class _RowDelegate(QStyledItemDelegate):
    """Paints a row the way the drafts list does: the title over a muted
    second line, a hover fill, and on the selected row a fill and a
    leading bar. Load More is centred, in the link colour."""

    def __init__(self, view: "PublishedView") -> None:
        super().__init__(view)
        self._view = view

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, row_height())

    def paint(self, painter, option, index) -> None:
        tokens = THEME_TOKENS[self._view.is_dark]
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        if selected:
            painter.fillRect(rect, QColor(tokens["selected_bg"]))
            bar = QRect(rect.left(), rect.top(), SELECTION_BAR_W, rect.height())
            painter.fillRect(QStyle.visualRect(option.direction, rect, bar),
                             QColor(tokens["selection_bar"]))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, QColor(tokens["hover_bg"]))
        inner = rect.adjusted(LIST_GUTTER, ROW_PAD_V, -LIST_GUTTER, -ROW_PAD_V)
        leading = QStyle.visualAlignment(option.direction, Qt.AlignmentFlag.AlignLeft)
        item = index.data(ITEM_ROLE)
        if item is None:
            self._paint_more(painter, index, inner, tokens, selected)
        else:
            deleting = index.data(STATE_ROLE) == DELETING
            title_colour = (tokens["selected_fg"] if selected
                            else tokens["muted"] if deleting else tokens["row_fg"])
            meta_colour = tokens["selected_muted"] if selected else tokens["muted"]
            heading = title_font()
            heading_height = QFontMetrics(heading).height()
            line_1 = QRect(inner.left(), inner.top(), inner.width(), heading_height)
            painter.setFont(heading)
            painter.setPen(QColor(title_colour))
            painter.drawText(line_1, int(leading | Qt.AlignmentFlag.AlignVCenter),
                             painter.fontMetrics().elidedText(
                                 index.data(Qt.ItemDataRole.DisplayRole) or "",
                                 Qt.TextElideMode.ElideRight, line_1.width()))
            secondary = secondary_font()
            line_2 = QRect(inner.left(), line_1.bottom() + 1 + ROW_LINE_GAP, inner.width(),
                           QFontMetrics(secondary).height())
            painter.setFont(secondary)
            painter.setPen(QColor(meta_colour))
            meta = index.data(META_ROLE) or ""
            if painter.fontMetrics().horizontalAdvance(meta) > line_2.width():
                # Large text on a narrow panel: the date in digits keeps
                # the whole date in view where the month's name would not.
                meta = index.data(SHORT_META_ROLE) or meta
            painter.drawText(line_2, int(leading | Qt.AlignmentFlag.AlignVCenter),
                             painter.fontMetrics().elidedText(
                                 meta, Qt.TextElideMode.ElideRight, line_2.width()))
        if option.state & QStyle.StateFlag.State_HasFocus and not selected:
            # The keyboard's place when nothing is selected yet.
            focus = QStyleOptionFocusRect()
            focus.rect = rect.adjusted(1, 1, -1, -1)
            focus.palette = option.palette
            focus.state = option.state
            self._view.style().drawPrimitive(QStyle.PrimitiveElement.PE_FrameFocusRect,
                                             focus, painter, self._view)
        painter.restore()


    def _paint_more(self, painter, index, inner: QRect, tokens: dict, selected: bool) -> None:
        """Load More: its action centred, in the link colour; after a
        failure, the action on line 1 and what went wrong under it."""
        loading = self._view.model.loading_more
        action_colour = (tokens["selected_fg"] if selected else tokens["muted"] if loading
                         else theme.dialog_link_color(self._view.is_dark))
        label = index.data(Qt.ItemDataRole.DisplayRole) or ""
        problem = index.data(PROBLEM_ROLE) or ""
        painter.setFont(QApplication.font())
        painter.setPen(QColor(action_colour))
        centre = int(Qt.AlignmentFlag.AlignCenter)
        if not problem:
            painter.drawText(inner, centre, painter.fontMetrics().elidedText(
                label, Qt.TextElideMode.ElideRight, inner.width()))
            return
        line_1 = QRect(inner.left(), inner.top(), inner.width(), painter.fontMetrics().height())
        painter.drawText(line_1, centre, painter.fontMetrics().elidedText(
            label, Qt.TextElideMode.ElideRight, inner.width()))
        secondary = secondary_font()
        line_2 = QRect(inner.left(), line_1.bottom() + 1 + ROW_LINE_GAP, inner.width(),
                       QFontMetrics(secondary).height())
        painter.setFont(secondary)
        painter.setPen(QColor(tokens["selected_muted"] if selected else tokens["error_fg"]))
        painter.drawText(line_2, centre, painter.fontMetrics().elidedText(
            problem, Qt.TextElideMode.ElideRight, inner.width()))


class _List(QListView):
    """The list, with a context menu for the row it is about: the one
    under the pointer, or from the keyboard (the Menu key) the current
    one, not whatever row the middle of the list happens to show."""

    menu_requested = Signal(QModelIndex, QPoint)     # the row, where (global)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        if event.reason() == QContextMenuEvent.Reason.Keyboard:
            index = self.currentIndex()
            where = self.visualRect(index).center() if index.isValid() else QPoint()
            point = self.viewport().mapToGlobal(where)
        else:
            index = self.indexAt(event.pos())
            point = event.globalPos()
            if index.isValid():
                self.setCurrentIndex(index)
        event.accept()
        if index.isValid():
            self.menu_requested.emit(index, point)


# --------------------------------------------------------------------------- #
# The view                                                                    #
# --------------------------------------------------------------------------- #

def _view_css(is_dark: bool) -> str:
    """Colour and weight from the drafts surfaces' tokens; sizes are set in
    Python, so they follow the application font."""
    t = THEME_TOKENS[bool(is_dark)]
    return f"""
QWidget#published_view {{ background: {t["panel_bg"]}; }}
QListView#published_list {{ background: {t["panel_bg"]}; border: none; }}
QLabel#published_empty_title {{ color: {t["chrome_fg"]}; font-weight: 600; }}
QLabel#published_empty_body {{ color: {t["muted"]}; }}
QPushButton#published_empty_action {{
    background: transparent;
    color: {t["row_fg"]};
    border: 1px solid {t["border"]};
    border-radius: 4px;
    padding: 4px 12px;
}}
QPushButton#published_empty_action:hover {{ background: {t["hover_bg"]}; }}
QPushButton#published_empty_action:focus {{ border-color: {t["accent"]}; }}
QProgressBar#published_busy {{
    background: {t["chrome_bg"]};
    border: 1px solid {t["border"]};
    border-radius: 3px;
    max-height: 6px;
}}
QProgressBar#published_busy::chunk {{ background: {t["accent"]}; border-radius: 2px; }}
"""


class PublishedView(QWidget):
    """The account's published notes and articles (see the module's text).

    Signals:
      open_requested(object)   an article (PublishedItem) to edit in a tab
      status_message(str)      a sentence for the host's status line
    """

    open_requested = Signal(object)
    status_message = Signal(str)

    def __init__(self, model: PublishedList, *, is_dark: bool = True,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("published_view")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._model = model
        self._is_dark = bool(is_dark)
        self._rows = _Rows(model, self)
        self._last = (model.state, model.refreshing, model.more_failed)
        self._build()
        # Bound methods, not lambdas: they go with the view if it goes first.
        model.changed.connect(self._on_changed)
        model.deletion_status.connect(self._on_deletion_status)
        model.deletion_done.connect(self._on_deleted)
        model.deletion_not_taken.connect(self._on_not_taken)
        model.deletion_refused.connect(self._on_refused)
        self.apply_theme(self._is_dark)
        self._on_changed()

    # -- the host's interface -------------------------------------------------

    @property
    def is_dark(self) -> bool:
        return self._is_dark

    @property
    def model(self) -> PublishedList:
        return self._model

    def set_account(self, profile) -> None:
        self._model.set_account(profile)

    def refresh(self) -> None:
        self._model.refresh()

    def apply_theme(self, is_dark: bool) -> None:
        self._is_dark = bool(is_dark)
        self.setStyleSheet(_view_css(self._is_dark))
        self._list.viewport().update()

    # -- building ---------------------------------------------------------------

    def _build(self) -> None:
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)

        self._list = _List()
        self._list.setObjectName("published_list")
        self._list.setAccessibleName(_("Published"))
        self._list.setModel(self._rows)
        self._list.setItemDelegate(_RowDelegate(self))
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setUniformItemSizes(True)
        self._list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setMouseTracking(True)
        self._list.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._list.activated.connect(self._on_activated)
        self._list.clicked.connect(self._on_clicked)
        self._list.menu_requested.connect(self._show_menu)
        # Return and Delete belong to the list, see ``eventFilter``.
        self._list.installEventFilter(self)
        self._stack.addWidget(self._list)

        self._placeholder = QWidget()
        column = QVBoxLayout(self._placeholder)
        column.setContentsMargins(LIST_GUTTER, 24, LIST_GUTTER, 24)
        column.setSpacing(8)
        column.addStretch(1)
        self._busy = QProgressBar()
        self._busy.setObjectName("published_busy")
        self._busy.setRange(0, 0)               # the wait has no known length
        self._busy.setTextVisible(False)
        self._busy.setMaximumWidth(160)
        self._busy.setAccessibleName(_("Loading"))
        column.addWidget(self._busy, 0, Qt.AlignmentFlag.AlignHCenter)
        self._empty_title = QLabel()
        self._empty_title.setObjectName("published_empty_title")
        self._empty_body = QLabel()
        self._empty_body.setObjectName("published_empty_body")
        self._empty_body.setFont(secondary_font())
        for label in (self._empty_title, self._empty_body):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            column.addWidget(label)
        self._empty_action = QPushButton(_("Try Again"))
        self._empty_action.setObjectName("published_empty_action")
        self._empty_action.setMinimumHeight(MIN_CONTROL_PX)
        self._empty_action.clicked.connect(self._model.refresh)
        column.addWidget(self._empty_action, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addStretch(2)
        self._stack.addWidget(self._placeholder)

    # -- what is shown ----------------------------------------------------------

    def _on_changed(self) -> None:
        current = self._list.currentIndex()
        row = current.row() if current.isValid() else -1
        self._rows.reload()
        if row >= 0 and not self._list.currentIndex().isValid() and self._rows.rowCount():
            # The row went (deleted): the keyboard stays where it was.
            nearest = self._rows.index(min(row, self._rows.rowCount() - 1))
            self._list.selectionModel().setCurrentIndex(
                nearest, self._list.selectionModel().SelectionFlag.ClearAndSelect)
        self._show_state()
        self._say_what_changed()

    def _show_state(self) -> None:
        state = self._model.state
        if state == SIGNED_OUT:
            self._show_placeholder(_("No account in use"),
                                   _("Choose an account in the Nostr menu to see what you "
                                     "published."))
        elif state == LOADING:
            self._show_placeholder("", _("Looking for your notes and articles…"), busy=True)
        elif state == UNREACHABLE:
            self._show_placeholder(_("Couldn’t load what you published"),
                                   _("Check your internet connection, then try again."),
                                   action=True)
        elif not self._model.items():
            self._show_placeholder(_("Nothing published yet"),
                                   _("Notes and articles you publish appear here."))
        else:
            self._stack.setCurrentWidget(self._list)
            if not self._list.currentIndex().isValid():
                # The arrow keys need somewhere to start; nothing is
                # selected until the person moves.
                self._list.selectionModel().setCurrentIndex(
                    self._rows.index(0), self._list.selectionModel().SelectionFlag.NoUpdate)

    def _show_placeholder(self, title: str, body: str, *, busy: bool = False,
                          action: bool = False) -> None:
        self._empty_title.setText(title)
        self._empty_title.setVisible(bool(title))
        self._empty_body.setText(body)
        self._busy.setVisible(busy)
        self._empty_action.setVisible(action)
        self._stack.setCurrentWidget(self._placeholder)

    def _say_what_changed(self) -> None:
        """A sentence for the host when a read began, or ended short of
        everything; "" when a refresh ended well, to clear the last one."""
        model = self._model
        was_state, was_refreshing, more_had_failed = self._last
        self._last = (model.state, model.refreshing, model.more_failed)
        if model.more_failed and not more_had_failed:
            self.status_message.emit(_("Couldn’t load more notes. Check your internet "
                                       "connection, then try again."))
            return
        if model.refreshing and not was_refreshing:
            self.status_message.emit(_("Refreshing what you published…"))
            return
        ended = ((was_refreshing and not model.refreshing)
                 or (was_state == LOADING and model.state == READY))
        if not ended:
            return
        if model.refresh_failed:
            self.status_message.emit(_("Couldn’t refresh. Check your internet connection, "
                                       "then try again."))
        elif model.partial:
            self.status_message.emit(_("Some relays didn’t answer, so something may be "
                                       "missing."))
        elif was_refreshing:
            self.status_message.emit("")

    # -- the actions ------------------------------------------------------------

    def current_item(self) -> Optional[PublishedItem]:
        return self._rows.item_at(self._list.currentIndex().row())

    def _open(self, item: PublishedItem) -> None:
        """What Return and a double click do: an article opens for
        editing, a note in the browser."""
        if item.is_article and item.identifier:
            self.open_requested.emit(item)
        else:
            self._open_in_browser(item)

    def _open_in_browser(self, item: PublishedItem) -> None:
        link = item.web_link()
        if link is None:
            self.status_message.emit(_("That link cannot be opened."))
            return
        QDesktopServices.openUrl(QUrl(link))

    def _copy_link(self, item: PublishedItem) -> None:
        link = item.web_link()
        if link is None:
            self.status_message.emit(_("That link cannot be opened."))
            return
        QGuiApplication.clipboard().setText(link)
        self.status_message.emit(_("Link copied."))

    def _ask_to_delete(self, item: PublishedItem) -> None:
        if self._model.deletion_state(item.id) == DELETING:
            return
        if alerts.confirm_destructive(
                self, title=_("Delete “{title}” from Nostr?").format(title=short_title(item)),
                message=_("Relays that keep it are asked to remove it. People and apps that "
                          "already saved a copy may still have it. Your draft stays."),
                action=_("Delete"), caution=True, is_dark=self._is_dark):
            self._model.delete(item.id)

    def _load_more(self) -> None:
        if not self._model.loading_more:
            self._model.load_more()

    def _on_activated(self, index: QModelIndex) -> None:
        item = self._rows.item_at(index.row())
        if item is not None:
            self._open(item)

    def _on_clicked(self, index: QModelIndex) -> None:
        # Load More is a button: one click.
        if self._rows.is_more(index.row()):
            self._load_more()

    def eventFilter(self, watched, event) -> bool:
        if watched is self._list and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            row = self._list.currentIndex().row()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._rows.is_more(row):
                    self._load_more()
                elif self.current_item() is not None:
                    self._open(self.current_item())
                return True
            if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                if self.current_item() is not None:
                    self._ask_to_delete(self.current_item())
                return True
        return super().eventFilter(watched, event)

    def menu_for(self, index: QModelIndex) -> Optional[QMenu]:
        """The row's menu, built but not shown (so tests can read it)."""
        item = self._rows.item_at(index.row()) if index.isValid() else None
        if item is None:
            return None
        menu = QMenu(self)
        default = None
        if item.is_article and item.identifier:
            default = menu.addAction(_("Edit"))
            default.triggered.connect(lambda: self.open_requested.emit(item))
        has_link = item.web_link() is not None
        browse = menu.addAction(_("Open in Browser"))
        browse.setEnabled(has_link)
        browse.triggered.connect(lambda: self._open_in_browser(item))
        copy = menu.addAction(_("Copy Link"))
        copy.setEnabled(has_link)
        copy.triggered.connect(lambda: self._copy_link(item))
        menu.setDefaultAction(default or browse)
        menu.addSeparator()
        delete = menu.addAction(_("Delete from Nostr…"))
        # The key that does it here, in the platform's own notation.
        delete.setShortcut(QKeySequence(Qt.Key.Key_Backspace if sys.platform == "darwin"
                                        else Qt.Key.Key_Delete))
        delete.setEnabled(self._model.deletion_state(item.id) != DELETING)
        delete.triggered.connect(lambda: self._ask_to_delete(item))
        return menu

    def _show_menu(self, index: QModelIndex, point: QPoint) -> None:
        menu = self.menu_for(index)
        if menu is not None:
            menu.exec(point)
            menu.deleteLater()

    # -- deletions that came back -----------------------------------------------

    def _on_deletion_status(self, _item: PublishedItem, text: str) -> None:
        self.status_message.emit(text)

    def _on_deleted(self, item: PublishedItem, results: list) -> None:
        accepted = sum(1 for _url, ok, _message in results if ok)
        self.status_message.emit(ngettext(
            "{accepted} of {total} relay accepted the request to delete “{title}”.",
            "{accepted} of {total} relays accepted the request to delete “{title}”.",
            len(results)).format(accepted=accepted, total=len(results),
                                 title=short_title(item)))

    def _on_not_taken(self, item: PublishedItem, results: list) -> None:
        details = "\n".join(f"{url}: {message or _('no answer')}"
                            for url, _ok, message in results)
        self._offer_try_again(item, _("No relay accepted the request. Check your internet "
                                      "connection, then try again."), details)

    def _on_refused(self, item: PublishedItem, reason: str) -> None:
        if is_signer_silent(reason):
            self._offer_try_again(item, humanize_failure(reason), "")
        else:
            self._offer_try_again(item, _("Your signer didn’t approve the request. Try "
                                          "again, and approve it on your signer."), reason)

    def _offer_try_again(self, item: PublishedItem, message: str, details: str) -> None:
        again = alerts.ask(
            self, title=_("Couldn’t delete “{title}”").format(title=short_title(item)),
            message=message, details=details, is_dark=self._is_dark,
            buttons=(alerts.Button(_("Cancel"), False, alerts.CANCEL),
                     alerts.Button(_("Try Again"), True, alerts.DEFAULT)))
        if again is True:
            self._model.try_again(item.id)
        else:
            self._model.dismiss(item.id)
