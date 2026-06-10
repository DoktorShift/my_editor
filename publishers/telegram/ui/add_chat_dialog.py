"""Manually add a chat to a bot's cache by ID or @username.

Useful when:

* The chat is not yet seen via ``getUpdates`` (e.g. you just added the
  bot to a private channel as admin and don't want to wait for the
  first message to arrive).
* The target is a DM where the recipient has already started the bot
  on another device, and you know their numeric chat id (Telegram
  user id).
* You're testing and want to send DMs to yourself; here the chat id
  is your own Telegram user id, fetchable from any of the @userinfobot
  variants - or by sending /start to your bot and then refreshing
  this list normally.

Calls ``getChat`` to validate and fetch the title. On success, the
chat is added to the per-bot cache and the publish dialog refreshes.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..api import BotApi
from ..errors import ApiError
from ..settings import Chat
from .theme import apply, palette_for


class AddChatDialog(QDialog):
    """Ask for a chat id or @username, validate via getChat, persist."""

    added = Signal(int)  # chat_id

    def __init__(
        self,
        *,
        api: BotApi,
        settings,
        bot_id: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._settings = settings
        self._bot_id = bot_id

        self.setWindowTitle("Add a chat")
        self.setModal(True)
        self.setMinimumWidth(460)

        self._build_ui()
        apply(self)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(12)

        title = QLabel("Add a chat manually")
        title.setProperty("role", "title")
        root.addWidget(title)

        body = QLabel(
            "Paste the chat's numeric ID or a public @username. The editor calls "
            "Telegram to confirm the chat exists and to fetch its title."
        )
        body.setProperty("role", "subtitle")
        body.setWordWrap(True)
        root.addWidget(body)

        self._input = QLineEdit()
        self._input.setPlaceholderText("e.g. @mychannel  or  -1001234567890  or  12345678")
        self._input.returnPressed.connect(self._on_lookup)
        root.addWidget(self._input)

        hint = QLabel(
            "<b>Groups / channels:</b> the bot must already be a member.<br>"
            "<b>DMs:</b> the recipient must have sent /start to the bot at least "
            "once. Their chat id is their Telegram user id (a positive number)."
        )
        hint.setProperty("role", "subtitle")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setMinimumHeight(36)
        root.addWidget(self._status)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self._add_btn = QPushButton("Look up and add")
        self._add_btn.setProperty("role", "primary")
        self._add_btn.clicked.connect(self._on_lookup)
        actions.addWidget(self._add_btn)
        root.addLayout(actions)

    def _on_lookup(self) -> None:
        raw = (self._input.text() or "").strip()
        if not raw:
            self._show("Enter a chat id or @username.", "warn")
            return
        # Normalise the input.
        target = raw
        if not raw.startswith("@") and not raw.lstrip("-").isdigit():
            # Looks like a username without the @.
            target = "@" + raw

        self._add_btn.setEnabled(False)
        self._add_btn.setText("Looking up…")
        self._show("Asking Telegram about this chat…", "info")

        call = self._api.get_chat(target)
        call.succeeded.connect(self._on_ok)
        call.failed.connect(self._on_fail)

    def _on_ok(self, result: dict) -> None:
        self._add_btn.setEnabled(True)
        self._add_btn.setText("Look up and add")
        try:
            chat_id = int(result.get("id"))
        except (TypeError, ValueError):
            self._show("Telegram returned an unexpected response.", "error")
            return
        chat_type = str(result.get("type") or "")
        title = str(result.get("title") or "")
        if not title:
            title = (
                str(result.get("first_name") or "")
                + (" " + str(result.get("last_name") or "")
                   if result.get("last_name") else "")
            ).strip()
        if not title:
            uname = result.get("username")
            title = "@" + str(uname) if uname else str(chat_id)
        chat = Chat(
            id=chat_id,
            type=chat_type,
            title=title,
            username=str(result.get("username") or ""),
            is_forum=bool(result.get("is_forum") or False),
        )
        self._settings.upsert_chat(self._bot_id, chat)
        self.added.emit(chat_id)
        self.accept()

    def _on_fail(self, err) -> None:
        self._add_btn.setEnabled(True)
        self._add_btn.setText("Look up and add")
        message = err.user_message if isinstance(err, ApiError) else str(err)
        self._show(message, "error")

    def _show(self, text: str, kind: str) -> None:
        self._status.setText(text)
        self._status.setProperty("status", kind)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)
