"""Scheduled-posts view.

Lists every queue entry across bots as a scrollable list of cards.
Each card shows: status pill, when (local time), bot, target count,
body preview, attempt/error info. Inline actions per card:
Cancel, Reschedule…, Delete.

Reschedule opens a small dedicated dialog with a real QDateTimeEdit,
not a typed-string input.

The list refreshes automatically when the scheduler emits ``event``.
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QDateTime, Qt, Signal
from PySide6.QtWidgets import (
    QDateTimeEdit,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..queue import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_ORPHANED,
    STATUS_PARTIAL,
    STATUS_PENDING,
    STATUS_SENDING,
    STATUS_SENT,
    ScheduledPost,
)
from .theme import apply, make_card, palette_for


_STATUS_COLORS = {
    STATUS_PENDING:   "#4F86C6",
    STATUS_SENDING:   "#FFB347",
    STATUS_SENT:      "#73C991",
    STATUS_PARTIAL:   "#FFB347",
    STATUS_FAILED:    "#F48771",
    STATUS_CANCELLED: "#888888",
    STATUS_ORPHANED:  "#888888",
}


class QueueDialog(QDialog):
    def __init__(self, publisher, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._publisher = publisher
        self._queue = publisher.queue
        self._settings = publisher.settings

        self.setWindowTitle("Telegram - scheduled posts")
        self.setModal(True)
        self.setMinimumSize(720, 540)

        self._build_ui()
        apply(self)
        self._reload()

        publisher.scheduler.event.connect(self._on_scheduler_event)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(12)

        title = QLabel("Scheduled posts")
        title.setProperty("role", "title")
        root.addWidget(title)

        subtitle = QLabel(
            "These posts will fire from your local editor at their scheduled "
            "time. Telegram has no server-side scheduling for bots, so the "
            "editor needs to be running."
        )
        subtitle.setProperty("role", "subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._inner = QWidget()
        self._inner_layout = QVBoxLayout(self._inner)
        self._inner_layout.setContentsMargins(0, 4, 0, 4)
        self._inner_layout.setSpacing(8)
        self._scroll.setWidget(self._inner)
        root.addWidget(self._scroll, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    # -- list management ------------------------------------------ #

    def _reload(self) -> None:
        while self._inner_layout.count():
            it = self._inner_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        posts = sorted(self._queue.all(), key=lambda p: p.scheduled_for)
        if not posts:
            self._inner_layout.addWidget(self._build_empty_state())
            self._inner_layout.addStretch(1)
            return
        for p in posts:
            self._inner_layout.addWidget(self._build_card(p))
        self._inner_layout.addStretch(1)

    def _build_empty_state(self) -> QWidget:
        card = make_card(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 28, 24, 28)
        layout.setSpacing(6)
        title = QLabel("No scheduled posts")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(title)
        body = QLabel(
            "Schedule a post from the Publish to Telegram dialog, "
            "and it will appear here."
        )
        body.setProperty("role", "subtitle")
        body.setAlignment(Qt.AlignCenter)
        body.setWordWrap(True)
        layout.addWidget(body)
        return card

    def _build_card(self, p: ScheduledPost) -> QWidget:
        card = make_card(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        # Status pill on the left.
        color = _STATUS_COLORS.get(p.status, "#888888")
        pill = QLabel(p.status.upper())
        pill.setFixedWidth(80)
        pill.setAlignment(Qt.AlignCenter)
        pill.setStyleSheet(
            f"color: white; background: {color}; "
            "border-radius: 10px; padding: 4px 8px; "
            "font-size: 10px; font-weight: 700;"
        )
        layout.addWidget(pill, 0, Qt.AlignTop)

        # Content column.
        col = QVBoxLayout()
        col.setSpacing(2)

        bot = self._settings.bot_by_id(p.bot_id)
        bot_label = bot.display_name if bot else "(removed bot)"
        when = time.strftime("%a %d %b %Y · %H:%M", time.localtime(p.scheduled_for))
        head = QLabel(f"<b>{when}</b>  ·  {bot_label}  ·  "
                      f"{len(p.targets)} chat" + ("s" if len(p.targets) != 1 else ""))
        col.addWidget(head)

        preview = p.body_markdown.replace("\n", " ")[:120]
        if len(p.body_markdown) > 120:
            preview += "…"
        body_lbl = QLabel(preview)
        body_lbl.setProperty("role", "subtitle")
        body_lbl.setWordWrap(True)
        col.addWidget(body_lbl)

        meta_bits = []
        if p.attempts:
            meta_bits.append(f"{p.attempts} attempt" + ("s" if p.attempts != 1 else ""))
        if p.last_error:
            meta_bits.append("last error: " + p.last_error[:80])
        if p.status == STATUS_PARTIAL:
            ok = sum(1 for d in p.delivered if d.ok)
            meta_bits.append(f"{ok}/{len(p.targets)} sent")
        if meta_bits:
            meta = QLabel("  ·  ".join(meta_bits))
            meta.setProperty("role", "subtitle")
            meta.setWordWrap(True)
            col.addWidget(meta)

        layout.addLayout(col, 1)

        # Right-hand actions.
        actions = QHBoxLayout()
        actions.setSpacing(4)
        if p.status in (STATUS_PENDING, STATUS_SENDING):
            cancel_btn = QPushButton("Cancel")
            cancel_btn.clicked.connect(lambda _c=False, pid=p.id: self._cancel(pid))
            actions.addWidget(cancel_btn)
        if p.status in (STATUS_PENDING, STATUS_FAILED):
            resched_btn = QPushButton("Reschedule…")
            resched_btn.clicked.connect(lambda _c=False, post=p: self._reschedule(post))
            actions.addWidget(resched_btn)
        if p.status in (STATUS_SENT, STATUS_PARTIAL, STATUS_FAILED,
                        STATUS_CANCELLED, STATUS_ORPHANED):
            del_btn = QPushButton("Delete")
            del_btn.setProperty("role", "ghost")
            del_btn.clicked.connect(lambda _c=False, pid=p.id: self._delete(pid))
            actions.addWidget(del_btn)
        layout.addLayout(actions)

        return card

    # -- actions --------------------------------------------------- #

    def _cancel(self, post_id: str) -> None:
        self._queue.cancel(post_id)
        self._publisher.scheduler.kick()
        self._reload()

    def _reschedule(self, post: ScheduledPost) -> None:
        dlg = _ReschedulePicker(post, parent=self)
        if dlg.exec() == QDialog.Accepted:
            when = dlg.unix_seconds()
            if when <= int(time.time()):
                QMessageBox.warning(self, "Reschedule", "Pick a time in the future.")
                return
            self._queue.reschedule(post.id, when)
            self._publisher.scheduler.kick()
            self._reload()

    def _delete(self, post_id: str) -> None:
        confirm = QMessageBox.question(
            self, "Delete entry",
            "Remove this entry from the local history?\n\n"
            "Sent messages stay in their Telegram chats.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._queue.delete(post_id)
        self._reload()

    def _on_scheduler_event(self, _kind: str, _post_id: str, _note: str) -> None:
        if self.isVisible():
            self._reload()


class _ReschedulePicker(QDialog):
    """Compact date+time picker dialog."""

    def __init__(self, post: ScheduledPost, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Reschedule post")
        self.setModal(True)
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        title = QLabel("Pick a new time")
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(title)

        body_preview = post.body_markdown.replace("\n", " ")[:80]
        if body_preview:
            preview = QLabel(f"\"{body_preview}{'…' if len(post.body_markdown) > 80 else ''}\"")
            preview.setProperty("role", "subtitle")
            preview.setWordWrap(True)
            layout.addWidget(preview)

        self._dt = QDateTimeEdit()
        self._dt.setCalendarPopup(True)
        self._dt.setDisplayFormat("yyyy-MM-dd  HH:mm")
        now_plus_min = QDateTime.currentDateTime().addSecs(60)
        self._dt.setMinimumDateTime(now_plus_min)
        self._dt.setMaximumDateTime(QDateTime.currentDateTime().addDays(365))
        suggested = QDateTime.currentDateTime().addSecs(
            max(300, post.scheduled_for - int(time.time()))
        )
        self._dt.setDateTime(suggested)
        layout.addWidget(self._dt)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        save = QPushButton("Save")
        save.setProperty("role", "primary")
        save.clicked.connect(self.accept)
        actions.addWidget(save)
        layout.addLayout(actions)
        apply(self)

    def unix_seconds(self) -> int:
        return int(self._dt.dateTime().toSecsSinceEpoch())
