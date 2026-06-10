"""Connect a Telegram bot in two clean steps.

Step 1: paste the token from BotFather and verify. The dialog calls
``getMe`` against the token and renders the resulting identity card.

Step 2: give the bot a friendly display name, pick a color, and
optionally set it as the default. The "Add" button is only enabled
after a successful verification.

Emits ``added(bot_id)`` so callers (the bots manager, the publish
dialog) can react.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..bots import looks_like_token, parse_token
from ..errors import ApiError
from ..settings import BOT_COLORS
from .bot_chip import BotAvatarLabel, make_bot_avatar
from .theme import apply, make_card, make_divider, palette_for


class AddBotDialog(QDialog):
    added = Signal(str)  # new bot id

    def __init__(self, publisher, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._publisher = publisher
        self._settings = publisher.settings
        self._verified_payload: Optional[dict] = None
        self._verified_token: str = ""
        self._chosen_color: str = self._suggest_color()
        self._color_buttons: list[QToolButton] = []

        self.setWindowTitle("Connect a Telegram bot")
        self.setModal(True)
        self.setMinimumWidth(540)
        self.setMinimumHeight(560)

        self._build_ui()
        apply(self)
        self._set_verified(False)

    # -- UI ---------------------------------------------------------- #

    def _build_ui(self) -> None:
        p = palette_for(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 22)
        root.setSpacing(16)

        title = QLabel("Connect a Telegram bot")
        title.setProperty("role", "title")
        root.addWidget(title)

        subtitle = QLabel(
            "Bots post on behalf of your product. Get a token from "
            "<a href='https://t.me/BotFather' style='color:" + p["accent"] + "'>"
            "BotFather</a> on Telegram, then paste it below."
        )
        subtitle.setOpenExternalLinks(True)
        subtitle.setProperty("role", "subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # -- step 1: token ------------------------------------------ #
        section1 = QLabel("Step 1  ·  Paste your bot token")
        section1.setProperty("role", "section")
        root.addWidget(section1)

        token_row = QHBoxLayout()
        token_row.setSpacing(8)
        self._token_edit = QLineEdit()
        self._token_edit.setEchoMode(QLineEdit.Password)
        self._token_edit.setPlaceholderText("123456789:AAH…")
        self._token_edit.textChanged.connect(self._on_token_changed)
        self._token_edit.returnPressed.connect(self._on_verify)
        token_row.addWidget(self._token_edit, 1)
        self._reveal_btn = QToolButton()
        self._reveal_btn.setText("Show")
        self._reveal_btn.setCheckable(True)
        self._reveal_btn.toggled.connect(self._toggle_reveal)
        token_row.addWidget(self._reveal_btn)
        self._verify_btn = QPushButton("Verify")
        self._verify_btn.setProperty("role", "primary")
        self._verify_btn.clicked.connect(self._on_verify)
        token_row.addWidget(self._verify_btn)
        root.addLayout(token_row)

        # Token help line + BotFather quick link.
        help_row = QHBoxLayout()
        help_row.setSpacing(6)
        help_lbl = QLabel(
            "In BotFather: send <code>/newbot</code> to create one, or "
            "<code>/mybots</code> &rarr; your bot &rarr; <i>API Token</i> to copy an existing one."
        )
        help_lbl.setProperty("role", "subtitle")
        help_lbl.setWordWrap(True)
        help_row.addWidget(help_lbl, 1)
        bf_btn = QPushButton("Open BotFather")
        bf_btn.setProperty("role", "ghost")
        bf_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://t.me/BotFather"))
        )
        help_row.addWidget(bf_btn)
        root.addLayout(help_row)

        # Verify status (error in red, "checking..." in muted) and
        # verified identity card share the same vertical slot. The
        # stacked widget swaps them so only one is visible at a time.
        self._status_stack = QStackedWidget()
        self._status_stack.setMinimumHeight(110)
        root.addWidget(self._status_stack)

        self._status_msg = QLabel("")
        self._status_msg.setWordWrap(True)
        self._status_msg.setAlignment(Qt.AlignTop)
        self._status_stack.addWidget(self._status_msg)

        # Verified card: avatar + identity.
        self._verified_card = make_card(self)
        card_layout = QHBoxLayout(self._verified_card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(14)
        self._verified_avatar = BotAvatarLabel("?", BOT_COLORS[0], size=48)
        card_layout.addWidget(self._verified_avatar)
        identity_col = QVBoxLayout()
        identity_col.setSpacing(2)
        self._verified_name = QLabel("")
        self._verified_name.setStyleSheet("font-weight: 600; font-size: 14px;")
        identity_col.addWidget(self._verified_name)
        self._verified_username = QLabel("")
        self._verified_username.setProperty("role", "subtitle")
        identity_col.addWidget(self._verified_username)
        self._verified_ok_lbl = QLabel("✓ Token verified")
        self._verified_ok_lbl.setProperty("status", "ok")
        identity_col.addWidget(self._verified_ok_lbl)
        identity_col.addStretch(1)
        card_layout.addLayout(identity_col, 1)
        self._status_stack.addWidget(self._verified_card)

        root.addWidget(make_divider(self))

        # -- step 2: name + color ----------------------------------- #
        section2 = QLabel("Step 2  ·  Name this bot in the editor")
        section2.setProperty("role", "section")
        root.addWidget(section2)

        name_row = QHBoxLayout()
        name_row.setSpacing(10)
        self._preview_avatar = BotAvatarLabel("?", self._chosen_color, size=32)
        name_row.addWidget(self._preview_avatar)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. Product Updates")
        self._name_edit.textChanged.connect(self._refresh_preview_avatar)
        name_row.addWidget(self._name_edit, 1)
        root.addLayout(name_row)

        color_row = QHBoxLayout()
        color_row.setSpacing(6)
        color_lbl = QLabel("Colour")
        color_lbl.setProperty("role", "subtitle")
        color_row.addWidget(color_lbl)
        for hex_color in BOT_COLORS:
            swatch = self._make_swatch(hex_color)
            self._color_buttons.append(swatch)
            color_row.addWidget(swatch)
        color_row.addStretch(1)
        root.addLayout(color_row)

        self._default_chk = QCheckBox("Make this the default bot")
        self._default_chk.setChecked(len(self._settings.bots) == 0)
        root.addWidget(self._default_chk)

        root.addStretch(1)

        # Bottom buttons.
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self._add_btn = QPushButton("Add bot")
        self._add_btn.setProperty("role", "primary")
        self._add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(self._add_btn)
        root.addLayout(btn_row)

        self._refresh_color_buttons()

    # -- swatch button --------------------------------------------- #

    def _make_swatch(self, hex_color: str) -> QToolButton:
        btn = QToolButton(self)
        btn.setFixedSize(QSize(22, 22))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(hex_color)
        btn.clicked.connect(lambda _checked=False, c=hex_color: self._pick_color(c))
        return btn

    def _refresh_color_buttons(self) -> None:
        for btn, hex_color in zip(self._color_buttons, BOT_COLORS):
            border = "#FFFFFF" if hex_color == self._chosen_color else "transparent"
            btn.setStyleSheet(
                f"""
                QToolButton {{
                    background: {hex_color};
                    border: 2px solid {border};
                    border-radius: 11px;
                }}
                QToolButton:hover {{
                    border: 2px solid #AAAAAA;
                }}
                """
            )

    def _pick_color(self, hex_color: str) -> None:
        self._chosen_color = hex_color
        self._refresh_color_buttons()
        self._refresh_preview_avatar()

    # -- behaviour -------------------------------------------------- #

    def _on_token_changed(self, text: str) -> None:
        cleaned = parse_token(text)
        self._verify_btn.setEnabled(looks_like_token(cleaned))
        if cleaned != self._verified_token:
            self._set_verified(False)

    def _toggle_reveal(self, on: bool) -> None:
        self._token_edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        self._reveal_btn.setText("Hide" if on else "Show")

    def _on_verify(self) -> None:
        cleaned = parse_token(self._token_edit.text())
        if not looks_like_token(cleaned):
            self._show_status("That doesn't look like a token.", "error")
            return
        for existing in self._settings.bots:
            if existing.token == cleaned:
                self._show_status(
                    f'You already added this bot as "{existing.display_name}".',
                    "warn",
                )
                return

        self._verify_btn.setEnabled(False)
        self._verify_btn.setText("Verifying…")
        self._show_status("Reaching out to Telegram…", "info")

        from ..api import BotApi
        api = BotApi(cleaned, parent=self)
        call = api.get_me()
        call.succeeded.connect(lambda result, t=cleaned: self._on_verified(t, result))
        call.failed.connect(self._on_verify_failed)

    def _on_verified(self, token: str, result: dict) -> None:
        self._verify_btn.setEnabled(True)
        self._verify_btn.setText("Verify")
        user_id = int(result.get("id") or 0)
        username = str(result.get("username") or "")
        first_name = str(result.get("first_name") or "")

        existing = self._settings.bot_by_telegram_user_id(user_id)
        if existing is not None:
            self._show_status(
                f'You already added this bot as "{existing.display_name}".',
                "warn",
            )
            return

        self._verified_token = token
        self._verified_payload = {
            "id": user_id,
            "username": username,
            "first_name": first_name,
        }
        self._verified_name.setText(first_name or username or "(unnamed)")
        self._verified_username.setText("@" + username if username else "(no @username)")
        self._verified_avatar.set_bot(first_name or username or "?", self._chosen_color)
        if not self._name_edit.text().strip():
            self._name_edit.setText(first_name)
        self._set_verified(True)

    def _on_verify_failed(self, err) -> None:
        self._verify_btn.setEnabled(True)
        self._verify_btn.setText("Verify")
        message = err.user_message if isinstance(err, ApiError) else str(err)
        self._show_status(message, "error")

    def _on_add(self) -> None:
        if self._verified_payload is None:
            self._show_status("Verify the token first.", "warn")
            return
        name = self._name_edit.text().strip()
        if not name:
            self._show_status("Pick a display name.", "warn")
            return
        try:
            bot = self._settings.add_bot(
                display_name=name,
                token=self._verified_token,
                telegram_username=self._verified_payload["username"],
                telegram_first_name=self._verified_payload["first_name"],
                telegram_user_id=int(self._verified_payload["id"]),
                make_default=self._default_chk.isChecked(),
            )
        except ValueError as exc:
            self._show_status(str(exc), "error")
            return
        # Persist the chosen color too.
        self._settings.recolor_bot(bot.id, self._chosen_color)
        self.added.emit(bot.id)
        self.accept()

    # -- helpers --------------------------------------------------- #

    def _set_verified(self, verified: bool) -> None:
        if verified:
            self._status_stack.setCurrentWidget(self._verified_card)
        else:
            self._status_stack.setCurrentWidget(self._status_msg)
        self._name_edit.setEnabled(verified)
        self._default_chk.setEnabled(verified)
        for btn in self._color_buttons:
            btn.setEnabled(verified)
        self._add_btn.setEnabled(verified)
        if not verified:
            self._verified_payload = None
            self._verified_token = ""

    def _refresh_preview_avatar(self) -> None:
        name = self._name_edit.text().strip() or "?"
        self._preview_avatar.set_bot(name, self._chosen_color)
        self._verified_avatar.set_bot(name, self._chosen_color)

    def _show_status(self, message: str, kind: str) -> None:
        self._status_stack.setCurrentWidget(self._status_msg)
        self._status_msg.setText(message)
        self._status_msg.setProperty("status", kind)
        self._status_msg.style().unpolish(self._status_msg)
        self._status_msg.style().polish(self._status_msg)

    def _suggest_color(self) -> str:
        used = {b.color for b in self._settings.bots}
        for c in BOT_COLORS:
            if c not in used:
                return c
        return BOT_COLORS[len(self._settings.bots) % len(BOT_COLORS)]
