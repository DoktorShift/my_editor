"""Multi-select chat picker for the publish dialog.

A scrollable list of cards. Each card is a chat (or, for forum
supergroups, a parent + nested topics). Each card has a clear
checkbox, a chat-type pill (group / channel / DM), and the most
useful timestamp ("sent 2h ago" or "seen 3d ago").

A small toolbar above the list owns search and an "Add chat
manually" button (used for DMs and pre-emptively-added channels).

The empty state is a teaching block, not a sad "no items" line.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Set, Tuple

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..base_types import Target
from ..settings import Chat
from .theme import make_card


# (label, color) per Telegram chat type for the small pill.
_TYPE_LABEL: dict = {
    "private":    ("DM",       "#73C991"),
    "group":      ("Group",    "#4F86C6"),
    "supergroup": ("Group",    "#4F86C6"),
    "channel":    ("Channel",  "#C68F3E"),
}


class ChatsPanel(QWidget):
    """Pickable list of a bot's chats and forum topics."""

    selection_changed = Signal()
    add_chat_requested = Signal()
    star_toggled = Signal(int, bool)  # (chat_id, starred)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._bot = None
        self._rows: List["_ChatRow"] = []
        self._initial_selection: List[Tuple[int, Optional[int]]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # Toolbar: search + add.
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search chats…")
        self._search.textChanged.connect(self._apply_filter)
        bar.addWidget(self._search, 1)
        self._add_btn = QPushButton("+  Add chat")
        self._add_btn.setProperty("role", "ghost")
        self._add_btn.clicked.connect(self.add_chat_requested.emit)
        bar.addWidget(self._add_btn)
        outer.addLayout(bar)

        # Scroll list.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._inner = QWidget()
        self._inner_layout = QVBoxLayout(self._inner)
        self._inner_layout.setContentsMargins(0, 0, 0, 0)
        self._inner_layout.setSpacing(6)
        self._scroll.setWidget(self._inner)
        outer.addWidget(self._scroll, 1)

    # -- public ----------------------------------------------------- #

    def set_bot(self, bot) -> None:
        self._bot = bot
        self._reload()

    def restore_selection(self, picks: List[Tuple[int, Optional[int]]]) -> None:
        self._initial_selection = list(picks)
        self._apply_initial_selection()

    def picked_targets(self) -> List[Target]:
        out: List[Target] = []
        if self._bot is None:
            return out
        for row in self._rows:
            out.extend(row.picked())
        return out

    def picked_count(self) -> int:
        return len(self.picked_targets())

    # -- internals -------------------------------------------------- #

    def _reload(self) -> None:
        while self._inner_layout.count():
            it = self._inner_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        self._rows = []

        if self._bot is None or not self._bot.chats:
            self._inner_layout.addWidget(self._build_empty_state())
            self._inner_layout.addStretch(1)
            return

        chats = sorted(
            self._bot.chats,
            key=lambda c: (
                0 if c.starred else 1,
                -(c.last_sent_at or 0),
                -(c.last_seen or 0),
                (c.title or "").lower(),
            ),
        )
        for chat in chats:
            row = _ChatRow(chat, parent=self)
            row.toggled.connect(self.selection_changed.emit)
            row.star_toggled.connect(self.star_toggled.emit)
            self._rows.append(row)
            self._inner_layout.addWidget(row)
        self._inner_layout.addStretch(1)
        self._apply_initial_selection()

    def _build_empty_state(self) -> QWidget:
        card = make_card(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 24, 20, 24)
        layout.setSpacing(6)
        uname = (self._bot.telegram_username if self._bot else "") or "your-bot"

        title = QLabel("No chats yet")
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        body = QLabel(
            "Two ways to get chats here:<br>"
            f"<b>1.</b> Add <code>@{uname}</code> to a group or channel and send "
            f"<code>/start@{uname}</code> in it (or post as admin in a channel), "
            "then click Refresh.<br>"
            "<b>2.</b> Use <b>Add chat</b> above to add a chat by its id or "
            "@username (handy for DMs)."
        )
        body.setProperty("role", "subtitle")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignCenter)
        layout.addWidget(body)
        return card

    def _apply_filter(self, text: str) -> None:
        needle = (text or "").strip().lower()
        for row in self._rows:
            visible = (
                not needle
                or needle in row.chat.title.lower()
                or needle in (row.chat.username or "").lower()
            )
            row.setVisible(visible)

    def _apply_initial_selection(self) -> None:
        if not self._initial_selection:
            return
        wanted: Dict[int, Set[Optional[int]]] = {}
        for chat_id, topic_id in self._initial_selection:
            wanted.setdefault(int(chat_id), set()).add(
                int(topic_id) if topic_id is not None else None
            )
        for row in self._rows:
            topics = wanted.get(row.chat.id)
            if topics:
                row.preselect(topics)


# ---------------------------------------------------------------------------- #
# Chat card                                                                    #
# ---------------------------------------------------------------------------- #


class _ChatRow(QWidget):
    """One card in the chats panel. Contains the chat and any topics."""

    toggled = Signal()
    star_toggled = Signal(int, bool)

    def __init__(self, chat: Chat, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.chat = chat
        self._topic_checkboxes: List[Tuple[int, QCheckBox]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = make_card(self)
        outer.addWidget(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(4)

        # Top row: checkbox + pill + star.
        top = QHBoxLayout()
        top.setSpacing(8)

        self._main_cb = QCheckBox(chat.title or f"chat {chat.id}")
        self._main_cb.toggled.connect(self._on_main_toggle)
        # Re-emit as our zero-arg ``toggled`` signal. Direct
        # ``.emit`` connection would pass the bool through and the
        # zero-arg signal would TypeError.
        self._main_cb.toggled.connect(lambda _on: self.toggled.emit())
        font = self._main_cb.font()
        font.setWeight(font.Weight.DemiBold)
        self._main_cb.setFont(font)
        top.addWidget(self._main_cb, 1)

        label_text, color = _TYPE_LABEL.get(
            chat.type, (chat.type or "Chat", "#888888")
        )
        pill = QLabel(label_text)
        pill.setStyleSheet(
            f"color: {color}; border: 1px solid {color}; border-radius: 8px; "
            f"padding: 1px 8px; font-size: 10px; font-weight: 600;"
        )
        top.addWidget(pill, 0)

        self._star_btn = QToolButton()
        self._star_btn.setCheckable(True)
        self._star_btn.setChecked(chat.starred)
        self._star_btn.setFixedSize(QSize(24, 24))
        self._refresh_star_visual()
        self._star_btn.toggled.connect(self._on_star_toggled)
        top.addWidget(self._star_btn, 0)
        card_layout.addLayout(top)

        # Meta line (username / last sent / last seen).
        meta_text = self._meta_line()
        if meta_text:
            meta_row = QHBoxLayout()
            meta_row.setContentsMargins(0, 0, 0, 0)
            meta_row.setSpacing(0)
            meta_row.addSpacing(28)   # indent under the checkbox label
            meta_lbl = QLabel(meta_text)
            meta_lbl.setProperty("role", "subtitle")
            meta_row.addWidget(meta_lbl, 1)
            card_layout.addLayout(meta_row)

        # Topic checkboxes for forum supergroups.
        if chat.is_forum:
            topics_row = QVBoxLayout()
            topics_row.setContentsMargins(28, 4, 0, 0)
            topics_row.setSpacing(2)
            if chat.topics:
                for topic in chat.topics:
                    cb = QCheckBox(topic.name or f"Topic {topic.id}")
                    cb.toggled.connect(lambda _on: self.toggled.emit())
                    topics_row.addWidget(cb)
                    self._topic_checkboxes.append((topic.id, cb))
            else:
                empty = QLabel(
                    "No topics discovered yet. Send a message in each topic, "
                    "then Refresh."
                )
                empty.setProperty("role", "subtitle")
                empty.setWordWrap(True)
                topics_row.addWidget(empty)
            card_layout.addLayout(topics_row)

    # -- public ---------------------------------------------------- #

    def picked(self) -> List[Target]:
        out: List[Target] = []
        if not self._topic_checkboxes:
            if self._main_cb.isChecked():
                out.append(Target(chat=self.chat, topic_id=None))
            return out
        for topic_id, cb in self._topic_checkboxes:
            if cb.isChecked():
                out.append(Target(chat=self.chat, topic_id=topic_id))
        return out

    def preselect(self, topics: Set[Optional[int]]) -> None:
        if not self._topic_checkboxes:
            if None in topics:
                self._main_cb.setChecked(True)
            return
        for topic_id, cb in self._topic_checkboxes:
            if topic_id in topics:
                cb.setChecked(True)

    # -- behaviour ------------------------------------------------- #

    def _on_main_toggle(self, on: bool) -> None:
        if not self._topic_checkboxes:
            return
        # The top-level checkbox cascades to topics.
        for _topic_id, cb in self._topic_checkboxes:
            cb.blockSignals(True)
            cb.setChecked(on)
            cb.blockSignals(False)
        # Fire one signal at the end so listeners update.
        self.toggled.emit()

    def _on_star_toggled(self, on: bool) -> None:
        self._refresh_star_visual()
        self.star_toggled.emit(self.chat.id, on)

    def _refresh_star_visual(self) -> None:
        color = "#FFB347" if self._star_btn.isChecked() else "#666666"
        self._star_btn.setText("★" if self._star_btn.isChecked() else "☆")
        self._star_btn.setStyleSheet(
            f"QToolButton {{ color: {color}; font-size: 16px; "
            f"border: none; background: transparent; }} "
            f"QToolButton:hover {{ color: #FFC872; }}"
        )

    def _meta_line(self) -> str:
        chat = self.chat
        bits: List[str] = []
        if chat.username:
            bits.append("@" + chat.username)
        if chat.last_sent_at:
            bits.append("sent " + _relative_age(chat.last_sent_at))
        elif chat.last_seen:
            bits.append("seen " + _relative_age(chat.last_seen))
        return "  ·  ".join(bits)


def _relative_age(ts: int) -> str:
    delta = max(0, int(time.time()) - int(ts))
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    if delta < 86400 * 30:
        return f"{delta // 86400}d ago"
    return "long ago"
