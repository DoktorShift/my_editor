"""One review row inside the social panel.

Renders a single ``CommentNode`` (or the leaf attached to a rating)
with avatar, display name, NIP-05 badge, stars, body, relative
time, and the reply / zap / mute / edit / delete affordances
appropriate to the viewer.

Pattern reference: a hybrid of the App Store review card (avatar +
stars + name on one line) and the GitHub Discussions comment
(content body + reply link below).

Most of the heavy lifting (state, lifecycle) is in the parent
``SocialPanel``. This widget is a pure presentational unit: it
takes the data it should show and exposes signals for user actions.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..social.models import CommentNode
from .icons import plugin_pixmap   # reused for generated-avatar fallback
from .stats import RatingStars, format_relative_time


class ReviewCard(QWidget):
    """One reviewer's avatar + name + stars + body + actions."""

    reply_clicked = Signal(str)        # parent comment event id
    delete_clicked = Signal(str)       # event id of the user's own comment
    mute_clicked = Signal(str)         # author pubkey
    unmute_clicked = Signal(str)       # author pubkey
    zap_clicked = Signal(str)          # author pubkey
    avatar_clicked = Signal(str)       # author pubkey - opens profile modal

    def __init__(
        self,
        *,
        node: CommentNode,
        author_display: str,
        author_picture_url: str,
        avatar_pixmap: Optional[QPixmap] = None,
        nip05_identifier: str,
        nip05_verified: Optional[bool],
        is_plugin_author: bool,
        is_own: bool,
        is_muted: bool = False,
        is_signed_in: bool = False,
        stars: Optional[int] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._node = node
        self._pubkey = node.comment.author_pubkey
        self.setObjectName("review-card")
        self.setStyleSheet(
            "#review-card {"
            "  background: rgba(255,255,255,0.03);"
            "  border-radius: 8px;"
            "}"
            "#review-card[muted=\"true\"] {"
            "  background: rgba(255,255,255,0.015);"
            "}"
        )
        if is_muted:
            self.setProperty("muted", "true")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(12)

        avatar_widget = _ClickableAvatar(
            pubkey=self._pubkey,
            pixmap=_avatar_pixmap(
                picture_url=author_picture_url,
                pixmap=avatar_pixmap,
                fallback_seed=self._pubkey,
                fallback_name=author_display,
                size=36,
            ),
            size=36,
            tooltip=f"View {author_display or _short_pubkey(self._pubkey)}'s profile",
        )
        avatar_widget.clicked.connect(
            lambda pk=self._pubkey: self.avatar_clicked.emit(pk)
        )
        outer.addWidget(avatar_widget, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)

        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        name_label = QLabel(f"<b>{_escape(author_display or _short_pubkey(self._pubkey))}</b>")
        name_label.setStyleSheet("font-size: 13px;")
        header_row.addWidget(name_label)

        # NIP-05 badge: only render when verification succeeded.
        # Pending or failed verifications stay invisible so we never
        # display a negative signal that the user can't act on.
        if nip05_verified is True:
            header_row.addWidget(_verified_badge(nip05_identifier))

        if is_plugin_author:
            header_row.addWidget(_author_badge())
        if is_own:
            header_row.addWidget(_own_badge())
        if is_muted:
            header_row.addWidget(_muted_badge())

        if stars is not None:
            header_row.addWidget(RatingStars(rating=float(stars), size=12))

        header_row.addStretch(1)

        relative = format_relative_time(_iso_from_unix(node.comment.created_at))
        if relative:
            time_label = QLabel(f"<span style='color:#888'>{_escape(relative)}</span>")
            time_label.setToolTip(_iso_from_unix(node.comment.created_at))
            header_row.addWidget(time_label)

        text_col.addLayout(header_row)

        # Muted comments collapse their body to a single line. The
        # user can still un-mute and see the full content. This keeps
        # the thread structure visible without forcing them to scroll
        # through content they explicitly hid.
        body_text = (
            "(hidden — you muted this author)" if is_muted
            else node.comment.content
        )
        body_label = QLabel(_escape(body_text))
        body_label.setWordWrap(not is_muted)
        body_label.setStyleSheet(
            "color: #ddd; font-size: 13px;" if not is_muted
            else "color: #777; font-size: 12px; font-style: italic;"
        )
        body_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        text_col.addWidget(body_label)

        actions_row = QHBoxLayout()
        actions_row.setSpacing(12)
        actions_row.setContentsMargins(0, 2, 0, 0)

        # Reply / Zap surface only for non-muted cards (muting hides
        # the author; engaging with them would be incoherent UX).
        if not is_muted:
            reply_btn = _link_button("Reply")
            reply_btn.clicked.connect(
                lambda *_a: self.reply_clicked.emit(node.comment.event_id)
            )
            actions_row.addWidget(reply_btn)

            if is_signed_in and not is_own:
                zap_btn = _link_button("⚡ Zap")
                zap_btn.clicked.connect(
                    lambda *_a, pk=self._pubkey: self.zap_clicked.emit(pk)
                )
                actions_row.addWidget(zap_btn)

        if is_own:
            delete_btn = _link_button("Delete")
            delete_btn.clicked.connect(
                lambda *_a: self.delete_clicked.emit(node.comment.event_id)
            )
            actions_row.addWidget(delete_btn)

        # Mute/unmute lives on the right so it's reachable but not in
        # the way. Signed-in only; muting requires a NIP-51 publish.
        if is_signed_in and not is_own:
            if is_muted:
                unmute_btn = _link_button("Unmute")
                unmute_btn.clicked.connect(
                    lambda *_a, pk=self._pubkey: self.unmute_clicked.emit(pk)
                )
                actions_row.addWidget(unmute_btn)
            else:
                mute_btn = _link_button("Mute")
                mute_btn.setToolTip(
                    "Mute this author. Publishes an updated NIP-51 list to your relays."
                )
                mute_btn.clicked.connect(
                    lambda *_a, pk=self._pubkey: self.mute_clicked.emit(pk)
                )
                actions_row.addWidget(mute_btn)

        actions_row.addStretch(1)
        text_col.addLayout(actions_row)

        outer.addLayout(text_col, 1)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _avatar_pixmap(
    *,
    picture_url: str,
    pixmap: Optional[QPixmap],
    fallback_seed: str,
    fallback_name: str,
    size: int,
) -> QPixmap:
    """Pick the best available avatar pixmap and present it at ``size``.

    Order of preference:
      1. Real downloaded pixmap (from the editor's AvatarStore), scaled
         to the requested square size and clipped to a rounded square
         so it matches the deterministic letter-avatar shape.
      2. Generated deterministic placeholder based on the pubkey.

    The picture_url is unused at render time. The social panel
    pre-resolves it through ``AvatarBatchLoader`` and hands us the
    finished pixmap; we keep the parameter for future inline-loading.
    """
    del picture_url  # signature parity with future inline loading
    if pixmap is not None and not pixmap.isNull():
        return _round_to_size(pixmap, size)
    return plugin_pixmap(
        plugin_id=fallback_seed,
        name=fallback_name or fallback_seed,
        icon_path="",
        size=size,
    )


def _round_to_size(source: QPixmap, size: int) -> QPixmap:
    """Scale ``source`` to ``size`` square and clip to a rounded square.

    AvatarLoader returns the raw image (often 256x256 or larger). Drawing
    it into a fixed 36x36 QLabel without scaling crops the top-left
    corner of the image, which for most photos is a dark area — that's
    the cause of the black-square reviewer avatars. Scaling and clipping
    here means every avatar surface uses the exact same shape language.
    """
    target = QPixmap(size, size)
    target.fill(Qt.transparent)

    painter = QPainter(target)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)

    radius = max(4, size // 5)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, radius, radius)
    painter.setClipPath(path)

    scaled = source.scaled(
        size, size,
        Qt.KeepAspectRatioByExpanding,
        Qt.SmoothTransformation,
    )
    # Centre-crop when the aspect ratio of the source doesn't match
    # 1:1 (KeepAspectRatioByExpanding overshoots one axis).
    x = (size - scaled.width()) // 2
    y = (size - scaled.height()) // 2
    painter.drawPixmap(x, y, scaled)
    painter.end()
    return target


class _ClickableAvatar(QLabel):
    """A QLabel that emits ``clicked(pubkey)`` on a primary mouse release.

    Plain-QLabel subclass rather than a QPushButton because we want
    pixel-accurate control over the painted bitmap and no platform
    button chrome (the rendering is identical on macOS, Windows and
    Linux this way). The pointing-hand cursor is the cross-platform
    affordance for "this is interactive."
    """

    clicked = Signal(str)

    def __init__(
        self,
        *,
        pubkey: str,
        pixmap: QPixmap,
        size: int,
        tooltip: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._pubkey = pubkey
        self.setFixedSize(size, size)
        self.setPixmap(pixmap)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        # Keyboard focus + Enter/Return activation so the modal is
        # reachable for keyboard-only users on every platform.
        self.setFocusPolicy(Qt.StrongFocus)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit(self._pubkey)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit(self._pubkey)
            event.accept()
            return
        super().keyPressEvent(event)


def _verified_badge(identifier: str) -> QLabel:
    badge = QLabel("✓")
    badge.setToolTip(f"NIP-05 verified: {identifier}")
    badge.setStyleSheet(
        "QLabel {"
        "  color: white;"
        "  background-color: #2d7fff;"
        "  border-radius: 7px;"
        "  padding: 0 5px;"
        "  font-size: 10px;"
        "  font-weight: 700;"
        "}"
    )
    return badge


def _author_badge() -> QLabel:
    badge = QLabel("AUTHOR")
    badge.setStyleSheet(
        "QLabel {"
        "  color: #b8860b;"
        "  background-color: rgba(245,180,0,0.12);"
        "  border: 1px solid rgba(245,180,0,0.55);"
        "  border-radius: 4px;"
        "  padding: 0 5px;"
        "  font-size: 9px;"
        "  font-weight: 700;"
        "}"
    )
    badge.setToolTip("This person published the plugin.")
    return badge


def _own_badge() -> QLabel:
    badge = QLabel("YOU")
    badge.setStyleSheet(
        "QLabel {"
        "  color: #3aa14a;"
        "  background-color: rgba(58,161,74,0.12);"
        "  border: 1px solid rgba(58,161,74,0.55);"
        "  border-radius: 4px;"
        "  padding: 0 5px;"
        "  font-size: 9px;"
        "  font-weight: 700;"
        "}"
    )
    return badge


def _muted_badge() -> QLabel:
    badge = QLabel("MUTED")
    badge.setStyleSheet(
        "QLabel {"
        "  color: #aaa;"
        "  background-color: rgba(255,255,255,0.06);"
        "  border-radius: 4px;"
        "  padding: 0 5px;"
        "  font-size: 9px;"
        "  font-weight: 700;"
        "}"
    )
    badge.setToolTip("You muted this account via NIP-51.")
    return badge


def _link_button(label: str) -> QPushButton:
    btn = QPushButton(label)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFlat(True)
    btn.setAutoDefault(False)
    # Note: Qt Style Sheets don't support ``text-decoration``; we use
    # a color shift on hover instead, which is the same affordance
    # without the unsupported declaration.
    btn.setStyleSheet(
        "QPushButton {"
        "  color: #2d7fff;"
        "  background: transparent;"
        "  border: none;"
        "  padding: 0;"
        "  font-size: 12px;"
        "}"
        "QPushButton:hover { color: #5fa0ff; }"
    )
    return btn


def _short_pubkey(pubkey: str) -> str:
    """Compact form when we don't have a display name yet."""
    if len(pubkey) < 16:
        return pubkey
    return f"{pubkey[:6]}…{pubkey[-4:]}"


def _iso_from_unix(seconds: int) -> str:
    """Convert a unix timestamp to ISO 8601 so the time formatter
    inside ``stats.format_relative_time`` can handle it."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )
