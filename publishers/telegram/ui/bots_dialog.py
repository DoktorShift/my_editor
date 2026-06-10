"""Multi-bot manager - card-list layout.

Each connected bot is rendered as a self-contained card row with
its avatar, name, identity, chat-count, and a single "..." menu
button that fans out to all per-bot actions (rename, recolor,
default, replace token, show token, invite links, remove).

Below the list, a single primary "Add a bot" button. Empty state
shows a friendly callout instead of a blank list.
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..settings import BOT_COLORS, Bot
from .add_bot_dialog import AddBotDialog
from .bot_chip import BotAvatarLabel
from .invite_picker import InvitePicker
from .theme import apply, make_card, palette_for


class BotsDialog(QDialog):
    """Top-level dialog reached from the Telegram menu."""

    bots_changed = Signal()

    def __init__(self, publisher, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._publisher = publisher
        self._settings = publisher.settings

        self.setWindowTitle("Telegram bots")
        self.setModal(True)
        self.setMinimumSize(580, 520)

        self._build_ui()
        apply(self)
        self._reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 22)
        root.setSpacing(14)

        title = QLabel("Telegram bots")
        title.setProperty("role", "title")
        root.addWidget(title)

        subtitle = QLabel(
            "Each bot has its own identity, chat list, and token. "
            "Add as many as you need - product updates, internal alerts, community channel."
        )
        subtitle.setProperty("role", "subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # Scrollable card list.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll_inner = QWidget()
        self._inner_layout = QVBoxLayout(self._scroll_inner)
        self._inner_layout.setContentsMargins(0, 4, 0, 4)
        self._inner_layout.setSpacing(10)
        self._scroll.setWidget(self._scroll_inner)
        root.addWidget(self._scroll, 1)

        # Bottom action row.
        actions = QHBoxLayout()
        self._add_btn = QPushButton("+  Add a bot")
        self._add_btn.setProperty("role", "primary")
        self._add_btn.clicked.connect(self._on_add)
        actions.addWidget(self._add_btn)
        actions.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        actions.addWidget(close_btn)
        root.addLayout(actions)

    # -- population ------------------------------------------------ #

    def _reload(self) -> None:
        # Clear current rows.
        while self._inner_layout.count():
            item = self._inner_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        default_id = (
            self._settings.default_bot.id if self._settings.default_bot else ""
        )

        if not self._settings.bots:
            self._inner_layout.addWidget(self._build_empty_state())
            self._inner_layout.addStretch(1)
            return

        for bot in self._settings.bots:
            card = self._build_bot_card(bot, is_default=(bot.id == default_id))
            self._inner_layout.addWidget(card)
        self._inner_layout.addStretch(1)

    def _build_empty_state(self) -> QWidget:
        card = make_card(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 32, 24, 32)
        layout.setSpacing(8)
        title = QLabel("No bots yet")
        title.setStyleSheet("font-weight: 600; font-size: 16px;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        body = QLabel(
            "Connect your first bot to start posting updates to Telegram. "
            "Bots are free to create through BotFather, and you can have as many as you want."
        )
        body.setProperty("role", "subtitle")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignCenter)
        layout.addWidget(body)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn = QPushButton("Connect a bot")
        btn.setProperty("role", "primary")
        btn.clicked.connect(self._on_add)
        btn_row.addWidget(btn)
        btn_row.addStretch(1)
        layout.addSpacing(8)
        layout.addLayout(btn_row)
        return card

    def _build_bot_card(self, bot: Bot, *, is_default: bool) -> QWidget:
        card = make_card(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(16, 14, 12, 14)
        layout.setSpacing(14)

        avatar = BotAvatarLabel(bot.display_name, bot.color, size=44)
        layout.addWidget(avatar, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(2)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_lbl = QLabel(bot.display_name)
        name_lbl.setStyleSheet("font-weight: 600; font-size: 14px;")
        name_row.addWidget(name_lbl)
        if is_default:
            badge = self._badge("Default", palette_for(self)["accent"])
            name_row.addWidget(badge)
        if bot.invalid:
            name_row.addWidget(self._badge("Token invalid", palette_for(self)["error"]))
        name_row.addStretch(1)
        col.addLayout(name_row)

        meta_bits = []
        if bot.telegram_username:
            meta_bits.append("@" + bot.telegram_username)
        chat_count = len(bot.chats)
        meta_bits.append(
            f"{chat_count} chat" + ("s" if chat_count != 1 else "")
        )
        meta_bits.append("added " + _format_relative(bot.added_at))
        meta_lbl = QLabel("  ·  ".join(meta_bits))
        meta_lbl.setProperty("role", "subtitle")
        col.addWidget(meta_lbl)

        layout.addLayout(col, 1)

        menu_btn = self._build_actions_button(bot, is_default=is_default)
        layout.addWidget(menu_btn, 0, Qt.AlignTop)

        return card

    def _badge(self, text: str, color: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"""
            background: rgba(0,0,0,0);
            color: {color};
            border: 1px solid {color};
            border-radius: 8px;
            padding: 1px 8px;
            font-size: 10px;
            font-weight: 600;
            """
        )
        return lbl

    def _build_actions_button(self, bot: Bot, *, is_default: bool) -> QToolButton:
        btn = QToolButton(self)
        btn.setText("⋯")
        btn.setFixedSize(QSize(32, 32))
        btn.setPopupMode(QToolButton.InstantPopup)
        btn.setStyleSheet(
            """
            QToolButton {
                font-size: 18px;
                font-weight: 700;
                border-radius: 6px;
            }
            """
        )

        menu = QMenu(btn)
        if not is_default:
            act = QAction("Set as default", menu)
            act.triggered.connect(lambda: self._action_set_default(bot.id))
            menu.addAction(act)
        act = QAction("Rename…", menu)
        act.triggered.connect(lambda: self._action_rename(bot.id))
        menu.addAction(act)
        act = QAction("Change colour…", menu)
        act.triggered.connect(lambda: self._action_recolor(bot.id))
        menu.addAction(act)
        menu.addSeparator()
        act = QAction("Invite links…", menu)
        act.triggered.connect(lambda: self._action_invite(bot.id))
        menu.addAction(act)
        act = QAction("Show token", menu)
        act.triggered.connect(lambda: self._action_show_token(bot.id))
        menu.addAction(act)
        act = QAction("Replace token…", menu)
        act.triggered.connect(lambda: self._action_replace_token(bot.id))
        menu.addAction(act)
        menu.addSeparator()
        act = QAction("Remove bot…", menu)
        act.triggered.connect(lambda: self._action_remove(bot.id))
        menu.addAction(act)

        btn.setMenu(menu)
        return btn

    # -- actions --------------------------------------------------- #

    def _on_add(self) -> None:
        dlg = AddBotDialog(self._publisher, parent=self)
        dlg.added.connect(self._on_bot_added)
        dlg.exec()

    def _on_bot_added(self, _bot_id: str) -> None:
        self._reload()
        self.bots_changed.emit()

    def _action_set_default(self, bot_id: str) -> None:
        self._settings.set_default_bot(bot_id)
        self._reload()
        self.bots_changed.emit()

    def _action_rename(self, bot_id: str) -> None:
        bot = self._settings.bot_by_id(bot_id)
        if bot is None:
            return
        name, ok = QInputDialog.getText(
            self, "Rename bot", "Display name:", QLineEdit.Normal, bot.display_name
        )
        if ok and name.strip():
            self._settings.rename_bot(bot.id, name.strip())
            self._reload()
            self.bots_changed.emit()

    def _action_recolor(self, bot_id: str) -> None:
        bot = self._settings.bot_by_id(bot_id)
        if bot is None:
            return
        ColorPickerDialog(bot, self._settings, parent=self).exec()
        self._reload()
        self.bots_changed.emit()

    def _action_invite(self, bot_id: str) -> None:
        bot = self._settings.bot_by_id(bot_id)
        if bot is None or not bot.telegram_username:
            QMessageBox.information(
                self, "Invite links",
                "This bot does not have a @username yet, so it has no invite links.",
            )
            return
        InvitePicker(bot.telegram_username, parent=self).exec()

    def _action_show_token(self, bot_id: str) -> None:
        bot = self._settings.bot_by_id(bot_id)
        if bot is None:
            return
        TokenRevealDialog(bot, parent=self).exec()

    def _action_replace_token(self, bot_id: str) -> None:
        bot = self._settings.bot_by_id(bot_id)
        if bot is None:
            return
        token, ok = QInputDialog.getText(
            self, "Replace token",
            f"Paste a new token for {bot.display_name}:",
            QLineEdit.Password,
        )
        if not ok or not token.strip():
            return
        from ..api import BotApi
        from ..bots import looks_like_token, parse_token
        cleaned = parse_token(token)
        if not looks_like_token(cleaned):
            QMessageBox.warning(self, "Replace token", "That doesn't look like a token.")
            return
        api = BotApi(cleaned, parent=self)
        call = api.get_me()

        def _on_ok(result):
            if int(result.get("id") or 0) != bot.telegram_user_id:
                QMessageBox.warning(
                    self, "Replace token",
                    "That token belongs to a different bot. Refusing to replace.",
                )
                return
            self._settings.replace_token(bot.id, cleaned)
            self._publisher.bots.discard(bot.id)
            self._reload()
            self.bots_changed.emit()
            QMessageBox.information(self, "Replace token", "Token updated.")

        def _on_fail(err):
            QMessageBox.warning(self, "Replace token", err.user_message)

        call.succeeded.connect(_on_ok)
        call.failed.connect(_on_fail)

    def _action_remove(self, bot_id: str) -> None:
        bot = self._settings.bot_by_id(bot_id)
        if bot is None:
            return
        pending_count = sum(
            1 for p in self._publisher.queue.by_bot(bot.id) if p.status == "pending"
        )
        text = (
            f"Remove \"{bot.display_name}\"?\n\n"
            f"The bot itself stays alive on Telegram - you can re-add it later by\n"
            f"pasting the same token. The cached list of {len(bot.chats)} chats and\n"
            f"this bot's defaults are forgotten."
        )
        if pending_count:
            text += (
                f"\n\nThere {'is' if pending_count == 1 else 'are'} "
                f"{pending_count} pending scheduled post"
                f"{'' if pending_count == 1 else 's'} for this bot; they will be marked orphaned."
            )
        confirm = QMessageBox.question(
            self, "Remove bot", text,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._publisher.queue.orphan_bot_posts(bot.id)
        self._publisher.bots.discard(bot.id)
        self._publisher.file_ids.evict_bot(bot.id)
        self._settings.remove_bot(bot.id)
        self._reload()
        self.bots_changed.emit()


# ---------------------------------------------------------------------------- #
# Small helper dialogs                                                         #
# ---------------------------------------------------------------------------- #


class ColorPickerDialog(QDialog):
    """Pick from the 8 preset colors."""

    def __init__(self, bot: Bot, settings, parent: QWidget) -> None:
        super().__init__(parent)
        self._bot = bot
        self._settings = settings
        self.setWindowTitle("Bot colour")
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        lbl = QLabel(f"Pick a colour for {bot.display_name}:")
        layout.addWidget(lbl)

        avatar_row = QHBoxLayout()
        avatar_row.setSpacing(10)
        avatar_row.addStretch(1)
        self._preview = BotAvatarLabel(bot.display_name, bot.color, size=48)
        avatar_row.addWidget(self._preview)
        avatar_row.addStretch(1)
        layout.addLayout(avatar_row)

        swatches = QHBoxLayout()
        swatches.setSpacing(8)
        swatches.addStretch(1)
        self._buttons = []
        self._chosen = bot.color
        for hex_color in BOT_COLORS:
            btn = QToolButton(self)
            btn.setFixedSize(QSize(28, 28))
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, c=hex_color: self._pick(c))
            self._buttons.append((btn, hex_color))
            swatches.addWidget(btn)
        swatches.addStretch(1)
        layout.addLayout(swatches)
        self._refresh()

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        save = QPushButton("Save")
        save.setProperty("role", "primary")
        save.clicked.connect(self._save)
        actions.addWidget(save)
        layout.addLayout(actions)
        apply(self)

    def _pick(self, hex_color: str) -> None:
        self._chosen = hex_color
        self._preview.set_bot(self._bot.display_name, hex_color)
        self._refresh()

    def _refresh(self) -> None:
        for btn, hex_color in self._buttons:
            border = "#FFFFFF" if hex_color == self._chosen else "transparent"
            btn.setStyleSheet(
                f"""
                QToolButton {{
                    background: {hex_color};
                    border: 2px solid {border};
                    border-radius: 14px;
                }}
                QToolButton:hover {{ border-color: #AAAAAA; }}
                """
            )

    def _save(self) -> None:
        self._settings.recolor_bot(self._bot.id, self._chosen)
        self.accept()


class TokenRevealDialog(QDialog):
    """Reveal the bot's token with a single click-to-copy."""

    def __init__(self, bot: Bot, parent: QWidget) -> None:
        super().__init__(parent)
        self._bot = bot
        self.setWindowTitle("Bot token")
        self.setModal(True)
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)

        title = QLabel(f"Token for {bot.display_name}")
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(title)

        warn = QLabel(
            "Anyone with this token can post as your bot. Treat it like a password."
        )
        warn.setProperty("role", "subtitle")
        warn.setWordWrap(True)
        layout.addWidget(warn)

        token_edit = QLineEdit(bot.token)
        token_edit.setReadOnly(True)
        token_edit.setCursorPosition(0)
        layout.addWidget(token_edit)

        actions = QHBoxLayout()
        actions.addStretch(1)
        copy_btn = QPushButton("Copy")
        copy_btn.setProperty("role", "primary")
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(bot.token))
        copy_btn.clicked.connect(lambda: copy_btn.setText("Copied ✓"))
        actions.addWidget(copy_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

        apply(self)


# ---------------------------------------------------------------------------- #
# Misc                                                                         #
# ---------------------------------------------------------------------------- #


def _format_relative(ts: int) -> str:
    if not ts:
        return "just now"
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
