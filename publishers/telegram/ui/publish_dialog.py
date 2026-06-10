"""The main "publish to Telegram" composer.

Layout:

  +--------------------------------------------------------------+
  | [chip] Sending as Product Updates    @MyProductBot  Manage…  |
  |--------------------------------------------------------------|
  |  Message                          | To                       |
  |  [body editor, proportional       | [search]    [+ Add chat] |
  |   font, dominant]                 |                          |
  |                                   | [chat card]              |
  |                                   | [chat card]              |
  |                                   |                          |
  |  3 messages · 412 chars · + photo |                          |
  |--------------------------------------------------------------|
  | When: ( ) Now  (•) Schedule [picker]                         |
  | Options:  [silent] [no preview] [protect] [pin]              |
  | status: ...                                                  |
  | [cancel]                                  [SEND to 2 chats]  |
  +--------------------------------------------------------------+

On send, the stacked widget flips to the results view with
permalinks per target.
"""

from __future__ import annotations

import time
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..base_types import SendOptions, Target
from ..chats import ChatDiscovery
from ..errors import WebhookActive
from ..format import TEXT_LIMIT, format_for_send
from ..publisher import PublishResult, TelegramPublishJob
from ..queue import ScheduledTarget
from .add_bot_dialog import AddBotDialog
from .add_chat_dialog import AddChatDialog
from .bot_chip import BotChip
from .bots_dialog import BotsDialog
from .chats_panel import ChatsPanel
from .schedule_widget import MODE_SCHEDULE, ScheduleWidget
from .send_results import SendResultsView
from .theme import apply, make_divider, palette_for


class PublishDialog(QDialog):
    """Compose -> send -> results, all in one dialog."""

    def __init__(
        self,
        publisher,
        *,
        body_markdown: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._publisher = publisher
        self._settings = publisher.settings
        self._active_bot_id: str = ""
        self._job: Optional[TelegramPublishJob] = None
        self._discovery: Optional[ChatDiscovery] = None

        self.setWindowTitle("Publish to Telegram")
        self.setModal(True)
        self.setMinimumSize(940, 620)

        self._build_ui(body_markdown)
        apply(self)
        self._pick_initial_bot()
        self._update_send_button()
        self._setup_shortcuts()

    # -- UI -------------------------------------------------------- #

    def _build_ui(self, body_markdown: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(14)

        # Header.
        root.addLayout(self._build_header())
        root.addWidget(make_divider(self))

        # Main stacked area: compose | results.
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        self._compose_page = self._build_compose_page(body_markdown)
        self._stack.addWidget(self._compose_page)

        self._results_view = SendResultsView()
        self._results_view.edit_again_requested.connect(self._show_compose)
        self._results_view.close_requested.connect(self.accept)
        self._results_view.pin_requested.connect(self._on_pin_targets)
        self._results_view.remove_chat_requested.connect(self._on_remove_chat)
        self._stack.addWidget(self._results_view)

    def _build_header(self) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(10)

        sending_as = QLabel("Sending as")
        sending_as.setProperty("role", "subtitle")
        h.addWidget(sending_as)

        self._bot_chip = BotChip(
            on_pick=self._switch_to_bot,
            on_add=self._on_add_bot,
            get_entries=self._bot_entries,
            parent=self,
        )
        h.addWidget(self._bot_chip)

        self._username_lbl = QLabel("")
        self._username_lbl.setProperty("role", "subtitle")
        h.addWidget(self._username_lbl, 1)

        manage_btn = QPushButton("Manage bots")
        manage_btn.setProperty("role", "ghost")
        manage_btn.clicked.connect(self._on_manage_bots)
        h.addWidget(manage_btn)
        return h

    def _build_compose_page(self, body_markdown: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Splitter: body on the left (dominant), targets on the right.
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # Left: body editor + meta.
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        msg_lbl = QLabel("Message")
        msg_lbl.setProperty("role", "section")
        left_layout.addWidget(msg_lbl)

        self._body_edit = QTextEdit()
        self._body_edit.setAcceptRichText(False)
        # Force a proportional font; the theme's monospace selector
        # only kicks in when ``font-role`` is set to ``code``.
        font = QFont()
        font.setPointSize(14)
        self._body_edit.setFont(font)
        self._body_edit.setPlainText(body_markdown)
        self._body_edit.textChanged.connect(self._update_send_button)
        self._body_edit.textChanged.connect(self._update_body_meta)
        self._body_edit.setPlaceholderText(
            "Write your update… Markdown is supported. "
            "Drop the first ![image](url) to attach a photo."
        )
        left_layout.addWidget(self._body_edit, 1)

        self._body_meta_lbl = QLabel("")
        self._body_meta_lbl.setProperty("role", "subtitle")
        left_layout.addWidget(self._body_meta_lbl)

        splitter.addWidget(left)

        # Right: chats.
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        to_row = QHBoxLayout()
        to_lbl = QLabel("To")
        to_lbl.setProperty("role", "section")
        to_row.addWidget(to_lbl)
        to_row.addStretch(1)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setProperty("role", "ghost")
        self._refresh_btn.clicked.connect(self._on_refresh)
        to_row.addWidget(self._refresh_btn)
        right_layout.addLayout(to_row)

        self._chats_panel = ChatsPanel()
        self._chats_panel.selection_changed.connect(self._update_send_button)
        self._chats_panel.add_chat_requested.connect(self._on_add_chat)
        self._chats_panel.star_toggled.connect(self._on_star_toggled)
        right_layout.addWidget(self._chats_panel, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([560, 360])
        layout.addWidget(splitter, 1)

        # Send mode (radio: Now / Schedule).
        self._schedule = ScheduleWidget()
        self._schedule.mode_changed.connect(self._update_send_button)
        layout.addWidget(self._schedule)

        # Options as inline checkboxes.
        opts_row = QHBoxLayout()
        opts_row.setSpacing(14)
        opts_lbl = QLabel("Options")
        opts_lbl.setProperty("role", "section")
        opts_row.addWidget(opts_lbl)
        ui = self._settings.ui
        self._opt_silent = QCheckBox("Silent")
        self._opt_silent.setToolTip("Send without a notification sound.")
        self._opt_silent.setChecked(ui.silent)
        self._opt_no_preview = QCheckBox("No link preview")
        self._opt_no_preview.setToolTip(
            "Suppress the link preview card that Telegram auto-generates."
        )
        self._opt_no_preview.setChecked(ui.disable_link_preview)
        self._opt_protect = QCheckBox("Protect content")
        self._opt_protect.setToolTip(
            "Prevent recipients from forwarding or saving this message."
        )
        self._opt_protect.setChecked(ui.protect_content)
        self._opt_pin = QCheckBox("Pin after sending")
        self._opt_pin.setToolTip(
            "Pin the message to the top of every target chat."
        )
        self._opt_pin.setChecked(ui.pin_after_sending)
        for cb in (self._opt_silent, self._opt_no_preview,
                   self._opt_protect, self._opt_pin):
            opts_row.addWidget(cb)
        opts_row.addStretch(1)
        layout.addLayout(opts_row)

        # Status line + bottom buttons.
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        bottom.addWidget(self._status_lbl, 1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        bottom.addWidget(cancel_btn)
        self._send_btn = QPushButton("Send")
        self._send_btn.setProperty("role", "primary")
        self._send_btn.setMinimumWidth(160)
        self._send_btn.clicked.connect(self._on_send)
        bottom.addWidget(self._send_btn)
        layout.addLayout(bottom)

        self._update_body_meta()
        return page

    # -- bot selection --------------------------------------------- #

    def _bot_entries(self) -> list:
        return [
            (b.id, b.display_name, b.color,
             f"@{b.telegram_username}" if b.telegram_username else "")
            for b in self._settings.bots
        ]

    def _pick_initial_bot(self) -> None:
        default = self._settings.default_bot
        if default is None:
            return
        self._switch_to_bot(default.id)

    def _switch_to_bot(self, bot_id: str) -> None:
        bot = self._settings.bot_by_id(bot_id)
        if bot is None:
            return
        self._active_bot_id = bot.id
        self._bot_chip.set_active(
            bot_id=bot.id,
            display_name=bot.display_name,
            color_hex=bot.color,
            subtitle=f"@{bot.telegram_username}" if bot.telegram_username else "",
        )
        self._username_lbl.setText(
            f"@{bot.telegram_username}" if bot.telegram_username else "(no @username)"
        )
        self._chats_panel.set_bot(bot)
        self._update_send_button()
        if bot.invalid:
            self._set_status(
                "This bot's token is invalid. Open Manage bots to replace it.",
                "error",
            )
        else:
            self._set_status("", "info")

    def _on_manage_bots(self) -> None:
        dlg = BotsDialog(self._publisher, parent=self)
        dlg.bots_changed.connect(self._on_bots_changed)
        dlg.exec()

    def _on_add_bot(self) -> None:
        dlg = AddBotDialog(self._publisher, parent=self)
        dlg.added.connect(self._on_bot_added)
        dlg.exec()

    def _on_bot_added(self, bot_id: str) -> None:
        self._switch_to_bot(bot_id)
        self._set_status(
            "Bot added. Click Refresh once you've added the bot to a chat, "
            "or use Add chat to enter a chat id manually.",
            "info",
        )

    def _on_bots_changed(self) -> None:
        if self._settings.bot_by_id(self._active_bot_id) is None:
            self._pick_initial_bot()
        else:
            self._switch_to_bot(self._active_bot_id)

    # -- chats ----------------------------------------------------- #

    def _on_refresh(self) -> None:
        api = self._publisher.bots.api_for(self._active_bot_id)
        if api is None:
            return
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText("Refreshing…")
        self._set_status("Looking for new chats…", "info")
        self._discovery = ChatDiscovery(
            api=api, settings=self._settings,
            bot_id=self._active_bot_id, parent=self,
        )
        self._discovery.done.connect(self._on_refresh_done)
        self._discovery.failed.connect(self._on_refresh_failed)
        self._discovery.start()

    def _on_refresh_done(self, new_chats: int, new_topics: int) -> None:
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("Refresh")
        bot = self._settings.bot_by_id(self._active_bot_id)
        if bot is not None:
            self._chats_panel.set_bot(bot)
        bits = []
        if new_chats:
            bits.append(f"{new_chats} new chat" + ("s" if new_chats != 1 else ""))
        if new_topics:
            bits.append(f"{new_topics} new topic" + ("s" if new_topics != 1 else ""))
        self._set_status(
            "Refreshed - " + (", ".join(bits) if bits else "no changes."),
            "ok" if bits else "info",
        )
        self._update_send_button()

    def _on_refresh_failed(self, err) -> None:
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("Refresh")
        if isinstance(err, WebhookActive):
            confirm = QMessageBox.question(
                self, "Webhook is set",
                "This bot has a webhook configured (probably by another tool). "
                "Delete it so this editor can poll for updates?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if confirm == QMessageBox.Yes and self._discovery is not None:
                self._set_status("Removing webhook…", "info")
                self._discovery.delete_webhook()
            else:
                self._set_status("Refresh skipped: webhook is still set.", "warn")
            return
        self._set_status(getattr(err, "user_message", str(err)), "error")

    def _on_add_chat(self) -> None:
        api = self._publisher.bots.api_for(self._active_bot_id)
        if api is None:
            return
        dlg = AddChatDialog(
            api=api, settings=self._settings,
            bot_id=self._active_bot_id, parent=self,
        )
        dlg.added.connect(lambda _cid: self._chats_panel.set_bot(
            self._settings.bot_by_id(self._active_bot_id)
        ))
        dlg.exec()

    def _on_star_toggled(self, chat_id: int, starred: bool) -> None:
        self._settings.set_chat_starred(self._active_bot_id, chat_id, starred)

    # -- send ------------------------------------------------------ #

    def _options(self) -> SendOptions:
        return SendOptions(
            silent=self._opt_silent.isChecked(),
            disable_link_preview=self._opt_no_preview.isChecked(),
            protect_content=self._opt_protect.isChecked(),
            pin_after_sending=self._opt_pin.isChecked(),
        )

    def _persist_options_as_default(self) -> None:
        self._settings.set_ui(
            silent=self._opt_silent.isChecked(),
            disable_link_preview=self._opt_no_preview.isChecked(),
            protect_content=self._opt_protect.isChecked(),
            pin_after_sending=self._opt_pin.isChecked(),
        )

    def _on_send(self) -> None:
        targets = self._chats_panel.picked_targets()
        if not targets:
            self._set_status("Pick at least one chat.", "warn")
            return
        if self._active_bot_id == "":
            self._set_status("Pick a bot first.", "warn")
            return
        body = self._body_edit.toPlainText().strip()
        if not body:
            self._set_status("Write a message first.", "warn")
            return

        self._persist_options_as_default()

        if self._schedule.mode() == MODE_SCHEDULE:
            self._submit_scheduled(targets, body)
        else:
            self._submit_now(targets, body)

    def _submit_now(self, targets: List[Target], body: str) -> None:
        api = self._publisher.bots.api_for(self._active_bot_id)
        if api is None:
            self._set_status("Active bot is not available.", "error")
            return
        self._send_btn.setEnabled(False)
        self._set_status("Sending…", "info")
        self._job = TelegramPublishJob(
            api=api, settings=self._settings,
            bot_id=self._active_bot_id,
            body_markdown=body, targets=targets,
            options=self._options(),
            file_ids=self._publisher.file_ids,
            parent=self,
        )
        self._job.status_changed.connect(lambda t: self._set_status(t, "info"))
        self._job.completed.connect(self._on_job_completed)
        self._job.failed.connect(self._on_job_failed)
        self._job.start()

    def _submit_scheduled(self, targets: List[Target], body: str) -> None:
        when = self._schedule.scheduled_for_unix()
        if when <= int(time.time()):
            self._set_status("Pick a time in the future.", "warn")
            return
        self._publisher.queue.add(
            bot_id=self._active_bot_id,
            targets=[
                ScheduledTarget(chat_id=t.chat.id, topic_id=t.topic_id)
                for t in targets
            ],
            body_markdown=body,
            scheduled_for=when,
            silent=self._opt_silent.isChecked(),
            disable_link_preview=self._opt_no_preview.isChecked(),
            protect_content=self._opt_protect.isChecked(),
            pin_after_sending=self._opt_pin.isChecked(),
        )
        self._publisher.scheduler.kick()
        n = len(targets)
        when_human = time.strftime("%a %d %b %H:%M", time.localtime(when))
        QMessageBox.information(
            self, "Scheduled",
            f"Will send to {n} chat" + ("s" if n != 1 else "")
            + f" at {when_human}.\n\n"
            "Your editor must be running at that time. Posts that fire while "
            "the editor was closed will be sent (late) on the next launch.",
        )
        self.accept()

    def _on_job_completed(self, results: list) -> None:
        self._send_btn.setEnabled(True)
        bot = self._settings.bot_by_id(self._active_bot_id)
        if bot is None:
            self.accept()
            return
        self._results_view.populate(results, bot)
        self._stack.setCurrentWidget(self._results_view)
        ok_count = sum(1 for r in results if r.ok)
        total = len(results)
        if ok_count == total:
            self._set_status(f"Sent to {ok_count} chats.", "ok")
        elif ok_count == 0:
            self._set_status(f"Failed: 0 of {total} chats accepted.", "error")
        else:
            self._set_status(f"Partial: {ok_count} of {total} chats.", "warn")

    def _on_job_failed(self, reason: str) -> None:
        self._send_btn.setEnabled(True)
        self._set_status(reason, "error")

    def _show_compose(self) -> None:
        self._stack.setCurrentWidget(self._compose_page)
        self._set_status("", "info")

    def _on_pin_targets(self, chat_ids: list) -> None:
        api = self._publisher.bots.api_for(self._active_bot_id)
        if api is None:
            return
        by_chat = self._results_view.results_by_chat()
        wanted = set(int(c) for c in chat_ids)
        pinned = 0
        for cid in wanted:
            r = by_chat.get(cid)
            if r is None or not r.ok or not r.message_id:
                continue
            api.pin_chat_message(
                chat_id=r.chat_id,
                message_id=r.message_id,
                disable_notification=True,
            )
            pinned += 1
        self._set_status(
            f"Pinned in {pinned} chats." if pinned else "No pinnable targets.",
            "ok" if pinned else "warn",
        )

    def _on_remove_chat(self, chat_id: int) -> None:
        self._settings.remove_chat(self._active_bot_id, int(chat_id))
        bot = self._settings.bot_by_id(self._active_bot_id)
        if bot is not None:
            self._chats_panel.set_bot(bot)

    # -- misc ------------------------------------------------------ #

    def _update_send_button(self) -> None:
        has_target = self._chats_panel.picked_count() > 0
        has_body = bool(self._body_edit.toPlainText().strip())
        bot = self._settings.bot_by_id(self._active_bot_id)
        valid_bot = bot is not None and not bot.invalid
        n = self._chats_panel.picked_count()
        if self._schedule.mode() == MODE_SCHEDULE:
            text = (f"Schedule for {n} chat" + ("s" if n != 1 else "")) if n else "Schedule"
        else:
            text = (f"Send to {n} chat" + ("s" if n != 1 else "")) if n else "Send"
        self._send_btn.setText(text)
        self._send_btn.setEnabled(has_target and has_body and valid_bot)

    def _update_body_meta(self) -> None:
        body = self._body_edit.toPlainText()
        try:
            formatted = format_for_send(body, auto_split=self._settings.ui.auto_split)
        except Exception:  # noqa: BLE001
            self._body_meta_lbl.setText("")
            return
        if not formatted.chunks:
            self._body_meta_lbl.setText("")
            return
        n = len(formatted.chunks)
        chars = sum(len(c) for c in formatted.chunks)
        photo_note = "  ·  +photo" if formatted.photo else ""
        self._body_meta_lbl.setText(
            f"{n} message" + ("s" if n != 1 else "")
            + f"  ·  {chars} chars (limit {TEXT_LIMIT}/msg){photo_note}"
        )

    def _set_status(self, text: str, kind: str) -> None:
        self._status_lbl.setText(text)
        self._status_lbl.setProperty("status", kind)
        self._status_lbl.style().unpolish(self._status_lbl)
        self._status_lbl.style().polish(self._status_lbl)

    def _setup_shortcuts(self) -> None:
        # QKeySequence("Ctrl+Return") auto-maps to Cmd+Return on macOS.
        sc = QShortcut(QKeySequence("Ctrl+Return"), self)
        sc.activated.connect(self._on_send)
        sc_bot = QShortcut(QKeySequence("Ctrl+B"), self)
        sc_bot.activated.connect(self._bot_chip.showMenu)
