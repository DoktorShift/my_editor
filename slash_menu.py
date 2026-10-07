# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The slash menu: structure inserted while typing.

Typing "/" at the start of a word, in a document that holds Markdown
structure, opens a short list under the caret: the paragraph styles,
the lists, the blocks and what can be inserted. The list is made of the
window's own commands (CommandRegistry.rank), so it offers what the
menus offer, by the same names and with the same keys, and only what can
act right now (Nostr items only while a Nostr account is in use). The
words typed after the slash narrow it: "/he" puts the headings first.

Up and Down move (and so does the pointer, as in every menu), Return or
Tab picks, Escape closes it and keeps the text. A space right after the
slash (a slash in a sentence: "either / or"), the caret leaving the
words, the slash being deleted, three letters that match nothing, or a
space after words that match nothing close it too; Return right after a
bare slash, with no item moved to, ends the paragraph as usual. Picking
takes "/words" away and runs the command there, as one step to undo.

The list never takes the keyboard focus: the text keeps it, and the
editor hands the menu the keys it needs (HtmlEditor). Screen readers
hear the menu open, each item chosen ("Heading 2, 3 of 9") and what was
done, through announcements (announce.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QFrame, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

import format_icons
from constants import (
    DARK_BORDER, DARK_FG, DARK_MENU_BG, DARK_MUTED_FG, DARK_SELECTION, LIGHT_BORDER, LIGHT_FG,
    LIGHT_MENU_BG, LIGHT_MUTED_FG, LIGHT_SELECTION,
)
from i18n import _, pgettext
from popup_placement import place_below

# The menu's groups, in order (Command.slash_section names one).
TEXT = "text"
LISTS = "lists"
BLOCKS = "blocks"
INSERT = "insert"
SECTIONS = (TEXT, LISTS, BLOCKS, INSERT)


def section_title(section: str) -> str:
    """A group's name, as the menu shows it."""
    return {
        TEXT: pgettext("slash menu section", "Text"),
        LISTS: pgettext("slash menu section", "Lists"),
        BLOCKS: pgettext("slash menu section", "Blocks"),
        INSERT: pgettext("slash menu section", "Insert"),
    }.get(section, "")


# The icon of each command the menu can show, drawn in the menu's ink:
# the toolbar's where it has one. A command not listed has none.
IconMaker = Callable[[QColor], QIcon]
ICONS: Dict[str, IconMaker] = {
    "format.style.body": lambda ink: format_icons.letter("¶", ink),
    "format.style.h1": lambda ink: format_icons.letter("H1", ink, bold=True),
    "format.style.h2": lambda ink: format_icons.letter("H2", ink, bold=True),
    "format.style.h3": lambda ink: format_icons.letter("H3", ink, bold=True),
    "format.list.bullet": format_icons.bulleted_list,
    "format.list.number": format_icons.numbered_list,
    "format.quote": format_icons.quote,
    "insert.divider": format_icons.divider,
    "insert.link": format_icons.link,
    "format.list.task": format_icons.checklist,
}


@dataclass(frozen=True)
class SlashItem:
    """One row of the menu: a command, under its group's name."""

    section: str
    action: QAction


_HEADER = Qt.ItemDataRole.UserRole + 1
_KEYS = Qt.ItemDataRole.UserRole + 2


class _Rows(QStyledItemDelegate):
    """Group names in small capitals; commands with their icon, name and
    keys (on the right, quieter), the chosen one in the selection color."""

    ROW = 28
    HEADER = 24

    def __init__(self, menu: "SlashMenu") -> None:
        super().__init__(menu)
        self._menu = menu

    def sizeHint(self, option, index) -> QSize:
        return QSize(SlashMenu.WIDTH - 12, self.HEADER if index.data(_HEADER) else self.ROW)

    def paint(self, painter: QPainter, option, index) -> None:
        colors = self._menu.colors()
        rect = option.rect
        painter.save()
        if index.data(_HEADER):
            font = QFont(option.font)
            font.setCapitalization(QFont.Capitalization.SmallCaps)
            font.setPointSizeF(max(8.0, font.pointSizeF() - 1))
            painter.setFont(font)
            painter.setPen(QColor(colors["muted"]))
            painter.drawText(rect.adjusted(10, 0, -10, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                             index.data(Qt.ItemDataRole.DisplayRole))
            painter.restore()
            return
        chosen = bool(option.state & QStyle.StateFlag.State_Selected)
        if chosen:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors["selection"]))
            painter.drawRoundedRect(rect.adjusted(2, 1, -2, -1), 5, 5)
        ink = QColor("#FFFFFF") if chosen else QColor(colors["text"])
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        size = format_icons.SIZE
        if isinstance(icon, QIcon) and not icon.isNull():
            icon_rect = QRect(rect.left() + 10, rect.center().y() - size // 2, size, size)
            icon.paint(painter, icon_rect)
        keys = index.data(_KEYS) or ""
        keys_width = option.fontMetrics.horizontalAdvance(keys) + 12 if keys else 0
        painter.setPen(ink)
        text_rect = rect.adjusted(10 + size + 10, 0, -10 - keys_width, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         option.fontMetrics.elidedText(
                             index.data(Qt.ItemDataRole.DisplayRole),
                             Qt.TextElideMode.ElideRight, text_rect.width()))
        if keys:
            painter.setPen(QColor("#FFFFFF") if chosen else QColor(colors["muted"]))
            painter.drawText(rect.adjusted(0, 0, -10, 0),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, keys)
        painter.restore()


class SlashMenu(QFrame):
    """The list the editor opens under the caret. ``picked(action)`` when
    a row is clicked; the editor asks for the chosen one on Return."""

    picked = Signal(object)

    WIDTH = 300
    VISIBLE_ROWS = 10

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setObjectName("SlashMenu")
        self.setAccessibleName(_("Insert"))
        self.setFixedWidth(self.WIDTH)
        self._dark = False
        self._actions: List[Optional[QAction]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.list = QListWidget(self)
        self.list.setObjectName("SlashMenuList")
        self.list.setAccessibleName(_("Insert"))
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setItemDelegate(_Rows(self))
        self.list.itemClicked.connect(self._clicked)
        # The pointer chooses the row it is over, as in every menu.
        self.list.setMouseTracking(True)
        self.list.itemEntered.connect(self._hovered)
        layout.addWidget(self.list)

    # -- what is shown ---------------------------------------------------------

    def colors(self) -> Dict[str, str]:
        if self._dark:
            return {"background": DARK_MENU_BG, "border": DARK_BORDER, "text": DARK_FG,
                    "muted": DARK_MUTED_FG, "selection": DARK_SELECTION}
        return {"background": LIGHT_MENU_BG, "border": LIGHT_BORDER, "text": LIGHT_FG,
                "muted": LIGHT_MUTED_FG, "selection": LIGHT_SELECTION}

    def show_items(self, items: List[SlashItem], *, headers: bool, dark: bool) -> None:
        """Fill the list with ``items`` (in order), under their group names
        when ``headers``, the first command chosen."""
        self._dark = dark
        colors = self.colors()
        self.setStyleSheet(
            f"#SlashMenu {{ background: {colors['background']}; "
            f"border: 1px solid {colors['border']}; border-radius: 8px; }}"
            f"#SlashMenuList {{ background: transparent; color: {colors['text']}; }}")
        ink = QColor(colors["text"])
        self.list.clear()
        self._actions = []
        section = None
        for item in items:
            if headers and item.section != section:
                section = item.section
                header = QListWidgetItem(section_title(section))
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                header.setData(_HEADER, True)
                self.list.addItem(header)
                self._actions.append(None)
            action = item.action
            make_icon = ICONS.get(action.objectName())
            row = QListWidgetItem(make_icon(ink) if make_icon else QIcon(),
                                  action.text().replace("&", ""))
            row.setData(_KEYS, action.shortcut().toString(
                QKeySequence.SequenceFormat.NativeText))
            row.setToolTip(action.toolTip() if action.toolTip() != action.text() else "")
            self.list.addItem(row)
            self._actions.append(action)
        self._choose(next((i for i, a in enumerate(self._actions) if a is not None), -1))
        rows = [self.list.sizeHintForRow(i) for i in range(self.list.count())]
        shown = sum(rows[:self.VISIBLE_ROWS])
        self.list.setFixedHeight(shown + 2)
        self.adjustSize()

    def has_choices(self) -> bool:
        return any(a is not None for a in self._actions)

    # -- choosing --------------------------------------------------------------

    def chosen(self) -> Optional[QAction]:
        row = self.list.currentRow()
        return self._actions[row] if 0 <= row < len(self._actions) else None

    def chosen_title(self) -> str:
        action = self.chosen()
        return action.text().replace("&", "") if action is not None else ""

    def choice_position(self) -> tuple:
        """Where the chosen item is among the items (from 1), and how many
        there are: (0, count) when none is chosen."""
        rows = [i for i, a in enumerate(self._actions) if a is not None]
        current = self.list.currentRow()
        return (rows.index(current) + 1 if current in rows else 0), len(rows)

    def move_choice(self, step: int) -> None:
        """Up (-1) or Down (+1), skipping group names, wrapping around."""
        rows = [i for i, a in enumerate(self._actions) if a is not None]
        if not rows:
            return
        current = self.list.currentRow()
        position = rows.index(current) if current in rows else -1
        self._choose(rows[(position + step) % len(rows)])

    def _choose(self, row: int) -> None:
        self.list.setCurrentRow(row)
        if row >= 0:
            self.list.scrollToItem(self.list.item(row))

    def _clicked(self, item: QListWidgetItem) -> None:
        action = self._actions[self.list.row(item)]
        if action is not None:
            self.picked.emit(action)

    def _hovered(self, item: QListWidgetItem) -> None:
        row = self.list.row(item)
        if 0 <= row < len(self._actions) and self._actions[row] is not None:
            self._choose(row)

    # -- where it opens ----------------------------------------------------------

    def open_at(self, caret: QRect, parent: QWidget) -> None:
        """Under the caret (in ``parent``'s coordinates), or above it when
        the screen has no room below."""
        place_below(self, caret, parent)
        self.show()
