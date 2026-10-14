# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Help > Keyboard Shortcuts: every key MyEditor answers to, to look up
and to learn.

A window of its own beside the document, as the Character Viewer or a
Help viewer is: not modal, one of it (the command brings it to the
front), closed with Escape or Command-W (Ctrl+W), and where it was left
the next time. Laid out after System Settings and Mail: a search field
on top, a sidebar of categories below it (a pop-up menu instead when the
window is narrow), and the category's keys on the right, in sections,
the ones used most first. Each row names what a key does and draws the
keys at the trailing end, as menus do, in the platform's own notation
(shortcut_catalog.shown). A command that does nothing right now is
dimmed, with the reason in a few words (Nostr's say it needs an account,
rather than hiding).

The search finds a row by its words, in any case and without accents,
and by its keys however they are typed ("cmd b", "⌘B", "ctrl+b").
Type a Shortcut takes the next key combination pressed and shows what it
does, or says that it does nothing. Down goes from the field to the
list; Return or a double-click there does the command, in the document
window, which comes to the front.

Native widgets only, in the app's palette and system font: the keys are
drawn as quiet key caps in the palette's own colors. The rows come from
shortcut_catalog.build_rows, which reads the command list and the
in-window tables, so the window cannot list a key that does not work.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import QEvent, QKeyCombination, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QKeySequence, QPainter, QPalette, QPen, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStackedLayout,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import shortcut_catalog as catalog
import theme
from i18n import _
from settings import load_settings, save_setting
from shortcut_catalog import Row

# The setting that keeps where the window was and how big.
GEOMETRY_SETTING = "shortcuts_window_geometry"
# Narrower than this, the sidebar gives way to a pop-up menu.
NARROW = 600
ALL = ""

KIND_ROLE = Qt.ItemDataRole.UserRole + 1
ROW_ROLE = Qt.ItemDataRole.UserRole + 2
CATEGORY, SECTION, NOTE, ROW = "category", "section", "note", "row"

_MODIFIER_KEYS = {Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta,
                  Qt.Key.Key_AltGr, Qt.Key.Key_CapsLock, Qt.Key.Key_unknown}


def _pressed_keys(event) -> List[str]:
    """The combination ``event`` pressed, as the catalog writes keys
    ("Ctrl+Shift+P"): Shift+Tab as itself, not Backtab, and, for a symbol
    Shift makes ("}" on Shift-]), with and without the Shift."""
    key = event.key()
    modifiers = event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                     | Qt.KeyboardModifier.ShiftModifier
                                     | Qt.KeyboardModifier.AltModifier
                                     | Qt.KeyboardModifier.MetaModifier)
    if key == Qt.Key.Key_Backtab:
        key = Qt.Key.Key_Tab
        modifiers |= Qt.KeyboardModifier.ShiftModifier
    combination = QKeySequence(QKeyCombination(modifiers, Qt.Key(key)))
    texts = [combination.toString(QKeySequence.SequenceFormat.PortableText)]
    symbol = not (Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9
                  or key >= Qt.Key.Key_Escape)
    if symbol and modifiers & Qt.KeyboardModifier.ShiftModifier:
        plain = QKeySequence(QKeyCombination(modifiers & ~Qt.KeyboardModifier.ShiftModifier,
                                             Qt.Key(key)))
        texts.append(plain.toString(QKeySequence.SequenceFormat.PortableText))
    return texts


class _RowDelegate(QStyledItemDelegate):
    """Draws the list: section headings, and rows with their name at the
    leading end and their keys as quiet key caps at the trailing end, the
    focus ring around the row with the keyboard focus."""

    def __init__(self, view: QListWidget) -> None:
        super().__init__(view)
        self._view = view

    # -- sizes -------------------------------------------------------------------

    def sizeHint(self, option, index) -> QSize:  # noqa: N802, Qt's name
        metrics = QFontMetrics(self._view.font())
        kind = index.data(KIND_ROLE)
        if kind == CATEGORY:
            return QSize(0, metrics.height() * 2 + 10)
        if kind == SECTION:
            return QSize(0, metrics.height() + 18)
        if kind == NOTE:
            lines = max(1, metrics.horizontalAdvance(index.data(Qt.ItemDataRole.DisplayRole))
                        // max(1, self._view.viewport().width() - 40) + 1)
            return QSize(0, metrics.lineSpacing() * lines + 14)
        # As tall as a menu item with room to breathe: easy to read and hit.
        return QSize(0, max(30, metrics.height() + 14))

    # -- painting ----------------------------------------------------------------

    def paint(self, painter: QPainter, option, index) -> None:
        kind = index.data(KIND_ROLE)
        palette = self._view.palette()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if kind == ROW:
            self._paint_row(painter, option, index, palette)
        else:
            self._paint_heading(painter, option, index, palette, kind)
        painter.restore()

    def _paint_heading(self, painter, option, index, palette, kind) -> None:
        font = QFont(self._view.font())
        text = index.data(Qt.ItemDataRole.DisplayRole)
        rect = option.rect.adjusted(12, 0, -12, -4)
        if kind == CATEGORY:
            font.setPointSizeF(font.pointSizeF() * 1.15)
            font.setWeight(QFont.Weight.Bold)
            color = palette.color(QPalette.ColorRole.Text)
            flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
        elif kind == SECTION:
            font.setWeight(QFont.Weight.DemiBold)
            color = palette.color(QPalette.ColorRole.PlaceholderText)
            flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
        else:
            color = palette.color(QPalette.ColorRole.PlaceholderText)
            flags = (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                     | Qt.TextFlag.TextWordWrap)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(rect, int(flags), text)

    def _paint_row(self, painter, option, index, palette) -> None:
        row: Row = index.data(ROW_ROLE)
        rect = option.rect.adjusted(6, 1, -6, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        focused = selected and self._view.hasFocus()
        dark = theme.is_dark_active()
        if selected:
            fill = (palette.color(QPalette.ColorRole.Highlight) if focused
                    else palette.color(QPalette.ColorRole.AlternateBase))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, 5, 5)
        if focused:
            # The ring stays visible on the highlight (a Mac's focus ring).
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QPalette().color(QPalette.ColorRole.Highlight) if False
                                else _ring(dark, on_accent=True), 2))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 5, 5)
        text = palette.color(QPalette.ColorRole.HighlightedText if focused
                             else QPalette.ColorRole.Text)
        muted = text if focused else palette.color(QPalette.ColorRole.PlaceholderText)
        if row.reason and not focused:
            text = muted
        font = QFont(self._view.font())
        keys_width = self._keys_width(row, font)
        inner = rect.adjusted(10, 0, -10, 0)
        name_rect = QRect(inner.left(), inner.top(), inner.width() - keys_width - 16,
                          inner.height())
        metrics = QFontMetrics(font)
        painter.setFont(font)
        painter.setPen(text)
        title = metrics.elidedText(row.title, Qt.TextElideMode.ElideRight, name_rect.width())
        painter.drawText(name_rect, int(Qt.AlignmentFlag.AlignLeft
                                        | Qt.AlignmentFlag.AlignVCenter), title)
        if row.reason:
            used = metrics.horizontalAdvance(title) + 12
            small = QFont(font)
            small.setPointSizeF(font.pointSizeF() * 0.9)
            painter.setFont(small)
            painter.setPen(muted)
            reason_rect = name_rect.adjusted(used, 0, 0, 0)
            reason = QFontMetrics(small).elidedText(row.reason, Qt.TextElideMode.ElideRight,
                                                    max(0, reason_rect.width()))
            painter.drawText(reason_rect, int(Qt.AlignmentFlag.AlignLeft
                                              | Qt.AlignmentFlag.AlignVCenter), reason)
        self._paint_keys(painter, row, QRect(inner.right() - keys_width, inner.top(),
                                              keys_width, inner.height()),
                         font, text, muted, palette, focused)

    # -- key caps ----------------------------------------------------------------

    def _pieces(self, row: Row):
        """What the keys part shows: caps for each combination, with a quiet
        "or" between two to choose from, or "to" across a run."""
        pieces = []
        for number, keys in enumerate(row.shown):
            if number:
                pieces.append(("word", _("to") if row.span else _("or")))
            pieces.append(("keys", keys))
        return pieces

    @staticmethod
    def _cap_font(font: QFont) -> QFont:
        cap = QFont(font)
        cap.setPointSizeF(font.pointSizeF() * 0.92)
        return cap

    def _keys_width(self, row: Row, font: QFont) -> int:
        cap = QFontMetrics(self._cap_font(font))
        plain = QFontMetrics(font)
        width = 0
        for kind, value in self._pieces(row):
            if kind == "word":
                width += plain.horizontalAdvance(value) + 12
                continue
            names = catalog.key_names(value)
            width += sum(max(cap.height() + 2, cap.horizontalAdvance(n) + 12) for n in names)
            width += 3 * (len(names) - 1)
            if not catalog.is_mac():
                width += (plain.horizontalAdvance("+") + 4) * (len(names) - 1)
        return width

    def _paint_keys(self, painter, row: Row, rect: QRect, font, text, muted, palette,
                    focused) -> None:
        cap_font = self._cap_font(font)
        cap = QFontMetrics(cap_font)
        plain = QFontMetrics(font)
        height = cap.height() + 6
        top = rect.top() + (rect.height() - height) // 2
        x = rect.left()
        border = text if focused else theme.check_box_border(palette)
        fill = Qt.BrushStyle.NoBrush if focused else palette.color(QPalette.ColorRole.Base)
        for kind, value in self._pieces(row):
            if kind == "word":
                painter.setFont(font)
                painter.setPen(muted)
                width = plain.horizontalAdvance(value) + 12
                painter.drawText(QRect(x, rect.top(), width, rect.height()),
                                 int(Qt.AlignmentFlag.AlignCenter), value)
                x += width
                continue
            for number, name in enumerate(catalog.key_names(value)):
                if number:
                    if not catalog.is_mac():
                        painter.setFont(font)
                        painter.setPen(muted)
                        plus = plain.horizontalAdvance("+") + 4
                        painter.drawText(QRect(x + 1, rect.top(), plus, rect.height()),
                                         int(Qt.AlignmentFlag.AlignCenter), "+")
                        x += plus
                    x += 3
                width = max(cap.height() + 2, cap.horizontalAdvance(name) + 12)
                box = QRect(x, top, width, height)
                painter.setPen(QPen(border, 1))
                painter.setBrush(fill)
                painter.drawRoundedRect(box.adjusted(0, 0, -1, -1), 4, 4)
                painter.setFont(cap_font)
                painter.setPen(text)
                painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), name)
                x += width


def _ring(dark: bool, *, on_accent: bool = False):
    from PySide6.QtGui import QColor
    return QColor(theme.focus_ring_on_accent_color(dark) if on_accent
                  else theme.focus_ring_color(dark))


class _RingDelegate(QStyledItemDelegate):
    """The sidebar's rows, as Qt draws them, with the focus ring around the
    current one while the sidebar has the keyboard focus."""

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        view = self.parent()
        if view.hasFocus() and index == view.currentIndex():
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(_ring(theme.is_dark_active()), 2))
            painter.drawRoundedRect(option.rect.adjusted(2, 2, -2, -2), 4, 4)
            painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802, Qt's name
        size = super().sizeHint(option, index)
        return QSize(size.width() + 16, max(size.height(), 28))


class ShortcutsWindow(QDialog):
    """Help > Keyboard Shortcuts (see the module docstring).

    ``rows`` gives the list as it is now (who can do what changes with the
    tab in front and with Nostr); ``perform`` does a command by its id in
    the document window and says whether it could; ``note`` gives the line
    over a category, if any.
    """

    def __init__(self, rows: Callable[[], List[Row]], *,
                 perform: Callable[[str], bool] = lambda _command: False,
                 note: Callable[[str], str] = lambda _category: "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("shortcuts_window")
        self.setWindowTitle(_("Keyboard Shortcuts"))
        self.setModal(False)
        self._source = rows
        self._perform = perform
        self._note = note
        self._rows: List[Row] = []
        self._captured: List[str] = []       # the keys Type a Shortcut took
        self._build()
        self.setMinimumSize(420, 340)
        self._restore_geometry()
        self.refresh()

    # -- building ----------------------------------------------------------------

    def _build(self) -> None:
        self._search = QLineEdit()
        self._search.setObjectName("shortcuts_search")
        self._search.setPlaceholderText(_("Search Shortcuts"))
        # A placeholder is not a name; a screen reader needs one of its own.
        self._search.setAccessibleName(_("Search Shortcuts"))
        self._search.setClearButtonEnabled(True)
        self._search.textEdited.connect(self._on_search_edited)
        self._search.installEventFilter(self)

        self._capture = QToolButton()
        self._capture.setText(_("Type a Shortcut"))
        self._capture.setAccessibleName(_("Type a Shortcut"))
        self._capture.setToolTip(_("Press a key combination to see what it does"))
        self._capture.setCheckable(True)
        self._capture.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._capture.toggled.connect(self._on_capture_toggled)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(self._search, 1)
        top.addWidget(self._capture)

        names = [(ALL, _("All Shortcuts"))] + [(c, catalog.category_title(c))
                                               for c in catalog.CATEGORIES]
        self._sidebar = QListWidget()
        self._sidebar.setObjectName("shortcuts_categories")
        self._sidebar.setAccessibleName(_("Categories"))
        self._sidebar.setItemDelegate(_RingDelegate(self._sidebar))
        self._popup = QComboBox()
        self._popup.setAccessibleName(_("Category"))
        for key, title in names:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self._sidebar.addItem(item)
            self._popup.addItem(title, key)
        width = max(self._sidebar.sizeHintForColumn(0), 140) + 24
        self._sidebar.setFixedWidth(width)
        self._sidebar.setCurrentRow(0)
        self._sidebar.currentRowChanged.connect(self._on_category)
        self._popup.currentIndexChanged.connect(self._on_category)

        self._list = QListWidget()
        self._list.setObjectName("shortcuts_list")
        self._list.setAccessibleName(_("Shortcuts"))
        self._list.setItemDelegate(_RowDelegate(self._list))
        self._list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.itemActivated.connect(lambda _item: self._perform_current())
        self._list.installEventFilter(self)

        self._empty = QLabel()
        self._empty.setObjectName("shortcuts_empty")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        holder = QWidget()
        self._stack = QStackedLayout(holder)
        self._stack.addWidget(self._list)
        self._stack.addWidget(self._empty)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self._sidebar)
        body.addWidget(holder, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        root.addLayout(top)
        root.addWidget(self._popup)
        root.addLayout(body, 1)

        # Visual order: the field, its toggle, the categories, the list.
        QWidget.setTabOrder(self._search, self._capture)
        QWidget.setTabOrder(self._capture, self._popup)
        QWidget.setTabOrder(self._popup, self._sidebar)
        QWidget.setTabOrder(self._sidebar, self._list)
        # Command-W (Ctrl+W, and Ctrl+F4 on Windows) closes it, as any window.
        close = QShortcut(QKeySequence.StandardKey.Close, self)
        close.activated.connect(self.close)
        self._fit_width()

    # -- the list ----------------------------------------------------------------

    def refresh(self) -> None:
        """Read the list again: what works changes with the tab in front
        and with Nostr coming into use or going out of it."""
        self._rows = list(self._source())
        self._fill()

    def _category(self) -> str:
        return self._sidebar.currentItem().data(Qt.ItemDataRole.UserRole)

    def _visible_rows(self) -> List[Row]:
        if self._captured:
            found = []
            for keys in self._captured:
                found += [r for r in catalog.rows_for_keys(self._rows, keys) if r not in found]
            return found
        query = self._search.text()
        if query.strip():
            return [row for row in self._rows if catalog.matches(row, query)]
        category = self._category()
        return [row for row in self._rows if category in (ALL, row.category)]

    def _fill(self) -> None:
        current = self._list.currentItem()
        was = current.data(ROW_ROLE) if current is not None else None
        self._list.clear()
        rows = self._visible_rows()
        by_category = not self._captured and not self._search.text().strip() \
            and self._category() != ALL
        grouped = {}
        for row in rows:
            grouped.setdefault(row.category, {}).setdefault(row.section, []).append(row)
        for category in catalog.CATEGORIES:
            sections = grouped.get(category)
            if not sections:
                continue
            if not by_category:
                self._heading(CATEGORY, catalog.category_title(category))
            note = self._note(category)
            if note:
                self._heading(NOTE, note)
            for section, section_rows in sections.items():
                self._heading(SECTION, section)
                for row in section_rows:
                    item = QListWidgetItem(row.title)
                    item.setData(KIND_ROLE, ROW)
                    item.setData(ROW_ROLE, row)
                    item.setData(Qt.ItemDataRole.AccessibleTextRole, catalog.spoken_row(row))
                    self._list.addItem(item)
                    if row == was:
                        self._list.setCurrentItem(item)
        self._show_empty(not rows)

    def _heading(self, kind: str, text: str) -> None:
        item = QListWidgetItem(text)
        item.setData(KIND_ROLE, kind)
        item.setData(Qt.ItemDataRole.AccessibleTextRole, text)
        # Not a stop for the arrow keys: they go from row to row.
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self._list.addItem(item)

    def _show_empty(self, empty: bool) -> None:
        if empty:
            if self._captured:
                keys = catalog.shown(self._captured[0])
                self._empty.setText(_("{keys} does nothing in MyEditor.").format(keys=keys))
            else:
                self._empty.setText(_("No shortcuts for “{query}”").format(
                    query=self._search.text().strip()))
        self._stack.setCurrentWidget(self._empty if empty else self._list)

    def rows_shown(self) -> List[Row]:
        """The rows listed now, in order."""
        return [self._list.item(i).data(ROW_ROLE) for i in range(self._list.count())
                if self._list.item(i).data(KIND_ROLE) == ROW]

    # -- choosing ----------------------------------------------------------------

    def _on_category(self, index: int) -> None:
        for widget in (self._sidebar, self._popup):
            widget.blockSignals(True)
        self._sidebar.setCurrentRow(index)
        self._popup.setCurrentIndex(index)
        for widget in (self._sidebar, self._popup):
            widget.blockSignals(False)
        if self._search.text() and not self._captured:
            # A category chosen is a new place to look: the search ends.
            self._search.clear()
        self._fill()

    def _on_search_edited(self, _text: str) -> None:
        self._captured = []
        self._fill()

    def _select_first_row(self) -> bool:
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item.data(KIND_ROLE) == ROW:
                self._list.setCurrentItem(item)
                self._list.setFocus(Qt.FocusReason.TabFocusReason)
                return True
        return False

    def _first_row_is_current(self) -> bool:
        current = self._list.currentRow()
        return all(self._list.item(i).data(KIND_ROLE) != ROW for i in range(current))

    def _perform_current(self) -> None:
        """Return or a double-click: the command of the row, done in the
        document window, which comes to the front; a beep when it cannot
        be done now or the row is a key a place handles itself."""
        item = self._list.currentItem()
        row = item.data(ROW_ROLE) if item is not None else None
        if row is None or not row.command or row.reason or not self._perform(row.command):
            QApplication.beep()

    # -- Type a Shortcut ---------------------------------------------------------

    def _on_capture_toggled(self, on: bool) -> None:
        self._search.setPlaceholderText(_("Press the keys of a shortcut") if on
                                        else _("Search Shortcuts"))
        if on:
            self._search.clear()
            self._captured = []
            self._search.setFocus(Qt.FocusReason.OtherFocusReason)
            self._fill()

    def _take_keys(self, event) -> None:
        self._captured = _pressed_keys(event)
        self._capture.setChecked(False)
        self._search.setText(catalog.shown(self._captured[0]))
        self._fill()

    # -- keys --------------------------------------------------------------------

    def eventFilter(self, watched, event) -> bool:  # noqa: N802, Qt's name
        if watched is self._search and self._capture.isChecked():
            if event.type() == QEvent.Type.ShortcutOverride:
                event.accept()              # the keys are what to look up, not commands
                return True
            if event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Escape and not event.modifiers():
                    self._capture.setChecked(False)
                elif event.key() not in _MODIFIER_KEYS:
                    self._take_keys(event)
                return True
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)
        key = event.key()
        if watched is self._search and key in (Qt.Key.Key_Down, Qt.Key.Key_Return,
                                               Qt.Key.Key_Enter):
            return self._select_first_row()
        if watched is self._list:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._perform_current()
                return True
            if key == Qt.Key.Key_Up and self._first_row_is_current():
                self._search.setFocus(Qt.FocusReason.BacktabFocusReason)
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:  # noqa: N802, Qt's name
        if event.key() == Qt.Key.Key_Escape and not event.modifiers():
            self.close()
            return
        super().keyPressEvent(event)

    # -- the window --------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802, Qt's name
        super().showEvent(event)
        self._search.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        QTimer.singleShot(0, self._update_narrow)

    def changeEvent(self, event) -> None:  # noqa: N802, Qt's name
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.refresh()

    def resizeEvent(self, event) -> None:  # noqa: N802, Qt's name
        super().resizeEvent(event)
        self._update_narrow()

    def _update_narrow(self) -> None:
        narrow = self.width() < NARROW
        self._sidebar.setVisible(not narrow)
        self._popup.setVisible(narrow)

    def _fit_width(self) -> None:
        """Wide enough by default for the longest row in any language."""
        self.resize(760, 560)

    def _restore_geometry(self) -> None:
        saved = load_settings().get(GEOMETRY_SETTING)
        if isinstance(saved, str):
            try:
                self.restoreGeometry(bytes.fromhex(saved))
            except ValueError:
                pass

    def hideEvent(self, event) -> None:  # noqa: N802, Qt's name
        save_setting(GEOMETRY_SETTING, bytes(self.saveGeometry()).hex())
        super().hideEvent(event)

    def apply_theme(self, is_dark: bool) -> None:
        """The window draws in the app's palette; a new one only repaints it."""
        self.update()
        self._list.viewport().update()
