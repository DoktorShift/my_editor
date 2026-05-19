"""Detail pane: the right-hand side of the marketplace dialog.

Layout (VS Code / GitHub Marketplace pattern):

    ┌─────────────────────────────────────────────────────────┐
    │ [icon] Name                  [Installed] [Homepage] ... │  ← FIXED header
    │        version · author · license · source              │
    │        Tags: ...                                        │  no scroll
    ├─────────────────────────────────────────────────────────┤
    │                                                         │
    │  Description (rendered inline, no inset box)            │  ← SCROLLABLE
    │                                                         │     body
    │  Reviews and engagement                                 │
    │  [histogram] [composer] [reviews list]                  │
    │                                                         │
    └─────────────────────────────────────────────────────────┘

The fixed header keeps Install and the title on screen no matter how
long the description or review thread grows. The scrollable body
matches what every major marketplace does for long-form plugin pages.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from plugin_system import CAPABILITY_LABELS

from ..models import PluginListing
from ..social import EngagementAggregator, EngagementSnapshot, PluginAnchor, TrustPolicy
from .icons import plugin_pixmap
from .social_panel import SocialPanel
from .stats import LightningTipChip, RatingStars, format_count, format_sats


class DetailPane(QWidget):
    """Right side of the master-detail split."""

    install_requested = Signal(object)    # PluginListing
    uninstall_requested = Signal(object)  # PluginListing
    homepage_requested = Signal(str)      # URL
    nostr_open_requested = Signal(str)    # nostr_naddr to open in a web client
    tip_address_copied = Signal(str)      # lightning address just copied
    # Emitted whenever the listing on display changes (or empties).
    # The marketplace dialog uses this to start/stop relay subs.
    listing_changed = Signal(object)      # PluginListing or None

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._listing: Optional[PluginListing] = None
        self._installed = False
        self._has_update = False
        self._bundled = False
        self._busy = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Empty placeholder ─────────────────────────────────────────
        self._empty_label = QLabel(
            "<i>Select a plugin from the list on the left.</i>"
        )
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setStyleSheet("color: #888;")
        outer.addWidget(self._empty_label)

        # ── Fixed header: icon, title row with action buttons, meta, tags ─
        self._header_widget = self._build_header_widget()
        outer.addWidget(self._header_widget)

        # ── Scrollable body: description + social panel ───────────────
        # Right pane scrolls as one unit when the content is taller
        # than the dialog. VS Code Marketplace works the same way.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )

        body_widget = QWidget()
        body_layout = QVBoxLayout(body_widget)
        body_layout.setContentsMargins(20, 8, 20, 20)
        body_layout.setSpacing(16)

        self._description = self._build_description_widget()
        body_layout.addWidget(self._description)

        self._permissions = self._build_permissions_widget()
        body_layout.addWidget(self._permissions)

        self._social = SocialPanel()
        body_layout.addWidget(self._social)

        body_layout.addStretch(1)
        self._scroll.setWidget(body_widget)
        outer.addWidget(self._scroll, 1)

        self._set_state_empty()

    # ----------------------------------------------------------------------
    # Fixed header
    # ----------------------------------------------------------------------

    def _build_header_widget(self) -> QWidget:
        """Header band: icon + title row (name + buttons) + meta + tags.

        Always visible above the scrollable body. Action buttons sit
        on the title line, right-aligned, so Install stays in reach
        without scrolling no matter how long the description grows.
        """
        wrap = QWidget()
        wrap.setObjectName("detail-header")
        wrap.setStyleSheet(
            "#detail-header {"
            "  border-bottom: 1px solid rgba(255,255,255,0.06);"
            "}"
        )
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(16)

        # Icon
        self._icon_label = QLabel()
        self._icon_label.setFixedSize(56, 56)
        self._icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._icon_label, 0, Qt.AlignTop)

        # Text column
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)

        # Title row: name on the left, action buttons right-aligned.
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        self._name = QLabel()
        self._name.setStyleSheet("font-size: 18px; font-weight: 600;")
        title_row.addWidget(self._name, 1, Qt.AlignVCenter)

        self._primary_btn = QPushButton()
        self._primary_btn.setDefault(True)
        self._primary_btn.clicked.connect(self._on_primary)
        title_row.addWidget(self._primary_btn, 0, Qt.AlignVCenter)

        self._uninstall_btn = QPushButton("Uninstall")
        self._uninstall_btn.clicked.connect(self._on_uninstall)
        title_row.addWidget(self._uninstall_btn, 0, Qt.AlignVCenter)

        self._homepage_btn = QPushButton("Homepage")
        self._homepage_btn.clicked.connect(self._on_homepage)
        title_row.addWidget(self._homepage_btn, 0, Qt.AlignVCenter)

        self._nostr_btn = QPushButton("View on Nostr")
        self._nostr_btn.setToolTip("Open the plugin's Nostr listing in your browser.")
        self._nostr_btn.clicked.connect(self._on_nostr)
        title_row.addWidget(self._nostr_btn, 0, Qt.AlignVCenter)

        text_col.addLayout(title_row)

        # Meta + tags + (optional) install progress.
        self._meta = QLabel()
        self._meta.setStyleSheet("color: #888;")
        self._meta.setWordWrap(True)
        self._meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text_col.addWidget(self._meta)

        self._tags = QLabel()
        self._tags.setStyleSheet("color: #666;")
        self._tags.setWordWrap(True)
        text_col.addWidget(self._tags)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setVisible(False)
        self._progress.setMaximumHeight(6)
        self._progress.setTextVisible(False)
        text_col.addWidget(self._progress)

        # Compact stats strip: stays in the fixed header so the user
        # always sees the rating + counts + tip affordance without
        # scrolling the description.
        stats_row = QHBoxLayout()
        stats_row.setContentsMargins(0, 6, 0, 0)
        stats_row.setSpacing(8)

        self._header_stars = RatingStars(rating=0.0, size=13)
        stats_row.addWidget(self._header_stars, 0, Qt.AlignVCenter)

        self._header_stats = QLabel("")
        self._header_stats.setStyleSheet("color: #aaa; font-size: 12px;")
        self._header_stats.setTextInteractionFlags(Qt.TextSelectableByMouse)
        stats_row.addWidget(self._header_stats, 1, Qt.AlignVCenter)

        self._header_tip_chip = LightningTipChip()
        self._header_tip_chip.copied.connect(self.tip_address_copied)
        self._header_tip_chip.setVisible(False)
        stats_row.addWidget(self._header_tip_chip, 0, Qt.AlignVCenter)

        text_col.addLayout(stats_row)

        layout.addLayout(text_col, 1)
        return wrap

    # ----------------------------------------------------------------------
    # Description widget
    # ----------------------------------------------------------------------

    def _build_description_widget(self) -> QTextBrowser:
        """Inline rich-text description that reads as part of the page.

        Default QTextBrowser styling draws a dark inset viewport that
        looks like a separate box. We strip that so the body text
        sits flush with the rest of the page, the way VS Code and
        Chrome Web Store render their READMEs.
        """
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        view.setFrameShape(QTextBrowser.NoFrame)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.setStyleSheet(
            "QTextBrowser {"
            "  background: transparent;"
            "  border: none;"
            "  color: #ddd;"
            "  font-size: 13px;"
            "}"
        )
        view.document().setDocumentMargin(0)
        # We resize the browser to fit its content so the outer scroll
        # area drives the only scrollbar on the page.
        view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        view.document().contentsChanged.connect(
            lambda: self._refit_description()
        )
        return view

    def _refit_description(self) -> None:
        """Grow / shrink the description widget to match its content."""
        doc = self._description.document()
        viewport_width = self._description.viewport().width()
        if viewport_width > 0:
            doc.setTextWidth(viewport_width)
        height = max(40, int(doc.size().height()) + 4)
        self._description.setFixedHeight(height)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        # Description height depends on width; recompute on every
        # resize so wrapping changes shrink the height accordingly.
        self._refit_description()

    # ----------------------------------------------------------------------
    # Permissions widget
    # ----------------------------------------------------------------------

    def _build_permissions_widget(self) -> QWidget:
        """Permissions card: what this plugin asks to do, in plain language.

        Renders the manifest's ``declared_capabilities`` and
        ``declared_kinds``. The whole card hides when neither is set,
        so plugins that ask for nothing get no extra UI noise.
        """
        wrap = QFrame()
        wrap.setObjectName("permissions_card")
        wrap.setStyleSheet(
            "QFrame#permissions_card {"
            "  background: rgba(255, 255, 255, 0.04);"
            "  border: 1px solid rgba(255, 255, 255, 0.08);"
            "  border-radius: 8px;"
            "}"
            "QLabel#permissions_title {"
            "  color: #ddd;"
            "  font-size: 13px;"
            "  font-weight: 600;"
            "}"
            "QLabel#permissions_item {"
            "  color: #bbb;"
            "  font-size: 12px;"
            "}"
        )
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        title = QLabel("What this plugin can do")
        title.setObjectName("permissions_title")
        layout.addWidget(title)

        self._permissions_body = QVBoxLayout()
        self._permissions_body.setContentsMargins(0, 0, 0, 0)
        self._permissions_body.setSpacing(4)
        layout.addLayout(self._permissions_body)

        wrap.setVisible(False)
        return wrap

    def _refresh_permissions(self, listing: PluginListing) -> None:
        """Re-render the permissions card for the current listing.

        Clears whatever rows the previous listing left, then appends
        one row per declared capability and (when present) one row for
        the Nostr publish kinds. Hides the card when nothing is
        declared so plugins with no special permissions stay quiet.
        """
        while self._permissions_body.count():
            item = self._permissions_body.takeAt(0)
            child = item.widget() if item is not None else None
            if child is not None:
                child.deleteLater()

        rows: list[str] = []
        for cap in listing.declared_capabilities:
            label = CAPABILITY_LABELS.get(cap, cap)
            rows.append(label)
        if listing.declared_kinds:
            kinds = ", ".join(str(k) for k in listing.declared_kinds)
            rows.append(f"Publishes Nostr events (kinds: {kinds})")

        if not rows:
            self._permissions.setVisible(False)
            return

        for text in rows:
            row = QLabel(f"  -  {_escape(text)}")
            row.setObjectName("permissions_item")
            row.setWordWrap(True)
            self._permissions_body.addWidget(row)
        self._permissions.setVisible(True)

    # ----------------------------------------------------------------------
    # State transitions
    # ----------------------------------------------------------------------

    def show_listing(
        self,
        listing: Optional[PluginListing],
        *,
        installed: bool,
        has_update: bool,
        bundled: bool,
        installed_version: Optional[str] = None,
    ) -> None:
        """Render ``listing`` or show the empty placeholder if None."""
        self._listing = listing
        self._installed = installed
        self._has_update = has_update
        self._bundled = bundled
        self._busy = False
        self._progress.setVisible(False)

        if listing is None:
            self._set_state_empty()
            self.listing_changed.emit(None)
            return

        # Plugin selected: show everything, hide the placeholder.
        self._empty_label.setVisible(False)
        self._header_widget.setVisible(True)
        self._scroll.setVisible(True)
        self._description.setVisible(True)
        self._action_buttons_visible(True)

        self._icon_label.setPixmap(plugin_pixmap(
            plugin_id=listing.plugin_id,
            name=listing.name,
            icon_path=listing.icon_path,
            size=64,
        ))
        self._nostr_btn.setVisible(bool(listing.nostr_naddr))
        self._name.setText(_escape(listing.name))
        # Meta line is identity-only: version, who made it, what license,
        # where it came from, what it costs. Engagement signals (rating,
        # downloads, zaps, lightning address) live below in the social
        # panel so the header doesn't compete with that for attention.
        meta_bits = [
            f"version {_escape(listing.version)}",
        ]
        if installed_version and installed_version != listing.version:
            meta_bits.append(f"(installed: {_escape(installed_version)})")
        if listing.author:
            meta_bits.append(f"by {_escape(listing.author)}")
        if listing.license:
            meta_bits.append(_escape(listing.license))
        source_label = _format_source(listing.source_name)
        if source_label:
            meta_bits.append(source_label)
        if not listing.is_free:
            meta_bits.append(f"<b>{listing.price_sats} sats</b>")
        self._meta.setText(" · ".join(meta_bits))

        if listing.tags:
            self._tags.setText("Tags: " + ", ".join(_escape(t) for t in listing.tags))
            self._tags.setVisible(True)
        else:
            self._tags.setVisible(False)

        # Stats strip in the header reflects whatever we have right
        # now from the listing's cached aggregate. The marketplace
        # dialog updates it live via ``update_engagement_stats`` as
        # the snapshot recomputes.
        self._reset_header_stats(listing)

        body = listing.long_description or listing.description or "<i>No description.</i>"
        # The marketplace renders the long_description as HTML. The
        # registry is curated; rich-text injection from a malicious
        # entry is *not* a sandboxing concern because Qt's QTextBrowser
        # won't execute JavaScript.
        self._description.setHtml(body)

        self._refresh_permissions(listing)

        # Hand the listing to the social panel so it can render its
        # empty state. The marketplace dialog drives the live snapshot
        # updates via the engagement fetcher.
        self.listing_changed.emit(listing)

        self._refresh_action_buttons()

    def _reset_header_stats(self, listing: PluginListing) -> None:
        """Reset the stats strip when a new listing is selected.

        Shows the lightning tip chip iff the listing advertises one.
        Counts default to zero; the marketplace dialog refreshes them
        as soon as the engagement fetcher pushes a snapshot.
        """
        self._header_stars.set_rating(0.0)
        bits: list[str] = []
        # Show installs count if the listing carries one (registry
        # may surface a hint independent of the Nostr engagement set).
        installs = getattr(listing, "downloads", 0)
        if installs:
            bits.append(f"{format_count(installs)} install{'s' if installs != 1 else ''}")
        self._header_stats.setText(" · ".join(bits))
        if listing.lightning_address:
            self._header_tip_chip.set_address(listing.lightning_address)
            self._header_tip_chip.setVisible(True)
        else:
            self._header_tip_chip.setVisible(False)

    def set_stats_tooltip(self, tooltip: str) -> None:
        """Surface a hint (e.g. "filtered by Verified only") on hover.

        Empty string clears the tooltip. Lets the marketplace dialog
        explain why the histogram or counts are smaller than the raw
        cached totals.
        """
        self._header_stats.setToolTip(tooltip)

    def update_engagement_stats(self, snapshot: EngagementSnapshot) -> None:
        """Reflect a fresh aggregator snapshot in the fixed header.

        Called by the marketplace dialog whenever a new snapshot lands.
        Renders into the existing widgets without rebuilding any layout
        so the header stays layout-stable during live updates.
        """
        dist = snapshot.distribution
        self._header_stars.set_rating(dist.average)
        bits: list[str] = []
        if dist.total:
            bits.append(
                f"<b>{dist.average:.1f}</b> "
                f"({format_count(dist.total)} rating{'s' if dist.total != 1 else ''})"
            )
        if snapshot.comment_count:
            bits.append(
                f"{format_count(snapshot.comment_count)} "
                f"review{'s' if snapshot.comment_count != 1 else ''}"
            )
        if snapshot.zaps_sats:
            bits.append(f"{format_sats(snapshot.zaps_sats)} sats zapped")
        listing = self._listing
        if listing is not None and getattr(listing, "downloads", 0):
            installs = listing.downloads
            bits.append(f"{format_count(installs)} install{'s' if installs != 1 else ''}")
        self._header_stats.setText(" · ".join(bits) if bits else "Be the first to rate this plugin.")

        # Tip chip visibility tracks the current listing's lightning
        # address even mid-stats-update so we never leave ghost
        # spacing in the header row.
        if listing is not None and listing.lightning_address:
            self._header_tip_chip.set_address(listing.lightning_address)
            self._header_tip_chip.setVisible(True)
        else:
            self._header_tip_chip.setVisible(False)

    def set_busy(self, busy: bool, *, progress: float = 0.0) -> None:
        """Disable the action buttons while a worker is in flight."""
        self._busy = busy
        self._refresh_action_buttons()
        if busy:
            self._progress.setVisible(True)
            self._progress.setValue(int(max(0.0, min(1.0, progress)) * 100))
        else:
            self._progress.setVisible(False)

    # ----------------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------------

    def _set_state_empty(self) -> None:
        # Hide the whole header band + scrollable body so the placeholder
        # text is the only thing on screen. Cleaner than leaving an
        # empty header sliver above the centered hint.
        self._empty_label.setVisible(True)
        self._header_widget.setVisible(False)
        self._scroll.setVisible(False)
        self._social.clear()

    @property
    def social_panel(self) -> SocialPanel:
        """Expose the social panel so the marketplace dialog can wire
        its signals (rate, comment, delete, connect) to the controller."""
        return self._social

    def _action_buttons_visible(self, visible: bool) -> None:
        self._primary_btn.setVisible(visible)
        self._uninstall_btn.setVisible(visible)
        self._homepage_btn.setVisible(visible)

    def _refresh_action_buttons(self) -> None:
        listing = self._listing
        if listing is None:
            return
        self._homepage_btn.setEnabled(bool(listing.homepage) and not self._busy)

        if self._has_update:
            self._primary_btn.setText(f"Update to {listing.version}")
            self._primary_btn.setEnabled(not self._busy)
            self._uninstall_btn.setEnabled(not self._busy and not self._bundled)
            self._uninstall_btn.setVisible(not self._bundled)
        elif self._installed:
            self._primary_btn.setText("Installed")
            self._primary_btn.setEnabled(False)
            self._uninstall_btn.setEnabled(not self._busy and not self._bundled)
            self._uninstall_btn.setVisible(not self._bundled)
        else:
            self._primary_btn.setText("Install" if listing.is_free else f"Install ({listing.price_sats} sats)")
            self._primary_btn.setEnabled(not self._busy)
            self._uninstall_btn.setVisible(False)

    def _on_primary(self) -> None:
        if self._listing is None or self._busy:
            return
        self.install_requested.emit(self._listing)

    def _on_nostr(self) -> None:
        if self._listing is None or not self._listing.nostr_naddr:
            return
        self.nostr_open_requested.emit(self._listing.nostr_naddr)

    def _on_uninstall(self) -> None:
        if self._listing is None or self._busy or self._bundled:
            return
        self.uninstall_requested.emit(self._listing)

    def _on_homepage(self) -> None:
        if self._listing is None or not self._listing.homepage:
            return
        self.homepage_requested.emit(self._listing.homepage)


def _format_source(source_name: str) -> str:
    """Translate the raw source label into something humans read well.

    ``source_name`` is whatever the catalog or PluginInfo carried:
    a registry name for registry plugins, or one of "bundled" / "user"
    / "extra" for already-installed plugins.
    """
    if not source_name:
        return ""
    table = {
        "bundled": "Bundled with the editor",
        "user": "Installed locally",
        "extra": "Sideloaded",
    }
    if source_name in table:
        return table[source_name]
    return f"from {_escape(source_name)}"


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )
