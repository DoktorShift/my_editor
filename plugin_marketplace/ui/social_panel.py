"""The social section inside the plugin detail pane.

Top-to-bottom layout:

  1. Summary line: average rating, count, comment count, zap total.
  2. Rating distribution histogram (5 bars, 1★ to 5★).
  3. Your contribution surface:
       * Signed-in users see a star input + textarea + Publish.
       * Anonymous users see a single CTA "Connect with Nostr".
  4. Filter chips: All / Verified only (default ON when verified
     pubkeys exist).
  5. Reviews list (newest first), built from ``CommentNode`` trees.
  6. Empty states:
       * No listing event published yet for this plugin.
       * No engagement yet ("Be the first to review").
       * No matches under current filter.

The panel never touches relays itself. It asks its parent (the
detail pane) for a fresh ``EngagementSnapshot`` whenever the
fetcher signals new data, then redraws.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from ..models import PluginListing
from ..social.models import (
    CommentNode,
    EngagementSnapshot,
    PluginAnchor,
    RatingDistribution,
    Stars,
)
from .icons import plugin_pixmap
from .review_card import ReviewCard
from .stats import (
    CompactStatsLine,
    LightningTipChip,
    RatingStars,
    format_count,
    format_sats,
)


# Publish state machine surfaced in the composer. ``IDLE`` is the
# normal state; the others are short-lived transitions the dialog
# narrates while a kind:1985 / 1111 / 5 is mid-flight.
#
# Typed enum (not string constants) so callers can't typo a state and
# the type system flags misuse. The string values are kept stable for
# debug logs and for legacy callers that import the names directly.

import enum as _enum


class PublishState(str, _enum.Enum):
    """Lifecycle states for a top-level review publish.

    Stored on the ``SocialPanel`` as instance state so re-renders
    triggered by streaming relay events don't destroy the in-flight
    transition (a critical bug from the production audit).
    """

    IDLE = "idle"
    SIGNING = "signing"
    BROADCASTING = "broadcasting"
    PUBLISHED = "published"
    FAILED = "failed"


# Backwards-compatible constants for existing call sites. New code
# should import ``PublishState`` directly.
PUBLISH_IDLE = PublishState.IDLE
PUBLISH_SIGNING = PublishState.SIGNING
PUBLISH_BROADCASTING = PublishState.BROADCASTING
PUBLISH_PUBLISHED = PublishState.PUBLISHED
PUBLISH_FAILED = PublishState.FAILED


class SocialPanel(QWidget):
    """Reviews + ratings + zaps for one plugin.

    Switch between three macro states:
      ``HIDDEN``   plugin has no Nostr listing event; nothing to render.
      ``EMPTY``    listing exists but no engagement yet; encourage a first contribution.
      ``CONTENT``  show the histogram + composer + reviews list.
    """

    rate_requested = Signal(int)                # stars 1..5
    comment_requested = Signal(str, str)        # parent_event_id ("" for top), body
    delete_requested = Signal(str)              # event id
    mute_requested = Signal(str)                # author pubkey
    unmute_requested = Signal(str)              # author pubkey
    connect_clicked = Signal()
    tip_address_copied = Signal(str)
    zap_requested = Signal(str)                 # target pubkey (plugin author or commenter)
    profile_open_requested = Signal(str)        # author pubkey - opens profile modal/web
    # Trust filter toggles: parent rebuilds the policy + re-aggregates.
    verified_filter_changed = Signal(bool)
    follow_filter_changed = Signal(bool)
    # Asks the parent (marketplace dialog) to re-run its render
    # pipeline with the current panel state. Used when filter or
    # reply state changes locally; the aggregator output is unchanged.
    redraw_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._listing: Optional[PluginListing] = None
        self._anchor: Optional[PluginAnchor] = None
        self._signed_in: bool = False
        self._verified_filter: bool = False
        self._follow_filter: bool = False
        # When at least one card surfaces a verified contributor we
        # offer to switch the filter on by default. The chip can still
        # be unchecked manually.
        self._verified_default_offered: bool = False
        # Active inline reply target: the parent comment's event id.
        # Replies appear under the comment being replied to (GitHub
        # / Reddit pattern) rather than as a label inside the top
        # composer. At most one reply composer is open at a time.
        self._active_reply_target: Optional[str] = None
        self._verified_view: dict[str, Optional[bool]] = {}
        self._profile_view: dict[str, dict] = {}
        # The set of pubkeys currently muted by the viewer. Populated
        # by the parent so the cards can render the muted-state badge
        # and surface "Unmute" without re-querying the cache per card.
        self._muted_view: frozenset = frozenset()
        # Publish state outlives any individual composer widget. The
        # composer's ``_build_composer`` reads these to re-paint the
        # status pill correctly even when a snapshot re-render rebuilds
        # the widget tree mid-flight.
        self._publish_state: PublishState = PublishState.IDLE
        self._publish_message: str = ""
        self._composer_draft: str = ""
        # Viewer's own profile (avatar + display name) for the
        # composer banner. Filled in by ``show_for`` whenever the
        # marketplace dialog has an active Nostr session.
        self._viewer_display_name: str = ""
        self._viewer_picture_url: str = ""
        self._viewer_avatar_pixmap = None
        # Avatar map: pubkey -> QPixmap. Populated by the marketplace
        # dialog as ``AvatarBatchLoader.ready`` fires. The social panel
        # never downloads images itself.
        self._avatar_view: dict = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)

        # Section header
        self._heading = QLabel("Reviews and engagement")
        self._heading.setStyleSheet("font-size: 13px; font-weight: 600; color: #aaa;")
        layout.addWidget(self._heading)

        self._content_holder = QWidget()
        self._content_layout = QVBoxLayout(self._content_holder)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(12)
        layout.addWidget(self._content_holder, 1)

        self.setVisible(False)

    # ----------------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------------

    def show_for(
        self,
        *,
        listing: PluginListing,
        anchor: Optional[PluginAnchor],
        signed_in: bool,
        viewer_display_name: str = "",
        viewer_picture_url: str = "",
        viewer_avatar_pixmap=None,
    ) -> None:
        """Render the section for the given plugin + viewer state.

        ``viewer_*`` carry the signed-in user's own profile data so
        the composer can show their actual avatar + name rather than
        a generated placeholder. All three are optional; the panel
        falls back gracefully when they're missing.
        """
        self._listing = listing
        self._anchor = anchor
        self._signed_in = signed_in
        self._active_reply_target = None
        self._viewer_display_name = viewer_display_name
        self._viewer_picture_url = viewer_picture_url
        self._viewer_avatar_pixmap = viewer_avatar_pixmap
        if anchor is None:
            self._render_no_listing()
            self.setVisible(True)
            return
        # Initial render with empty snapshot; the detail pane will
        # push a real one shortly via update_snapshot.
        self._render_empty_engagement()
        self.setVisible(True)

    def update_viewer_profile(
        self,
        *,
        display_name: str = "",
        picture_url: str = "",
        avatar_pixmap=None,
    ) -> None:
        """Hot-update the viewer's profile data without re-rendering.

        Used when the user's avatar download finishes mid-session;
        the marketplace dialog calls this and triggers a redraw so
        the new pixmap shows up in the composer.
        """
        self._viewer_display_name = display_name
        self._viewer_picture_url = picture_url
        self._viewer_avatar_pixmap = avatar_pixmap

    def update_avatar(self, pubkey: str, pixmap) -> None:
        """Record a downloaded avatar for ``pubkey``.

        Cards re-rendered after this call use the pixmap instead of
        the generated placeholder. Caller is responsible for asking
        the panel to redraw (typically by pushing a snapshot again).
        """
        if pixmap is not None and not pixmap.isNull():
            self._avatar_view[pubkey] = pixmap

    def update_snapshot(
        self,
        snapshot: EngagementSnapshot,
        *,
        verified_view: dict,
        profile_view: dict,
        muted_view: Optional[frozenset] = None,
    ) -> None:
        """Re-render with fresh aggregator output.

        ``verified_view`` is ``{pubkey: True|False|None}`` so the card
        renderer can decide whether to draw the NIP-05 badge.

        ``profile_view`` is ``{pubkey: {"display_name", "picture_url",
        "nip05"}}``. Missing entries fall back to a short-pubkey form.

        ``muted_view`` is the user's current NIP-51 mute set so cards
        can render the muted-state affordance. ``None`` means "no
        change" so the panel keeps whatever it had — this lets the
        parent push a snapshot mid-mute-fetch without flickering.
        """
        self._verified_view = verified_view
        self._profile_view = profile_view
        if muted_view is not None:
            self._muted_view = muted_view
        if snapshot.distribution.total == 0 and snapshot.comment_count == 0 and snapshot.zap_count == 0:
            self._render_empty_engagement(snapshot=snapshot)
            return
        self._render_content(snapshot)

    def clear(self) -> None:
        """Hide the section (used when the detail pane is empty)."""
        self._listing = None
        self._anchor = None
        self.setVisible(False)

    # ----------------------------------------------------------------------
    # Renderers
    # ----------------------------------------------------------------------

    def _wipe_content(self) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_no_listing(self) -> None:
        """Plugin has no nostr_naddr; engagement features unavailable."""
        self._wipe_content()
        card = _info_card(
            icon="📡",
            title="No Nostr listing for this plugin yet",
            body=(
                "Reviews, comments and zaps anchor on a kind:30700 "
                "Nostr event published by the plugin author. Until "
                "they publish one, engagement features stay disabled "
                "for this plugin."
            ),
        )
        self._content_layout.addWidget(card)

    def _render_empty_engagement(
        self,
        *,
        snapshot: Optional[EngagementSnapshot] = None,
    ) -> None:
        """Listing exists but nobody has rated or commented yet."""
        self._wipe_content()
        if self._signed_in:
            self._content_layout.addWidget(self._build_composer())
        else:
            self._content_layout.addWidget(self._build_anonymous_cta(
                primary="Be the first to review",
            ))
        empty = _info_card(
            icon="✨",
            title="No reviews yet",
            body=(
                "Be the first person to rate this plugin. "
                "Your rating travels with you across every Nostr app."
                if self._signed_in
                else "Sign in with Nostr to rate, comment, or zap."
            ),
        )
        self._content_layout.addWidget(empty)
        self._content_layout.addStretch(1)

    def _render_content(self, snapshot: EngagementSnapshot) -> None:
        self._wipe_content()
        self._content_layout.addWidget(self._build_summary_row(snapshot))
        self._content_layout.addWidget(self._build_histogram(snapshot.distribution))

        if self._signed_in:
            self._content_layout.addWidget(self._build_composer(snapshot=snapshot))
        else:
            self._content_layout.addWidget(self._build_anonymous_cta())

        if snapshot.comments:
            self._content_layout.addWidget(self._build_filter_bar())
            for node in self._filter_threads(snapshot.comments):
                self._add_thread(node)
        else:
            self._content_layout.addWidget(_info_card(
                icon="💬",
                title="No written reviews yet",
                body=(
                    "Ratings are in but nobody has left a comment yet. "
                    "Share what you think to help others decide."
                    if self._signed_in
                    else "Ratings are in but nobody has commented yet."
                ),
            ))

        self._content_layout.addStretch(1)

    # ----------------------------------------------------------------------
    # Sub-widgets
    # ----------------------------------------------------------------------

    def _build_summary_row(self, snapshot: EngagementSnapshot) -> QWidget:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)

        dist = snapshot.distribution
        average = QLabel(
            f"<span style='font-size: 22px; font-weight: 700; color: #ddd;'>"
            f"{dist.average:.1f}</span>"
            f"<span style='color:#888; margin-left: 6px;'> of 5</span>"
        )
        row.addWidget(average)

        stars = RatingStars(rating=dist.average, size=14)
        row.addWidget(stars)

        meta_bits: List[str] = []
        meta_bits.append(f"{format_count(dist.total)} rating{'s' if dist.total != 1 else ''}")
        if snapshot.comment_count:
            meta_bits.append(f"{format_count(snapshot.comment_count)} review{'s' if snapshot.comment_count != 1 else ''}")
        if snapshot.zaps_sats:
            meta_bits.append(f"{format_sats(snapshot.zaps_sats)} sats zapped")
        meta_label = QLabel(
            "<span style='color:#888;'>" + " · ".join(meta_bits) + "</span>"
        )
        row.addWidget(meta_label)
        row.addStretch(1)

        # Zap action: opens the real zap dialog (NIP-57 + LNURL flow).
        # Only shown when a lightning address is advertised; otherwise
        # there's nothing to send sats to.
        if self._listing is not None and self._listing.lightning_address:
            zap_btn = QPushButton("⚡ Zap")
            zap_btn.setCursor(Qt.PointingHandCursor)
            zap_btn.setAutoDefault(False)
            zap_btn.setStyleSheet(
                "QPushButton {"
                "  background-color: rgba(245,180,0,0.12);"
                "  color: #ffd45a;"
                "  border: 1px solid rgba(245,180,0,0.55);"
                "  border-radius: 14px;"
                "  padding: 5px 14px;"
                "  font-weight: 600;"
                "  font-size: 12px;"
                "}"
                "QPushButton:hover {"
                "  background-color: rgba(245,180,0,0.20);"
                "}"
            )
            anchor_pubkey = (
                self._anchor.author_pubkey if self._anchor is not None else ""
            )
            zap_btn.clicked.connect(
                lambda *_a, pk=anchor_pubkey: self.zap_requested.emit(pk)
            )
            row.addWidget(zap_btn)
        return wrap

    def _build_histogram(self, distribution: RatingDistribution) -> QWidget:
        wrap = QFrame()
        wrap.setObjectName("histogram")
        wrap.setStyleSheet(
            "#histogram { background: rgba(255,255,255,0.03); border-radius: 8px; }"
        )
        grid = QVBoxLayout(wrap)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setSpacing(4)
        # Show 5 -> 1 like every store does; bigger stars on top reads
        # as "the good news first".
        for star in (5, 4, 3, 2, 1):
            grid.addLayout(self._histogram_row(star, distribution))
        return wrap

    def _histogram_row(self, star: int, distribution: RatingDistribution) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(f"{star} ★")
        label.setStyleSheet("color: #888; font-size: 11px;")
        label.setFixedWidth(28)
        row.addWidget(label)

        bar = _ProgressBarLite(percent=distribution.percent(star))
        row.addWidget(bar, 1)

        count = QLabel(format_count(distribution.bucket(star)))
        count.setStyleSheet("color: #aaa; font-size: 11px;")
        count.setFixedWidth(36)
        count.setAlignment(Qt.AlignRight)
        row.addWidget(count)
        return row

    def _build_composer(self, *, snapshot: Optional[EngagementSnapshot] = None) -> QWidget:
        """Top-of-section composer for a top-level review.

        Layout:

            [avatar]  Posting as @alice
            ★ ★ ★ ★ ★    you rated 2/5
            Share what you think (optional)
            [           textarea            ]
                                   [ Publish ]

        Replies live in their own inline composer underneath the
        comment being replied to (see ``_build_reply_composer``).
        """
        wrap = QFrame()
        wrap.setObjectName("composer")
        wrap.setStyleSheet(
            "#composer { background: rgba(45,127,255,0.05); "
            "border-radius: 10px; }"
        )
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        existing = snapshot.own_rating if snapshot is not None else None
        existing_stars = existing.stars.value if existing else 0

        # Identity banner: the signed-in profile's avatar + name.
        layout.addWidget(self._build_viewer_banner(
            heading="Update your review" if existing else "Write a review",
        ))

        # Stars row: picker plus a subtle "you rated N/5" hint.
        stars_row = QHBoxLayout()
        stars_row.setSpacing(12)
        stars_row.setContentsMargins(0, 0, 0, 0)

        star_picker = _StarPicker(initial=existing_stars)
        star_picker.changed.connect(self.rate_requested.emit)
        stars_row.addWidget(star_picker, 0, Qt.AlignVCenter)

        if existing is not None:
            current_hint = QLabel(
                f"<span style='color:#888'>you rated this {existing.stars.value}/5"
                " &mdash; tap a different star to change it</span>"
            )
            current_hint.setStyleSheet("font-size: 12px;")
            current_hint.setWordWrap(True)
            stars_row.addWidget(current_hint, 1, Qt.AlignVCenter)
        else:
            stars_row.addStretch(1)

        layout.addLayout(stars_row)

        body_prompt = QLabel("Share what you think (optional)")
        body_prompt.setStyleSheet("color: #aaa; font-size: 12px;")
        layout.addWidget(body_prompt)

        self._composer_body = QPlainTextEdit()
        self._composer_body.setPlaceholderText(
            "What did you like, or wish was different?"
        )
        self._composer_body.setFixedHeight(88)
        self._composer_body.setStyleSheet(_TEXTAREA_STYLE)
        # Restore any draft the user was mid-way through so a snapshot
        # re-render mid-publish does not wipe their text. ``setPlainText``
        # is safe even when the draft is empty (the placeholder shows).
        if self._composer_draft:
            self._composer_body.setPlainText(self._composer_draft)
        self._composer_body.textChanged.connect(self._sync_composer_draft)
        layout.addWidget(self._composer_body)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 4, 0, 0)

        # Status pill: reads "Publishing…" while the bunker signs and
        # relays accept; flips to "Published" or "Failed: <reason>" so
        # the user never wonders whether their click did anything.
        self._publish_status = QLabel("")
        self._publish_status.setStyleSheet("color: #888; font-size: 12px;")
        self._publish_status.setWordWrap(True)
        actions.addWidget(self._publish_status, 1)

        self._publish_btn = QPushButton("Publish review")
        self._publish_btn.setDefault(True)
        self._publish_btn.clicked.connect(self._on_publish_top)
        actions.addWidget(self._publish_btn, 0, Qt.AlignVCenter)
        layout.addLayout(actions)

        # Re-paint the publish status from instance state so a snapshot
        # re-render mid-flight resumes the "Signing…" / "Publishing…"
        # affordance instead of resetting to IDLE.
        self._repaint_publish_state()

        return wrap

    def _sync_composer_draft(self) -> None:
        if hasattr(self, "_composer_body"):
            self._composer_draft = self._composer_body.toPlainText()

    def _build_viewer_banner(self, *, heading: str) -> QWidget:
        """Avatar + display-name strip used at the top of every composer.

        Tells the user exactly which profile is about to publish, the
        same way Twitter/Bluesky labels their compose dialogs.
        """
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        avatar = QLabel()
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setPixmap(_viewer_avatar_pixmap(
            pixmap=self._viewer_avatar_pixmap,
            display_name=self._viewer_display_name,
            anchor=self._anchor,
            size=32,
        ))
        row.addWidget(avatar, 0, Qt.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(0)
        heading_label = QLabel(heading)
        heading_label.setStyleSheet("font-weight: 600; font-size: 14px; color: #ddd;")
        text_col.addWidget(heading_label)
        display = self._viewer_display_name or "your Nostr profile"
        posting_as = QLabel(
            f"<span style='color:#888; font-size: 11px;'>Posting as {_escape(display)}</span>"
        )
        text_col.addWidget(posting_as)
        row.addLayout(text_col, 1)
        return wrap

    def _build_reply_composer(self, parent_comment) -> QWidget:
        """Inline composer that appears underneath the comment being
        replied to. Closes itself on Publish or Cancel.

        Carries the viewer's avatar + display name so the user knows
        which profile will sign the reply.
        """
        wrap = QFrame()
        wrap.setObjectName("reply-composer")
        wrap.setStyleSheet(
            "#reply-composer {"
            "  background: rgba(45,127,255,0.05);"
            "  border-left: 3px solid rgba(45,127,255,0.45);"
            "  border-radius: 6px;"
            "}"
        )
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        parent_author_pubkey = parent_comment.author_pubkey
        parent_display = (
            self._profile_view.get(parent_author_pubkey, {}).get("display_name")
            or _short_pubkey(parent_author_pubkey)
        )
        layout.addWidget(self._build_viewer_banner(
            heading=f"Reply to {parent_display}",
        ))

        body = QPlainTextEdit()
        body.setPlaceholderText("Write your reply...")
        body.setFixedHeight(72)
        body.setStyleSheet(_TEXTAREA_STYLE)
        body.setFocus()
        layout.addWidget(body)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 2, 0, 0)
        actions.addStretch(1)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setAutoDefault(False)
        cancel_btn.clicked.connect(self._cancel_reply)
        actions.addWidget(cancel_btn)

        publish_btn = QPushButton("Reply")
        publish_btn.setDefault(True)
        publish_btn.clicked.connect(
            lambda *_a, b=body, pid=parent_comment.event_id: self._publish_reply(pid, b)
        )
        actions.addWidget(publish_btn)
        layout.addLayout(actions)

        return wrap

    def _build_anonymous_cta(self, *, primary: str = "Connect with Nostr") -> QWidget:
        wrap = QFrame()
        wrap.setObjectName("cta")
        wrap.setStyleSheet(
            "#cta { background: rgba(45,127,255,0.06); "
            "border: 1px solid rgba(45,127,255,0.20); border-radius: 8px; }"
        )
        row = QHBoxLayout(wrap)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(12)

        label = QLabel(
            "<b>Sign in to interact</b><br>"
            "<span style='color:#aaa'>Connect a Nostr profile to rate, comment, or zap. "
            "Your contributions stay with you across every Nostr app.</span>"
        )
        label.setWordWrap(True)
        row.addWidget(label, 1)

        btn = QPushButton(primary)
        btn.setDefault(True)
        btn.clicked.connect(self.connect_clicked.emit)
        row.addWidget(btn)
        return wrap

    def _build_filter_bar(self) -> QWidget:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        label = QLabel("<span style='color:#888'>Reviews</span>")
        row.addWidget(label)

        row.addStretch(1)

        # "From people I follow" — only meaningful when signed in,
        # since the follow set comes from the active profile.
        if self._signed_in:
            self._follow_chip = QPushButton("From people I follow")
            self._follow_chip.setCheckable(True)
            self._follow_chip.setChecked(self._follow_filter)
            self._follow_chip.setCursor(Qt.PointingHandCursor)
            self._follow_chip.setToolTip(
                "Hide reviews from accounts you don't follow. Uses your "
                "Nostr contact list (NIP-02)."
            )
            self._follow_chip.setStyleSheet(_FILTER_CHIP_STYLE)
            self._follow_chip.clicked.connect(self._toggle_follow_filter)
            row.addWidget(self._follow_chip)

        self._verified_chip = QPushButton("Verified only")
        self._verified_chip.setCheckable(True)
        self._verified_chip.setChecked(self._verified_filter)
        self._verified_chip.setCursor(Qt.PointingHandCursor)
        self._verified_chip.setToolTip(
            "Only show reviews whose author has a NIP-05 verified identifier."
        )
        self._verified_chip.setStyleSheet(_FILTER_CHIP_STYLE)
        self._verified_chip.clicked.connect(self._toggle_verified_filter)
        row.addWidget(self._verified_chip)

        return wrap

    def _add_thread(self, node: CommentNode) -> None:
        # Top-level card.
        rating_stars = self._find_rating_for(node.comment.author_pubkey)
        card = self._build_card(node, stars=rating_stars)
        self._content_layout.addWidget(card)

        # Inline reply composer directly under the card if the user
        # clicked Reply on it. One open reply at a time across the
        # whole thread tree.
        if self._signed_in and self._active_reply_target == node.comment.event_id:
            self._content_layout.addWidget(
                self._build_inline_reply_holder(node.comment, indent=False)
            )

        # Render one level of nested replies inline. Deeper threading is
        # rare on Nostr-rendered plugin pages so far; if we need it
        # later, swap the indent for a tree widget.
        for child in node.children:
            self._content_layout.addWidget(
                self._indented_widget(self._build_card(child, stars=None))
            )
            if self._signed_in and self._active_reply_target == child.comment.event_id:
                self._content_layout.addWidget(
                    self._build_inline_reply_holder(child.comment, indent=True)
                )

    def _indented_widget(self, widget: QWidget) -> QWidget:
        indent_row = QHBoxLayout()
        indent_row.setContentsMargins(0, 0, 0, 0)
        indent_row.addSpacerItem(QSpacerItem(28, 1))
        indent_row.addWidget(widget, 1)
        holder = QWidget()
        holder.setLayout(indent_row)
        return holder

    def _build_inline_reply_holder(self, parent_comment, *, indent: bool) -> QWidget:
        composer = self._build_reply_composer(parent_comment)
        if indent:
            return self._indented_widget(composer)
        return composer

    def _build_card(self, node: CommentNode, *, stars: Optional[int]) -> ReviewCard:
        pubkey = node.comment.author_pubkey
        profile = self._profile_view.get(pubkey, {})
        is_plugin_author = bool(self._anchor and pubkey == self._anchor.author_pubkey)
        is_own = self._is_own_event(node.comment.event_id)
        is_muted = pubkey in self._muted_view

        card = ReviewCard(
            node=node,
            author_display=profile.get("display_name", ""),
            author_picture_url=profile.get("picture_url", ""),
            avatar_pixmap=self._avatar_view.get(pubkey),
            nip05_identifier=profile.get("nip05", ""),
            nip05_verified=self._verified_view.get(pubkey),
            is_plugin_author=is_plugin_author,
            is_own=is_own,
            is_muted=is_muted,
            is_signed_in=self._signed_in,
            stars=stars,
        )
        card.reply_clicked.connect(self._set_reply_target)
        card.delete_clicked.connect(self.delete_requested.emit)
        card.mute_clicked.connect(self.mute_requested.emit)
        card.avatar_clicked.connect(self.profile_open_requested.emit)
        card.unmute_clicked.connect(self.unmute_requested.emit)
        card.zap_clicked.connect(self.zap_requested.emit)
        return card

    # ----------------------------------------------------------------------
    # State helpers
    # ----------------------------------------------------------------------

    def _filter_threads(self, threads: tuple) -> List[CommentNode]:
        """Snapshot-side filter result.

        The aggregator already applied the trust policy (mute, verified,
        follow-graph) before producing the snapshot. We keep this
        method as a pass-through so future client-only filters (e.g.
        "from this week") can layer in without touching the dialog.
        """
        return list(threads)

    def _toggle_verified_filter(self) -> None:
        self._verified_filter = self._verified_chip.isChecked()
        # Trust policy lives in the parent. Notify and let it re-run
        # the aggregator so the histogram, counts, and review list all
        # update in lock-step.
        self.verified_filter_changed.emit(self._verified_filter)

    def _toggle_follow_filter(self) -> None:
        self._follow_filter = self._follow_chip.isChecked()
        self.follow_filter_changed.emit(self._follow_filter)

    def verified_filter_state(self) -> bool:
        """Public accessor so the parent can read panel state when
        building the next trust policy."""
        return self._verified_filter

    def follow_filter_state(self) -> bool:
        return self._follow_filter

    def _set_reply_target(self, parent_event_id: str) -> None:
        """Toggle the inline reply composer for the given parent.

        Clicking Reply on the same comment twice closes the composer
        (the universal pattern). Clicking Reply on a different comment
        moves the composer there: only one open reply at a time.
        """
        if not self._signed_in:
            self.connect_clicked.emit()
            return
        if self._active_reply_target == parent_event_id:
            self._active_reply_target = None
        else:
            self._active_reply_target = parent_event_id
        # Re-render with the new reply state. The marketplace dialog
        # pushes the latest snapshot back through on the next signal
        # tick; until then, signal a redraw by emitting nothing - the
        # caller (dialog) handles redraws on snapshot_changed.
        self._request_redraw()

    def _cancel_reply(self) -> None:
        self._active_reply_target = None
        self._request_redraw()

    def _request_redraw(self) -> None:
        """Re-run the snapshot render with the current state.

        The aggregator output didn't change; the panel just needs to
        repaint with a different reply target or filter. Emit a
        zero-arg pulse the marketplace dialog can hook to re-push.
        """
        self.redraw_requested.emit()

    def _publish_reply(self, parent_event_id: str, body_widget) -> None:
        body = body_widget.toPlainText().strip()
        if not body:
            return
        self.comment_requested.emit(parent_event_id, body)
        self._active_reply_target = None
        self._request_redraw()

    def _on_publish_top(self) -> None:
        """Publish from the top composer as a top-level review (not a reply)."""
        if not hasattr(self, "_composer_body"):
            return
        body = self._composer_body.toPlainText().strip()
        if not body:
            return
        # Mark the composer as in-flight before emitting so the dialog
        # can react immediately. The dialog will also drive the
        # ``set_publish_state`` lifecycle as the bunker progresses.
        self.set_publish_state(PublishState.SIGNING, "Signing…")
        self.comment_requested.emit("", body)

    def set_publish_state(self, state, message: str = "") -> None:
        """Reflect publish-job progress in the composer.

        State persists on the panel instance so a snapshot re-render
        triggered by streaming relay events does not wipe the in-flight
        affordance. Called by the marketplace dialog with one of the
        ``PublishState`` enum members.
        """
        if isinstance(state, str):
            try:
                state = PublishState(state)
            except ValueError:
                state = PublishState.IDLE
        self._publish_state = state
        self._publish_message = message or ""
        # PUBLISHED is a transient state — clear the draft and decay
        # back to IDLE so the textarea is ready for the next review
        # the next time the composer is rendered.
        if state == PublishState.PUBLISHED:
            self._composer_draft = ""
            if hasattr(self, "_composer_body"):
                self._composer_body.blockSignals(True)
                self._composer_body.clear()
                self._composer_body.blockSignals(False)
        self._repaint_publish_state()

    def _repaint_publish_state(self) -> None:
        """Apply ``self._publish_state`` to the live composer widgets.

        Safe to call when the composer is not currently realised; the
        guards keep this a no-op rather than raising during a render
        that doesn't include a composer (anonymous viewer, hidden
        panel, etc.).
        """
        if not hasattr(self, "_publish_btn") or not hasattr(self, "_publish_status"):
            return
        if not hasattr(self, "_composer_body"):
            return
        state = self._publish_state
        message = self._publish_message
        if state == PublishState.IDLE:
            self._publish_btn.setEnabled(True)
            self._publish_btn.setText("Publish review")
            self._composer_body.setEnabled(True)
            self._publish_status.setText("")
            self._publish_status.setStyleSheet("color: #888; font-size: 12px;")
        elif state == PublishState.SIGNING:
            self._publish_btn.setEnabled(False)
            self._publish_btn.setText("Signing…")
            self._composer_body.setEnabled(False)
            self._publish_status.setText(message or "Waiting on your signer…")
            self._publish_status.setStyleSheet("color: #aaa; font-size: 12px;")
        elif state == PublishState.BROADCASTING:
            self._publish_btn.setEnabled(False)
            self._publish_btn.setText("Publishing…")
            self._composer_body.setEnabled(False)
            self._publish_status.setText(message or "Broadcasting to relays…")
            self._publish_status.setStyleSheet("color: #aaa; font-size: 12px;")
        elif state == PublishState.PUBLISHED:
            self._publish_btn.setEnabled(True)
            self._publish_btn.setText("Publish review")
            self._composer_body.setEnabled(True)
            self._publish_status.setText(message or "Published.")
            self._publish_status.setStyleSheet("color: #3aa14a; font-size: 12px;")
        elif state == PublishState.FAILED:
            self._publish_btn.setEnabled(True)
            self._publish_btn.setText("Try again")
            self._composer_body.setEnabled(True)
            self._publish_status.setText(message or "Publish failed.")
            self._publish_status.setStyleSheet("color: #c44; font-size: 12px;")

    def publish_state(self) -> PublishState:
        """Public read of the current publish lifecycle state."""
        return self._publish_state

    def _find_rating_for(self, pubkey: str) -> Optional[int]:
        """Look up the stars value the latest cached rating event
        from ``pubkey`` (if any). The detail pane passes this via
        the profile view's ``stars`` slot when relevant."""
        slot = self._profile_view.get(pubkey, {})
        stars = slot.get("stars")
        if isinstance(stars, int) and 1 <= stars <= 5:
            return stars
        return None

    def _is_own_event(self, event_id: str) -> bool:
        # Walk the profile view's ``own_event_ids`` set if the detail
        # pane provided one; otherwise we never claim ownership.
        own = self._profile_view.get("__own__", {}).get("event_ids", set())
        return event_id in own


# ──────────────────────────────────────────────────────────────────────
# Stars input widget
# ──────────────────────────────────────────────────────────────────────

class _StarPicker(QWidget):
    """Click-to-rate: hover highlights, click commits.

    Sized for inline use inside a single-line rating row. Each star
    is 22 px square with an 18 px glyph, the smallest size that still
    has a comfortable click target on touch screens. Larger sizes
    fight with the textarea and the supplementary hint for the
    panel's vertical space.
    """

    changed = Signal(int)

    def __init__(self, *, initial: int = 0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._value = max(0, min(5, initial))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        self._buttons: List[QPushButton] = []
        for i in range(1, 6):
            btn = QPushButton("★")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFlat(True)
            btn.setFixedSize(22, 22)
            btn.setAutoDefault(False)
            btn.clicked.connect(lambda *_a, v=i: self._pick(v))
            row.addWidget(btn)
            self._buttons.append(btn)
        self._paint()

    def _pick(self, value: int) -> None:
        self._value = value
        self._paint()
        self.changed.emit(value)

    def _paint(self) -> None:
        for i, btn in enumerate(self._buttons, start=1):
            filled = i <= self._value
            color = "#f5b400" if filled else "rgba(255,255,255,0.20)"
            btn.setStyleSheet(
                f"QPushButton {{ color: {color}; background: transparent; "
                "border: none; font-size: 18px; padding: 0; }}"
                "QPushButton:hover { color: #f5b400; }"
            )


# ──────────────────────────────────────────────────────────────────────
# Compact progress bar painted as a label
# ──────────────────────────────────────────────────────────────────────

class _ProgressBarLite(QWidget):
    """A flat horizontal bar showing the percentage portion of a row."""

    def __init__(self, *, percent: float, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._percent = max(0.0, min(100.0, percent))
        self.setFixedHeight(8)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        from PySide6.QtGui import QColor, QPainter
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        radius = rect.height() / 2

        # Background track.
        track = QColor(255, 255, 255, 18)
        painter.setBrush(track)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, radius, radius)

        if self._percent > 0:
            width = int(rect.width() * (self._percent / 100.0))
            painter.setBrush(QColor("#f5b400"))
            painter.drawRoundedRect(0, 0, width, rect.height(), radius, radius)
        painter.end()


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _info_card(*, icon: str, title: str, body: str) -> QWidget:
    """Boxed inline notice for empty / unavailable states."""
    wrap = QFrame()
    wrap.setStyleSheet(
        "QFrame { background: rgba(255,255,255,0.03); border-radius: 8px; }"
    )
    layout = QHBoxLayout(wrap)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(12)
    icon_label = QLabel(icon)
    icon_label.setStyleSheet("font-size: 22px; color: #888;")
    icon_label.setFixedWidth(32)
    layout.addWidget(icon_label, 0, Qt.AlignTop)
    text = QLabel(f"<b>{title}</b><br><span style='color:#aaa'>{body}</span>")
    text.setWordWrap(True)
    text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    layout.addWidget(text, 1)
    return wrap


_TEXTAREA_STYLE = (
    "QPlainTextEdit {"
    "  background: rgba(255,255,255,0.04);"
    "  border: 1px solid rgba(255,255,255,0.08);"
    "  border-radius: 6px;"
    "  padding: 8px;"
    "  font-size: 13px;"
    "  color: #ddd;"
    "}"
)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


def _viewer_avatar_pixmap(*, pixmap, display_name: str, anchor, size: int):
    """Best avatar we can show for the signed-in user.

    Real avatar wins when present; otherwise a deterministic generated
    avatar keyed on the user's display name (so the same name renders
    the same color across the editor's chips and the composer).
    """
    if pixmap is not None and not pixmap.isNull():
        return pixmap.scaled(
            size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
    seed = display_name or (anchor.author_pubkey if anchor else "you")
    return plugin_pixmap(
        plugin_id=seed,
        name=display_name or "You",
        icon_path="",
        size=size,
    )


def _short_pubkey(pubkey: str) -> str:
    if not pubkey or len(pubkey) < 12:
        return pubkey
    return f"{pubkey[:6]}…{pubkey[-4:]}"


_FILTER_CHIP_STYLE = (
    "QPushButton {"
    "  border: 1px solid rgba(255,255,255,0.10);"
    "  border-radius: 12px;"
    "  padding: 3px 11px;"
    "  background: transparent;"
    "  color: #bbb;"
    "  font-size: 12px;"
    "}"
    "QPushButton:hover {"
    "  color: #fff;"
    "  border-color: rgba(255,255,255,0.20);"
    "}"
    "QPushButton:checked {"
    "  background: rgba(45,127,255,0.18);"
    "  border-color: #2d7fff;"
    "  color: #fff;"
    "}"
)
