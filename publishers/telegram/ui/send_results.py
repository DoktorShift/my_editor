"""Post-send view: per-target outcome with permalinks and quick actions.

Lives inside :class:`publishers.telegram.ui.publish_dialog.PublishDialog`
as a stacked page; the dialog flips to it once the publish job
completes. Each target gets a card row with a status badge, the
chat title, the permalink (clickable + copy), and an explicit
"Remove from cache" action for chats the bot was kicked from.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..publisher import PublishResult
from ..settings import Bot
from .theme import make_card, palette_for


class SendResultsView(QWidget):
    """Per-target outcome list + bulk actions."""

    edit_again_requested = Signal()
    pin_requested = Signal(list)              # list[chat_id]
    close_requested = Signal()
    remove_chat_requested = Signal(int)       # chat_id

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._results: List[PublishResult] = []
        self._bot: Optional[Bot] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        self._header = QLabel("")
        self._header.setProperty("role", "title")
        outer.addWidget(self._header)

        self._subheader = QLabel("")
        self._subheader.setProperty("role", "subtitle")
        outer.addWidget(self._subheader)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self._inner = QWidget()
        self._inner_layout = QVBoxLayout(self._inner)
        self._inner_layout.setContentsMargins(0, 0, 0, 0)
        self._inner_layout.setSpacing(8)
        scroll.setWidget(self._inner)
        outer.addWidget(scroll, 1)

        # Bulk actions row.
        bulk = QHBoxLayout()
        bulk.setSpacing(6)
        self._copy_all_btn = QPushButton("Copy all links")
        self._copy_all_btn.clicked.connect(self._on_copy_all)
        self._open_all_btn = QPushButton("Open all in Telegram")
        self._open_all_btn.clicked.connect(self._on_open_all)
        self._pin_btn = QPushButton("Pin in chats")
        self._pin_btn.clicked.connect(self._on_pin_clicked)
        bulk.addWidget(self._copy_all_btn)
        bulk.addWidget(self._open_all_btn)
        bulk.addWidget(self._pin_btn)
        bulk.addStretch(1)
        outer.addLayout(bulk)

        # Bottom buttons.
        bottom = QHBoxLayout()
        self._edit_btn = QPushButton("← Edit and send again")
        self._edit_btn.setProperty("role", "ghost")
        self._edit_btn.clicked.connect(self.edit_again_requested.emit)
        bottom.addWidget(self._edit_btn)
        bottom.addStretch(1)
        self._close_btn = QPushButton("Done")
        self._close_btn.setProperty("role", "primary")
        self._close_btn.clicked.connect(self.close_requested.emit)
        bottom.addWidget(self._close_btn)
        outer.addLayout(bottom)

    # -- populate -------------------------------------------------- #

    def populate(self, results: List[PublishResult], bot: Bot) -> None:
        self._results = list(results)
        self._bot = bot

        ok_count = sum(1 for r in results if r.ok)
        total = len(results)
        if total == 0:
            self._header.setText("Nothing sent.")
            self._subheader.setText("")
        elif ok_count == total:
            self._header.setText(
                f"Sent to {ok_count} chat" + ("s" if ok_count != 1 else "")
            )
            self._subheader.setText("Open the links below to view each post.")
        elif ok_count == 0:
            self._header.setText(
                f"Failed: 0 of {total} chat" + ("s" if total != 1 else "")
                + " accepted."
            )
            self._subheader.setText(
                "See the per-chat reason; fix and resend with Edit."
            )
        else:
            self._header.setText(f"Sent to {ok_count} of {total} chats")
            self._subheader.setText("Some chats failed; details below.")

        while self._inner_layout.count():
            it = self._inner_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        for r in results:
            self._inner_layout.addWidget(self._build_row(r))
        self._inner_layout.addStretch(1)

        any_link = any(r.ok and r.permalink for r in results)
        any_ok = any(r.ok for r in results)
        self._copy_all_btn.setEnabled(any_link)
        self._open_all_btn.setEnabled(any_link)
        self._pin_btn.setEnabled(any_ok)

    # -- one row --------------------------------------------------- #

    def _build_row(self, r: PublishResult) -> QWidget:
        card = make_card(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        status_color = "#73C991" if r.ok else "#F48771"
        status_text = "✓" if r.ok else "✕"
        status = QLabel(status_text)
        status.setFixedSize(28, 28)
        status.setAlignment(Qt.AlignCenter)
        status.setStyleSheet(
            f"color: white; background: {status_color}; "
            "border-radius: 14px; font-weight: 700; font-size: 14px;"
        )
        layout.addWidget(status, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(2)
        title = self._chat_title(r.chat_id)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-weight: 600; font-size: 13px;")
        col.addWidget(title_lbl)

        if r.ok and r.permalink:
            link_lbl = QLabel(
                f"<a href='{r.permalink}' style='color:#4F86C6'>"
                f"{r.permalink}</a>"
            )
            link_lbl.setOpenExternalLinks(True)
            link_lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
            col.addWidget(link_lbl)
        elif r.ok:
            note = QLabel("Sent. No public link for this chat type.")
            note.setProperty("role", "subtitle")
            col.addWidget(note)
        else:
            err = QLabel(r.message or "Failed.")
            err.setProperty("role", "subtitle")
            err.setWordWrap(True)
            col.addWidget(err)

        layout.addLayout(col, 1)

        # Right-hand inline buttons.
        actions = QHBoxLayout()
        actions.setSpacing(4)
        if r.ok and r.permalink:
            copy_btn = QPushButton("Copy")
            copy_btn.clicked.connect(
                lambda _c=False, u=r.permalink: QGuiApplication.clipboard().setText(u)
            )
            copy_btn.clicked.connect(lambda _c=False, b=copy_btn: b.setText("Copied"))
            open_btn = QPushButton("Open")
            open_btn.clicked.connect(
                lambda _c=False, u=r.permalink: QDesktopServices.openUrl(QUrl(u))
            )
            actions.addWidget(copy_btn)
            actions.addWidget(open_btn)
        elif not r.ok and _looks_like_dead_chat(r.message):
            remove_btn = QPushButton("Remove from list")
            remove_btn.setProperty("role", "destructive")
            remove_btn.clicked.connect(
                lambda _c=False, cid=r.chat_id: self.remove_chat_requested.emit(cid)
            )
            actions.addWidget(remove_btn)
        layout.addLayout(actions)

        return card

    def _chat_title(self, chat_id: int) -> str:
        if self._bot is not None:
            chat = self._bot.chat_by_id(chat_id)
            if chat is not None and chat.title:
                return chat.title
        return f"chat {chat_id}"

    # -- bulk actions ---------------------------------------------- #

    def _on_copy_all(self) -> None:
        lines = []
        for r in self._results:
            if not r.ok or not r.permalink:
                continue
            title = self._chat_title(r.chat_id)
            lines.append(f"{title}: {r.permalink}" if title else r.permalink)
        QGuiApplication.clipboard().setText("\n".join(lines))

    def _on_open_all(self) -> None:
        opened = 0
        for r in self._results:
            if not r.ok or not r.permalink or opened >= 5:
                continue
            QDesktopServices.openUrl(QUrl(r.permalink))
            opened += 1

    def _on_pin_clicked(self) -> None:
        ids = [r.chat_id for r in self._results if r.ok and r.message_id]
        if ids:
            self.pin_requested.emit(ids)

    # -- accessors ------------------------------------------------- #

    def results_by_chat(self) -> dict:
        return {r.chat_id: r for r in self._results}


def _looks_like_dead_chat(message: str) -> bool:
    m = (message or "").lower()
    return any(s in m for s in (
        "kicked", "blocked", "not a member", "chat not found",
        "user is deactivated",
    ))
