"""Reusable bot avatar widget: letter on color + label + dropdown.

The chip is the single most-used Telegram widget in the editor: it
appears in the publish dialog header (as a switcher) and is the
visual anchor everywhere a bot is referenced.

The menu is rebuilt **on click**, not on every ``set_active`` call.
Rebuilding during an in-flight action would destroy the menu mid-
trigger; deferring rebuild to the next click keeps the menu safe.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import QLabel, QMenu, QToolButton, QWidget


AVATAR_SIZE: int = 22


def make_bot_avatar(letter: str, color_hex: str, size: int = AVATAR_SIZE) -> QPixmap:
    """Render a flat colored circle with one centered letter."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.fillPath(path, QColor(color_hex))

        painter.setPen(Qt.white)
        font = QFont(painter.font())
        font.setBold(True)
        font.setPointSizeF(max(8.0, size * 0.5))
        painter.setFont(font)
        ch = (letter or "?")[:1].upper()
        rect = pm.rect()
        painter.drawText(rect, Qt.AlignCenter, ch)
    finally:
        painter.end()
    return pm


class BotAvatarLabel(QLabel):
    """Static avatar (no interactivity); used inside cards and lists."""

    def __init__(self, letter: str, color_hex: str, *, size: int = AVATAR_SIZE,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.set_bot(letter, color_hex)

    def set_bot(self, letter: str, color_hex: str) -> None:
        self.setPixmap(make_bot_avatar(letter, color_hex, self._size))


# Callback type aliases for readability.
BotEntry = Tuple[str, str, str, str]   # (bot_id, display_name, color, subtitle)
PickFn = Callable[[str], None]
AddFn = Callable[[], None]
GetEntriesFn = Callable[[], List[BotEntry]]


class BotChip(QToolButton):
    """Interactive chip: avatar + label, opens a dropdown menu on click.

    The owner provides a ``get_entries`` callable invoked at click
    time, so the menu is always fresh. ``on_pick(bot_id)`` fires when
    the user picks a bot; ``on_add()`` fires for the "Add a bot…"
    footer.
    """

    def __init__(
        self,
        *,
        on_pick: PickFn,
        on_add: AddFn,
        get_entries: GetEntriesFn,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._on_pick = on_pick
        self._on_add = on_add
        self._get_entries = get_entries
        self._current_bot_id: str = ""

        self.setPopupMode(QToolButton.MenuButtonPopup)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(AVATAR_SIZE, AVATAR_SIZE))
        self.setCursor(Qt.PointingHandCursor)
        self.setAutoRaise(False)
        # Menu is built lazily so the entry list is always current; the
        # ``aboutToShow`` signal fires every time the menu opens.
        self._menu = QMenu(self)
        self._menu.aboutToShow.connect(self._rebuild)
        self.setMenu(self._menu)
        # Default action (clicking the chip text) opens the menu, same
        # as clicking the menu arrow.
        self.clicked.connect(self.showMenu)

    def set_active(self, *, bot_id: str, display_name: str, color_hex: str,
                   subtitle: str = "") -> None:
        """Update the chip's display without touching the menu."""
        self._current_bot_id = bot_id
        self.setIcon(QIcon(make_bot_avatar(display_name, color_hex)))
        # Pad with a small leading space so the icon doesn't kiss the text.
        # The MenuButtonPopup style renders the dropdown arrow itself.
        self.setText(" " + display_name)
        self.setToolTip(subtitle or display_name)

    # -- internals ---------------------------------------------------- #

    def _rebuild(self) -> None:
        """Repopulate the menu just before it appears."""
        self._menu.clear()
        entries = list(self._get_entries())
        for bot_id, name, color, subtitle in entries:
            action = QAction(QIcon(make_bot_avatar(name, color)), name, self._menu)
            action.setCheckable(True)
            action.setChecked(bot_id == self._current_bot_id)
            if subtitle:
                action.setStatusTip(subtitle)
            # ``triggered`` fires after the menu has closed - safe to
            # rebuild on the next ``aboutToShow``.
            action.triggered.connect(
                lambda _checked=False, bid=bot_id: self._on_pick(bid)
            )
            self._menu.addAction(action)
        if entries:
            self._menu.addSeparator()
        add_action = QAction("Add a bot…", self._menu)
        add_action.triggered.connect(lambda _checked=False: self._on_add())
        self._menu.addAction(add_action)
