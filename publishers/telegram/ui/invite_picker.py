"""Invite-link helpers, grouped by destination type.

Categories (groups, channels, DM) are tabs at the top of the dialog
so the surface is calm even when all five links are present. Each
link card has a generous QR, a description that explains the
*outcome* (what happens when someone follows the link), and big copy
+ open buttons.

Special note on DMs: the bot cannot DM strangers - the recipient has
to send ``/start`` first. The DM tab spells this out so users don't
expect magic.
"""

from __future__ import annotations

import io
from typing import List, Optional

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..invites import InviteLink, all_invite_links
from .theme import apply, make_card, palette_for


class InvitePicker(QDialog):
    def __init__(self, bot_username: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._username = bot_username
        self.setWindowTitle(f"@{bot_username}  ·  Invite links")
        self.setModal(True)
        self.setMinimumSize(620, 540)

        self._build_ui()
        apply(self)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(12)

        title = QLabel(f"Add @{self._username} to a chat")
        title.setProperty("role", "title")
        root.addWidget(title)

        subtitle = QLabel(
            "Copy a link or scan the QR with your phone. Telegram opens "
            "the right picker for the kind of chat you choose."
        )
        subtitle.setProperty("role", "subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        links = all_invite_links(self._username)
        for category, label, lead in (
            ("group", "Groups", None),
            ("channel", "Channels", None),
            ("dm", "Direct messages",
             "Bots cannot DM strangers. The recipient must open this link "
             "and send <b>/start</b> first - then they appear in the chat "
             "picker and you can send them DMs."),
        ):
            tabs.addTab(
                self._build_tab(
                    [link for link in links if link.category == category],
                    lead=lead,
                ),
                label,
            )

        root.addWidget(tabs, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        actions.addWidget(close_btn)
        root.addLayout(actions)

    def _build_tab(self, links: List[InviteLink], *, lead: Optional[str]) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 12, 2, 4)
        layout.setSpacing(10)

        if lead:
            lead_lbl = QLabel(lead)
            lead_lbl.setProperty("role", "subtitle")
            lead_lbl.setWordWrap(True)
            layout.addWidget(lead_lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(2, 4, 2, 4)
        inner_layout.setSpacing(10)
        for link in links:
            inner_layout.addWidget(_LinkCard(link, parent=self))
        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)
        return page


class _LinkCard(QWidget):
    def __init__(self, link: InviteLink, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._url = link.url

        card = make_card(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        layout = QHBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(14)

        qr = QLabel()
        qr.setFixedSize(96, 96)
        qr.setAlignment(Qt.AlignCenter)
        pix = _render_qr_pixmap(link.url, 96)
        if pix is not None:
            qr.setPixmap(pix)
        else:
            qr.setText("QR\nN/A")
        layout.addWidget(qr)

        col = QVBoxLayout()
        col.setSpacing(4)
        title = QLabel(link.label)
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        col.addWidget(title)
        desc = QLabel(link.description)
        desc.setWordWrap(True)
        desc.setProperty("role", "subtitle")
        col.addWidget(desc)
        url_lbl = QLabel(f"<span style='font-family:monospace'>{link.url}</span>")
        url_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        url_lbl.setWordWrap(True)
        col.addWidget(url_lbl)
        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        copy_btn = QPushButton("Copy link")
        copy_btn.clicked.connect(self._on_copy)
        copy_btn.clicked.connect(lambda: copy_btn.setText("Copied ✓"))
        open_btn = QPushButton("Open in Telegram")
        open_btn.setProperty("role", "primary")
        open_btn.clicked.connect(self._on_open)
        action_row.addWidget(copy_btn)
        action_row.addWidget(open_btn)
        action_row.addStretch(1)
        col.addLayout(action_row)
        layout.addLayout(col, 1)

    def _on_copy(self) -> None:
        QGuiApplication.clipboard().setText(self._url)

    def _on_open(self) -> None:
        QDesktopServices.openUrl(QUrl(self._url))


def _render_qr_pixmap(text: str, size_px: int) -> Optional[QPixmap]:
    try:
        import segno
    except ImportError:
        return None
    try:
        qr = segno.make(text, error="m")
        buf = io.BytesIO()
        qr.save(buf, kind="png", scale=4, border=2)
        img = QImage.fromData(QByteArray(buf.getvalue()), "PNG")
        if img.isNull():
            return None
        return QPixmap.fromImage(img).scaled(
            size_px, size_px, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
    except Exception:  # noqa: BLE001
        return None
