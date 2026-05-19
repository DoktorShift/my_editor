"""Modal that opens when a reviewer's avatar is clicked.

Layout, top to bottom:

  +---------------------------------------------------------------+
  |  [Avatar]   Display name           [Follow] [Mute] [Zap*]     |
  |             ✓ alice@example.com    [View on Nostr ↗]          |
  |             npub1xy...3z   [copy]                              |
  |             3 reviews · joined Mar 2025                        |
  +---------------------------------------------------------------+
  |                                                               |
  |  Their reviews on this marketplace                            |
  |                                                               |
  |   [icon]  Plugin Name       ★★★★★  2 h ago                    |
  |           "the comment body, ellipsised if long"              |
  |                                                               |
  |   [icon]  Another Plugin    ★★★★☆  3 d ago                    |
  |           "another comment..."                                 |
  |                                                               |
  +---------------------------------------------------------------+
  |                                              [Close]          |
  +---------------------------------------------------------------+

Cross-platform notes:
  * No platform-specific font families or sizes are declared. Qt
    selects the system default, which produces native-looking text
    on macOS / Linux / Windows alike.
  * ``Qt.Dialog`` + ``setModal(True)`` is the cross-platform pattern;
    we don't reach for frameless or native sheets so the dialog
    behaves identically on every desktop.
  * Avatar rendering and rounded clipping use plain QPainter; no
    style-sheet ``border-radius`` workaround that only paints on
    some Qt builds.
  * "View on Nostr" routes through ``QDesktopServices.openUrl``
    (NSWorkspace on macOS, xdg-open on Linux, ShellExecute on
    Windows) so a registered ``nostr:`` handler always wins; we fall
    back to njump.me's HTTPS rendering when nothing is registered.

The dialog never reaches out to relays on its own. The marketplace
dialog hands it an ``AuthorReviewsFetcher`` and a ``listing_resolver``
callable (anchor -> PluginListing or None). The dialog calls
``fetcher.request(pubkey)``, listens for ``ready``, and renders
whatever lands.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..models import PluginListing
from ..social.author_reviews_fetcher import (
    AuthorReview,
    AuthorReviewSet,
    AuthorReviewsFetcher,
)
from ..social.models import PluginAnchor
from .icons import plugin_pixmap
from .review_card import _round_to_size  # avatar shape parity
from .stats import RatingStars, format_count, format_relative_time


# Type alias for the callback the dialog uses to translate the anchor
# attached to each AuthorReview into a known marketplace listing. The
# resolver returns ``None`` when the plugin isn't in the local catalog
# (uninstalled / removed); in that case we still render the row, just
# with a generic icon and the plugin id from the anchor.
ListingResolver = Callable[[PluginAnchor], Optional[PluginListing]]


class AuthorProfileDialog(QDialog):
    """Inspect one reviewer's identity and their cross-plugin reviews."""

    # The dialog never publishes mutes or follows itself: the parent
    # already owns those flows. We bubble user intent up through these
    # signals; the marketplace dialog wires them to the existing
    # publisher pipeline so behavior stays identical to the inline
    # review-card actions.
    mute_requested = Signal(str)             # pubkey
    unmute_requested = Signal(str)           # pubkey
    follow_requested = Signal(str)           # pubkey
    unfollow_requested = Signal(str)         # pubkey
    plugin_open_requested = Signal(object)   # PluginListing (or anchor when unknown)

    def __init__(
        self,
        *,
        author_pubkey: str,
        display_name: str,
        nip05_identifier: str,
        nip05_verified: Optional[bool],
        avatar_pixmap: Optional[QPixmap],
        reviews_fetcher: AuthorReviewsFetcher,
        listing_resolver: ListingResolver,
        is_self: bool,
        is_signed_in: bool,
        is_muted: bool,
        is_followed: bool,
        can_follow: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._pubkey = author_pubkey
        self._display_name = display_name or _short_pubkey(author_pubkey)
        self._nip05_identifier = nip05_identifier
        self._nip05_verified = nip05_verified
        self._avatar_pixmap = avatar_pixmap
        self._fetcher = reviews_fetcher
        self._resolve_listing = listing_resolver
        self._is_self = is_self
        self._is_signed_in = is_signed_in
        self._is_muted = is_muted
        self._is_followed = is_followed
        # ``can_follow`` is wired separately from ``is_signed_in`` because
        # follow requires a NIP-02 publisher in the editor; without one
        # the Follow button would always fail. Hiding it keeps the
        # dialog honest about what's actually available right now.
        self._can_follow = can_follow

        self.setWindowTitle(f"{self._display_name} · Nostr profile")
        self.setModal(True)
        self.setMinimumSize(QSize(560, 480))
        self.resize(640, 560)
        # No platform-specific window flags. ``Qt.Dialog`` is the
        # portable default that picks the right chrome on macOS,
        # Windows and the major Linux compositors.
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        self._build_ui()
        self._wire_fetcher()

        # Kick off the relay query. The fetcher emits ``ready``
        # synchronously when a cache hit exists, so the list may
        # already be populated by the time this returns.
        self._fetcher.request(self._pubkey)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        # Divider line painted with the same translucent white the
        # rest of the marketplace uses for section separators.
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background-color: rgba(255,255,255,0.08);")
        root.addWidget(divider)

        root.addWidget(self._build_reviews_section(), 1)
        root.addWidget(self._build_footer())

        # One global stylesheet at the dialog level so child widgets
        # don't each carry copies of the same colors. Keeps the dialog
        # visually consistent and easier to retheme later.
        self.setStyleSheet(
            "QDialog { background-color: #1f2126; }"
            "QLabel { color: #ddd; }"
        )

    # -- header --------------------------------------------------------

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("profile-header")
        header.setStyleSheet(
            "#profile-header {"
            "  background-color: rgba(255,255,255,0.03);"
            "  padding: 0;"
            "}"
        )
        # Use a horizontal layout so the avatar sits to the left of an
        # identity column, with action buttons aligned to the right.
        # This reads more naturally than a deeply nested vertical
        # stack and gives the dialog the breathing room it needs to
        # feel like a profile page rather than a tooltip.
        outer = QHBoxLayout(header)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(20)

        outer.addWidget(self._build_avatar(), 0, Qt.AlignTop)
        outer.addLayout(self._build_identity_column(), 1)
        outer.addLayout(self._build_action_column(), 0)
        return header

    def _build_avatar(self) -> QLabel:
        avatar = QLabel()
        avatar.setFixedSize(QSize(72, 72))
        if self._avatar_pixmap is not None and not self._avatar_pixmap.isNull():
            avatar.setPixmap(_round_to_size(self._avatar_pixmap, 72))
        else:
            avatar.setPixmap(plugin_pixmap(
                plugin_id=self._pubkey,
                name=self._display_name,
                icon_path="",
                size=72,
            ))
        return avatar

    def _build_identity_column(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        # Name + verified badge in one row so the badge sits inline
        # with the display name like every modern social app does.
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)

        name_label = QLabel(f"<b>{_escape(self._display_name)}</b>")
        name_label.setStyleSheet("font-size: 18px; color: #fff;")
        name_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        name_row.addWidget(name_label, 0, Qt.AlignVCenter)

        if self._nip05_verified is True and self._nip05_identifier:
            name_row.addWidget(_verified_chip(self._nip05_identifier), 0, Qt.AlignVCenter)
        if self._is_self:
            name_row.addWidget(_you_chip(), 0, Qt.AlignVCenter)
        name_row.addStretch(1)
        col.addLayout(name_row)

        # Secondary line: NIP-05 text (shown only when present, even if
        # not yet verified, because it's the user's chosen handle).
        if self._nip05_identifier:
            handle = QLabel(
                f"<span style='color:#88a4ff;'>{_escape(self._nip05_identifier)}</span>"
            )
            handle.setStyleSheet("font-size: 12px;")
            handle.setTextInteractionFlags(Qt.TextSelectableByMouse)
            col.addWidget(handle)

        # npub strip: short form + copy button. The full npub is too
        # long to read at a glance, so we display the canonical
        # short form (npub1xy…3z) and put the full string on the
        # clipboard.
        npub_row = QHBoxLayout()
        npub_row.setContentsMargins(0, 0, 0, 0)
        npub_row.setSpacing(8)
        npub = _safe_encode_npub(self._pubkey)
        short = _short_npub(npub)
        npub_label = QLabel(
            f"<span style='color:#888; font-family: monospace;'>{_escape(short)}</span>"
        )
        npub_label.setStyleSheet("font-size: 11px;")
        npub_label.setToolTip(npub)
        npub_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        npub_row.addWidget(npub_label, 0, Qt.AlignVCenter)

        copy_btn = QPushButton("Copy")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.setFlat(True)
        copy_btn.setAutoDefault(False)
        copy_btn.setStyleSheet(_LINK_BUTTON_STYLE)
        copy_btn.clicked.connect(lambda: _copy_to_clipboard(npub))
        npub_row.addWidget(copy_btn, 0, Qt.AlignVCenter)
        npub_row.addStretch(1)
        col.addLayout(npub_row)

        # Stats line — populated once the fetcher returns. Stored as
        # an instance attribute so the ready handler can update it.
        self._stats_label = QLabel("Loading reviews...")
        self._stats_label.setStyleSheet("color: #888; font-size: 12px;")
        col.addWidget(self._stats_label)

        col.addStretch(1)
        return col

    def _build_action_column(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        col.setAlignment(Qt.AlignTop)

        view_btn = QPushButton("View on Nostr ↗")
        view_btn.setCursor(Qt.PointingHandCursor)
        view_btn.setAutoDefault(False)
        view_btn.setStyleSheet(_PRIMARY_BUTTON_STYLE)
        view_btn.setToolTip(
            "Open this profile in your registered Nostr client, or "
            "njump.me when none is configured."
        )
        view_btn.clicked.connect(self._on_view_on_nostr)
        col.addWidget(view_btn)

        # Follow / mute only make sense when the viewer has a signed-in
        # profile that can publish a NIP-02 / NIP-51 update. Hiding the
        # buttons in the anonymous case keeps the action column honest
        # about what's actually possible right now. Follow is gated
        # additionally on ``can_follow`` because the editor doesn't
        # ship a NIP-02 publisher yet.
        if self._is_signed_in and not self._is_self:
            if self._can_follow:
                follow_btn = QPushButton("Unfollow" if self._is_followed else "Follow")
                follow_btn.setCursor(Qt.PointingHandCursor)
                follow_btn.setAutoDefault(False)
                follow_btn.setStyleSheet(_SECONDARY_BUTTON_STYLE)
                follow_btn.setToolTip(
                    "Add or remove this person from your NIP-02 contact list."
                )
                follow_btn.clicked.connect(self._on_follow_toggle)
                col.addWidget(follow_btn)

            mute_btn = QPushButton("Unmute" if self._is_muted else "Mute")
            mute_btn.setCursor(Qt.PointingHandCursor)
            mute_btn.setAutoDefault(False)
            mute_btn.setStyleSheet(_SECONDARY_BUTTON_STYLE)
            mute_btn.setToolTip(
                "Update your NIP-51 mute list. Muted reviews are hidden "
                "behind a placeholder across the marketplace."
            )
            mute_btn.clicked.connect(self._on_mute_toggle)
            col.addWidget(mute_btn)

        # Zap stub. Lit up visually so the affordance reads, but
        # disabled until the wallet milestone lands. The plugin's own
        # zap path (engagement panel) already supports sending sats
        # to the plugin author via the bunker + LNURL flow; cloning
        # that here without the wallet to fund it would be misleading.
        # TODO(wallet-milestone): wire this button to the same
        # ``EngagementPublisher`` zap path used by SocialPanel once a
        # real wallet plugin is connected. The recipient should be
        # this author's lightning address from their kind:0 metadata.
        zap_btn = QPushButton("⚡ Zap")
        zap_btn.setEnabled(False)
        zap_btn.setCursor(Qt.ForbiddenCursor)
        zap_btn.setAutoDefault(False)
        zap_btn.setStyleSheet(_ZAP_BUTTON_STYLE)
        zap_btn.setToolTip(
            "Sending sats to an author needs a wallet connection. "
            "Coming in a later release."
        )
        col.addWidget(zap_btn)
        return col

    # -- reviews list --------------------------------------------------

    def _build_reviews_section(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(12)

        heading = QLabel("Their reviews across the marketplace")
        heading.setStyleSheet("font-size: 13px; font-weight: 600; color: #aaa;")
        layout.addWidget(heading)

        # Scroll area absorbs any quantity of reviews without growing
        # the dialog past the user's screen. Frameless so it blends
        # into the dialog body.
        self._reviews_scroll = QScrollArea()
        self._reviews_scroll.setWidgetResizable(True)
        self._reviews_scroll.setFrameShape(QFrame.NoFrame)
        self._reviews_scroll.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding,
        )

        self._reviews_holder = QWidget()
        self._reviews_layout = QVBoxLayout(self._reviews_holder)
        self._reviews_layout.setContentsMargins(0, 0, 0, 0)
        self._reviews_layout.setSpacing(8)

        self._render_loading_state()

        self._reviews_scroll.setWidget(self._reviews_holder)
        layout.addWidget(self._reviews_scroll, 1)
        return wrap

    def _build_footer(self) -> QWidget:
        wrap = QFrame()
        wrap.setStyleSheet(
            "QFrame { border-top: 1px solid rgba(255,255,255,0.08); }"
        )
        row = QHBoxLayout(wrap)
        row.setContentsMargins(24, 12, 24, 12)
        row.setSpacing(8)
        row.addStretch(1)
        close = QPushButton("Close")
        close.setCursor(Qt.PointingHandCursor)
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        return wrap

    # ------------------------------------------------------------------
    # Wiring + dynamic content
    # ------------------------------------------------------------------

    def _wire_fetcher(self) -> None:
        self._fetcher.ready.connect(self._on_reviews_ready)
        self._fetcher.failed.connect(self._on_reviews_failed)

    def _on_reviews_ready(self, review_set: AuthorReviewSet) -> None:
        if review_set.author_pubkey != self._pubkey:
            return  # not for us
        self._render_reviews(review_set)
        self._update_stats_line(review_set)

    def _on_reviews_failed(self, pubkey: str, reason: str) -> None:
        if pubkey != self._pubkey:
            return
        self._stats_label.setText("Reviews unavailable")
        _clear_layout(self._reviews_layout)
        self._reviews_layout.addWidget(_info_panel(
            "Couldn't reach any relay.",
            "Try again later, or open the profile in your Nostr client to "
            "browse their activity directly.",
        ))
        self._reviews_layout.addStretch(1)

    def _render_loading_state(self) -> None:
        _clear_layout(self._reviews_layout)
        placeholder = QLabel("Loading reviews...")
        placeholder.setStyleSheet("color: #888; font-size: 12px;")
        placeholder.setAlignment(Qt.AlignCenter)
        self._reviews_layout.addWidget(placeholder)
        self._reviews_layout.addStretch(1)

    def _render_reviews(self, review_set: AuthorReviewSet) -> None:
        _clear_layout(self._reviews_layout)
        if not review_set.reviews:
            self._reviews_layout.addWidget(_info_panel(
                "No reviews on this marketplace yet.",
                "When this author rates or comments on a plugin, their "
                "review will show up here automatically.",
            ))
            self._reviews_layout.addStretch(1)
            return

        for review in review_set.reviews:
            listing = self._resolve_listing(review.anchor)
            self._reviews_layout.addWidget(
                _AuthorReviewRow(
                    review=review,
                    listing=listing,
                    on_open=self._emit_plugin_open,
                )
            )
        self._reviews_layout.addStretch(1)

    def _update_stats_line(self, review_set: AuthorReviewSet) -> None:
        count = len(review_set.reviews)
        if count == 0:
            self._stats_label.setText("No reviews on this marketplace yet")
            return
        with_stars = sum(1 for r in review_set.reviews if r.stars is not None)
        if with_stars > 0:
            avg = sum(r.stars for r in review_set.reviews if r.stars is not None) / with_stars
            self._stats_label.setText(
                f"{format_count(count)} review{'s' if count != 1 else ''} on this marketplace · "
                f"average {avg:.1f} ★"
            )
        else:
            self._stats_label.setText(
                f"{format_count(count)} review{'s' if count != 1 else ''} on this marketplace"
            )

    def _emit_plugin_open(self, listing_or_anchor) -> None:
        # The list row hands us either a PluginListing (catalog hit)
        # or a PluginAnchor (catalog miss). We forward both as-is so
        # the marketplace dialog can decide what to do with each.
        self.plugin_open_requested.emit(listing_or_anchor)

    def _on_view_on_nostr(self) -> None:
        npub = _safe_encode_npub(self._pubkey)
        nostr_uri = f"nostr:{npub}"
        if not QDesktopServices.openUrl(QUrl(nostr_uri)):
            QDesktopServices.openUrl(QUrl(f"https://njump.me/{npub}"))

    def _on_follow_toggle(self) -> None:
        if self._is_followed:
            self.unfollow_requested.emit(self._pubkey)
        else:
            self.follow_requested.emit(self._pubkey)
        self.accept()

    def _on_mute_toggle(self) -> None:
        if self._is_muted:
            self.unmute_requested.emit(self._pubkey)
        else:
            self.mute_requested.emit(self._pubkey)
        self.accept()


# --------------------------------------------------------------------------- #
# One review row inside the dialog                                            #
# --------------------------------------------------------------------------- #

class _AuthorReviewRow(QFrame):
    """One plugin the author rated or commented on.

    Layout: plugin icon (48 px), name + stars + relative time on the
    first line, the comment body (ellipsised by Qt's word wrap, not
    via JavaScript-style truncation) underneath. The whole row is
    clickable when the plugin is known to the local catalog so the
    user can jump to the plugin's detail pane.
    """

    def __init__(
        self,
        *,
        review: AuthorReview,
        listing: Optional[PluginListing],
        on_open: Callable[[object], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._review = review
        self._listing = listing
        self._on_open = on_open
        self.setObjectName("author-review-row")
        self.setStyleSheet(
            "#author-review-row {"
            "  background-color: rgba(255,255,255,0.03);"
            "  border-radius: 8px;"
            "}"
            "#author-review-row:hover {"
            "  background-color: rgba(255,255,255,0.06);"
            "}"
        )
        if listing is not None:
            self.setCursor(Qt.PointingHandCursor)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(14)

        outer.addWidget(self._build_icon(), 0, Qt.AlignTop)
        outer.addLayout(self._build_text_column(), 1)

    def _build_icon(self) -> QLabel:
        icon = QLabel()
        icon.setFixedSize(QSize(48, 48))
        plugin_id = (
            self._listing.plugin_id if self._listing is not None
            else self._review.anchor.plugin_id
        )
        display_name = (
            self._listing.name if self._listing is not None
            else self._review.anchor.plugin_id
        )
        icon_path = self._listing.icon if self._listing is not None else ""
        icon.setPixmap(plugin_pixmap(
            plugin_id=plugin_id,
            name=display_name,
            icon_path=icon_path,
            size=48,
        ))
        return icon

    def _build_text_column(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)

        name_text = (
            self._listing.name if self._listing is not None
            else self._review.anchor.plugin_id
        )
        name = QLabel(f"<b>{_escape(name_text)}</b>")
        name.setStyleSheet("font-size: 14px; color: #fff;")
        head.addWidget(name, 0, Qt.AlignVCenter)

        if self._review.stars is not None:
            head.addWidget(RatingStars(rating=float(self._review.stars), size=12),
                           0, Qt.AlignVCenter)

        head.addStretch(1)

        rel = format_relative_time(_iso_from_unix(self._review.created_at))
        if rel:
            time_label = QLabel(f"<span style='color:#888'>{_escape(rel)}</span>")
            time_label.setStyleSheet("font-size: 11px;")
            head.addWidget(time_label, 0, Qt.AlignVCenter)
        col.addLayout(head)

        # Sub-line either echoes the comment text or notes that this
        # rating was star-only. Keeping both shapes here means rows are
        # always the same vertical rhythm.
        body = QLabel(
            _escape(self._review.comment) if self._review.comment
            else "<span style='color:#777; font-style: italic;'>"
                 "Rated without a written review</span>"
        )
        body.setWordWrap(True)
        body.setStyleSheet("color: #ccc; font-size: 12px;")
        body.setMaximumHeight(48)   # ~2 lines; longer text ellipsises via Qt
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        col.addWidget(body)

        if self._listing is None:
            hint = QLabel(
                "<span style='color:#777;'>This plugin isn't in your local "
                "registry. The review still anchors to its Nostr coordinate."
                "</span>"
            )
            hint.setStyleSheet("font-size: 11px;")
            hint.setWordWrap(True)
            col.addWidget(hint)

        return col

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            target = self._listing if self._listing is not None else self._review.anchor
            self._on_open(target)
        super().mouseReleaseEvent(event)


# --------------------------------------------------------------------------- #
# Small UI helpers                                                            #
# --------------------------------------------------------------------------- #

def _verified_chip(identifier: str) -> QLabel:
    chip = QLabel("✓ verified")
    chip.setToolTip(f"NIP-05 verified: {identifier}")
    chip.setStyleSheet(
        "QLabel {"
        "  color: white;"
        "  background-color: #2d7fff;"
        "  border-radius: 8px;"
        "  padding: 1px 8px;"
        "  font-size: 11px;"
        "  font-weight: 700;"
        "}"
    )
    return chip


def _you_chip() -> QLabel:
    chip = QLabel("YOU")
    chip.setStyleSheet(
        "QLabel {"
        "  color: #3aa14a;"
        "  background-color: rgba(58,161,74,0.12);"
        "  border: 1px solid rgba(58,161,74,0.55);"
        "  border-radius: 4px;"
        "  padding: 0 6px;"
        "  font-size: 10px;"
        "  font-weight: 700;"
        "}"
    )
    chip.setToolTip("This is the profile you're signed in with.")
    return chip


def _info_panel(title: str, body: str) -> QFrame:
    panel = QFrame()
    panel.setStyleSheet(
        "QFrame {"
        "  background-color: rgba(255,255,255,0.03);"
        "  border-radius: 8px;"
        "  padding: 12px;"
        "}"
    )
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(4)
    t = QLabel(f"<b>{_escape(title)}</b>")
    t.setStyleSheet("color: #ddd; font-size: 13px;")
    layout.addWidget(t)
    b = QLabel(f"<span style='color:#aaa'>{_escape(body)}</span>")
    b.setWordWrap(True)
    b.setStyleSheet("font-size: 12px;")
    layout.addWidget(b)
    return panel


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


def _copy_to_clipboard(text: str) -> None:
    clipboard = QGuiApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(text)


def _safe_encode_npub(pubkey: str) -> str:
    """Encode ``pubkey`` as ``npub1...`` falling back to the hex form.

    Wrapped in try/except because ``encode_npub`` raises ValueError on
    malformed input, and the dialog should be resilient to a bad
    profile rather than refusing to render entirely.
    """
    try:
        from nostr.bech32 import encode_npub
        return encode_npub(pubkey)
    except Exception:  # noqa: BLE001
        return pubkey


def _short_npub(npub: str) -> str:
    if len(npub) < 16:
        return npub
    return f"{npub[:10]}...{npub[-6:]}"


def _short_pubkey(pubkey: str) -> str:
    if len(pubkey) < 12:
        return pubkey
    return f"{pubkey[:6]}...{pubkey[-4:]}"


def _iso_from_unix(seconds: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z",
    )


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


# --------------------------------------------------------------------------- #
# Style sheet fragments                                                       #
# --------------------------------------------------------------------------- #

_PRIMARY_BUTTON_STYLE = (
    "QPushButton {"
    "  background-color: #2d7fff;"
    "  color: white;"
    "  border: none;"
    "  border-radius: 6px;"
    "  padding: 7px 14px;"
    "  font-weight: 600;"
    "  font-size: 12px;"
    "}"
    "QPushButton:hover { background-color: #4a91ff; }"
    "QPushButton:pressed { background-color: #226de0; }"
)


_SECONDARY_BUTTON_STYLE = (
    "QPushButton {"
    "  background-color: rgba(255,255,255,0.06);"
    "  color: #ddd;"
    "  border: 1px solid rgba(255,255,255,0.12);"
    "  border-radius: 6px;"
    "  padding: 7px 14px;"
    "  font-size: 12px;"
    "}"
    "QPushButton:hover {"
    "  background-color: rgba(255,255,255,0.10);"
    "  border-color: rgba(255,255,255,0.20);"
    "}"
)


_ZAP_BUTTON_STYLE = (
    "QPushButton {"
    "  background-color: rgba(245,180,0,0.10);"
    "  color: rgba(255,210,90,0.65);"
    "  border: 1px solid rgba(245,180,0,0.30);"
    "  border-radius: 6px;"
    "  padding: 7px 14px;"
    "  font-weight: 600;"
    "  font-size: 12px;"
    "}"
    "QPushButton:disabled {"
    "  color: rgba(255,210,90,0.45);"
    "}"
)


_LINK_BUTTON_STYLE = (
    "QPushButton {"
    "  color: #2d7fff;"
    "  background: transparent;"
    "  border: none;"
    "  padding: 0;"
    "  font-size: 11px;"
    "}"
    "QPushButton:hover { color: #5fa0ff; }"
)
