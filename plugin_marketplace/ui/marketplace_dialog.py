"""The marketplace dialog.

The dialog is organised into three vertical bands:

  ┌─ Plugin Marketplace ─────────────────────────────────────────────┐
  │  Header strip                                                    │
  │  [ Discover | Installed (n) | Updates (n) ]            ⌕ search  │
  ├──────────────────────────────────────────────────────────────────┤
  │                                                                  │
  │  Section content (stacked widget):                               │
  │    Discover  -> filter chips + list + detail (or empty state)    │
  │    Installed -> list of installed plugins + detail               │
  │    Updates   -> list of plugins with an update + detail          │
  │                                                                  │
  ├──────────────────────────────────────────────────────────────────┤
  │  Footer:  "3 plugins . last refresh 2m ago"     [Sources] [↻]    │
  └──────────────────────────────────────────────────────────────────┘

The "content" middle band can show either a master-detail split
(when there are plugins to render) or a centered EmptyStateCard
(loading, no plugins published, no installed plugins, no matches for
the current filters). Section switching is instant - no network -
because the catalog is shared across sections.

All network I/O still lives on the QThreadPool workers used in the
previous version; this file only changes layout and state machinery.
"""

from __future__ import annotations

import dataclasses
import time
from enum import Enum
from typing import List, Optional

from PySide6.QtCore import QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from plugin_system import PluginSettingsStore

from ..controller import CatalogSnapshot, MarketplaceController
from ..installer import InstallResult
from ..models import CATEGORIES, PluginListing
from ..social import (
    AuthorReviewsFetcher,
    EngagementAggregator,
    EngagementSnapshot,
    NIP22_COMMENT_KIND,
    PluginAnchor,
    TrustPolicy,
)
from . import empty_state as es
from .author_profile_dialog import AuthorProfileDialog
from .detail_pane import DetailPane
from .list_widget import PluginListWidget
from .social_panel import (
    PUBLISH_BROADCASTING,
    PUBLISH_FAILED,
    PUBLISH_IDLE,
    PUBLISH_PUBLISHED,
    PUBLISH_SIGNING,
)
from .sources_dialog import SourcesDialog
from .workers import CatalogRefreshWorker, InstallWorker


# Magic "all categories" sentinel used by filter chips.
_ALL = "all"


@dataclasses.dataclass
class _FilterState:
    """Trust filter knobs owned by the marketplace dialog.

    The social panel renders the toggle chips and emits signals on
    change; the dialog owns the canonical state and feeds it into the
    aggregator's :class:`TrustPolicy`. Lifting the state out of the
    panel widget means a panel re-render never resets the user's
    filter selection and the dialog can build a policy without
    inspecting widget existence.
    """

    require_nip05: bool = False
    require_follow_graph: bool = False


class Section(str, Enum):
    """Top-level section the user is currently looking at.

    Each section reuses the same catalog snapshot but applies a
    different filter and renders a different empty state when its
    filter yields nothing.
    """
    DISCOVER = "discover"
    INSTALLED = "installed"
    UPDATES = "updates"


class MarketplaceDialog(QDialog):
    """Top-level marketplace UI.

    Owned by ``MainWindow``. Constructed lazily so the marketplace
    code path doesn't load until the user actually opens it.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Plugin Marketplace")
        self.setMinimumSize(QSize(900, 600))
        self.resize(1080, 680)

        # ``parent`` is the MainWindow; it satisfies MarketplaceHost
        # and exposes ``_plugin_settings`` and ``editor_show_status``.
        self._main_window = parent
        settings: PluginSettingsStore = parent._plugin_settings
        host = _MainWindowAdapter(parent)

        # Pick up the social stack the main window builds at startup,
        # if present. Tests that don't construct the full editor may
        # not have one; the controller treats the stack as optional
        # and the UI falls back to manifest-driven values cleanly.
        self._controller = MarketplaceController(
            host=host,
            settings=settings,
            engagement_cache=getattr(parent, "_engagement_cache", None),
            engagement_fetcher=getattr(parent, "_engagement_fetcher", None),
            engagement_publisher=getattr(parent, "_engagement_publisher", None),
            nip05_verifier=getattr(parent, "_nip05_verifier", None),
            commenter_profile_fetcher=getattr(parent, "_commenter_profile_fetcher", None),
            outbox_router=getattr(parent, "_marketplace_outbox_router", None),
        )
        self._thread_pool = QThreadPool.globalInstance()

        # UI state
        self._section: Section = Section.DISCOVER
        self._query: str = ""
        self._category: str = _ALL
        self._free_only: bool = False
        self._install_in_flight: bool = False
        self._fetch_in_flight: bool = False
        self._first_fetch_done: bool = False

        # Trust filter state lives on the dialog so the policy build
        # never depends on UI widget identity. The social panel emits
        # signals when its chips toggle; we mirror them here.
        self._filter_state = _FilterState()

        self._build_ui()
        self._refresh_async()

        # 30s auto-refresh timer: when the social panel is visible we
        # re-push the snapshot from cache + relays so newly-published
        # ratings/comments/zaps surface without the user having to
        # close and reopen the plugin. The fetcher's live subscription
        # already covers the happy path; the timer guards against
        # relays we silently dropped or whose backlog we missed.
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setInterval(30_000)
        self._auto_refresh_timer.timeout.connect(self._auto_refresh_tick)
        self._auto_refresh_timer.start()

    # ----------------------------------------------------------------------
    # UI scaffolding
    # ----------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(_hline())

        # Content area: a stacked widget so we can swap between the
        # "real" master-detail UI and a centered empty-state card.
        self._content_stack = QStackedWidget()
        self._content_stack.setContentsMargins(0, 0, 0, 0)

        self._master_detail = self._build_master_detail()
        self._empty_holder = QWidget()
        self._empty_holder_layout = QVBoxLayout(self._empty_holder)
        self._empty_holder_layout.setContentsMargins(0, 0, 0, 0)

        self._content_stack.addWidget(self._master_detail)  # index 0
        self._content_stack.addWidget(self._empty_holder)   # index 1
        root.addWidget(self._content_stack, 1)

        root.addWidget(_hline())
        root.addWidget(self._build_footer())

        # Keyboard shortcuts. Cmd/Ctrl-F focuses search; Esc clears it
        # so a user who hit search by accident can back out fast.
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(
            lambda: self._search.setFocus()
        )
        QShortcut(QKeySequence("Esc"), self._search).activated.connect(
            self._clear_search
        )

    # ----------------------------------------------------------------------
    def _build_header(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 12, 16, 8)
        layout.setSpacing(12)

        # Segmented section nav. Mutually exclusive checkable buttons
        # styled to feel like a NSSegmentedControl on macOS, the toggle
        # row in GNOME Software, and the section pivot in VS Code.
        self._section_group = QButtonGroup(self)
        self._section_group.setExclusive(True)

        self._btn_discover = self._make_section_btn("Discover", Section.DISCOVER)
        self._btn_installed = self._make_section_btn("Installed", Section.INSTALLED)
        self._btn_updates = self._make_section_btn("Updates", Section.UPDATES)
        self._btn_discover.setChecked(True)

        seg_holder = QFrame()
        seg_holder.setObjectName("seg-holder")
        seg_holder.setStyleSheet(
            "#seg-holder { background: rgba(255,255,255,0.04); border-radius: 8px; }"
        )
        seg_layout = QHBoxLayout(seg_holder)
        seg_layout.setContentsMargins(2, 2, 2, 2)
        seg_layout.setSpacing(0)
        seg_layout.addWidget(self._btn_discover)
        seg_layout.addWidget(self._btn_installed)
        seg_layout.addWidget(self._btn_updates)

        layout.addWidget(seg_holder)
        layout.addStretch(1)

        # Search input lives in the header, right-aligned. A compact
        # fixed width keeps the toolbar tidy.
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search plugins")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedWidth(260)
        self._search.textChanged.connect(self._on_search_changed)
        layout.addWidget(self._search)

        return bar

    def _make_section_btn(self, label: str, section: Section) -> QPushButton:
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumWidth(120)
        btn.setStyleSheet(
            "QPushButton {"
            "  background: transparent;"
            "  border: none;"
            "  color: #ccc;"
            "  padding: 6px 14px;"
            "  border-radius: 6px;"
            "  font-weight: 500;"
            "}"
            "QPushButton:hover {"
            "  color: #fff;"
            "}"
            "QPushButton:checked {"
            "  background: rgba(45,127,255,0.18);"
            "  color: #fff;"
            "}"
        )
        btn.clicked.connect(lambda *_a, s=section: self._select_section(s))
        self._section_group.addButton(btn)
        return btn

    # ----------------------------------------------------------------------
    def _build_master_detail(self) -> QWidget:
        wrap = QWidget()
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Filter strip lives above the split so it can affect just the
        # Discover section. We hide it for Installed/Updates because
        # those sections answer different questions.
        self._filter_strip = self._build_filter_strip()
        outer.addWidget(self._filter_strip)

        # Inline error panel for partial registry failures. Shown only
        # when at least one source threw an error.
        self._error_panel = _ErrorPanel()
        self._error_panel.retry_clicked.connect(self._refresh_async)
        self._error_panel.manage_clicked.connect(self._open_sources_dialog)
        self._error_panel.setVisible(False)
        outer.addWidget(self._error_panel)

        # Master + detail split.
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        self._list = PluginListWidget()
        self._list.listing_selected.connect(self._on_listing_selected)
        splitter.addWidget(self._list)

        self._detail = DetailPane()
        self._detail.install_requested.connect(self._on_install_requested)
        self._detail.uninstall_requested.connect(self._on_uninstall_requested)
        self._detail.homepage_requested.connect(self._on_homepage_requested)
        self._detail.nostr_open_requested.connect(self._on_nostr_open)
        self._detail.tip_address_copied.connect(self._on_tip_copied)
        self._detail.listing_changed.connect(self._on_detail_listing_changed)
        # Social panel signals: rate / comment / delete / connect-cta.
        social = self._detail.social_panel
        social.rate_requested.connect(self._on_rate)
        social.comment_requested.connect(self._on_comment)
        social.delete_requested.connect(self._on_delete)
        social.connect_clicked.connect(self._on_connect_with_nostr)
        social.tip_address_copied.connect(self._on_tip_copied)
        social.zap_requested.connect(self._on_zap_requested)
        social.redraw_requested.connect(self._redraw_social)
        social.verified_filter_changed.connect(self._on_verified_filter_changed)
        social.follow_filter_changed.connect(self._on_follow_filter_changed)
        social.mute_requested.connect(self._on_mute_requested)
        social.unmute_requested.connect(self._on_unmute_requested)
        social.profile_open_requested.connect(self._on_profile_open_requested)
        splitter.addWidget(self._detail)

        # Engagement subscription state: anchor currently being watched
        # so we can unwatch when the user navigates away.
        self._watched_anchor: Optional[PluginAnchor] = None
        if self._controller.engagement_fetcher is not None:
            self._controller.engagement_fetcher.snapshot_changed.connect(
                self._on_engagement_changed,
            )
        # Cross-plugin author-review fetcher powering the profile modal
        # opened from a reviewer's avatar. One instance lives for the
        # lifetime of the marketplace dialog so its session cache
        # persists across modal open/close cycles.
        relay_pool = getattr(self._main_window, "_relay_pool", None)
        self._author_reviews_fetcher: Optional[AuthorReviewsFetcher] = (
            AuthorReviewsFetcher(relay_pool=relay_pool, parent=self)
            if relay_pool is not None else None
        )
        # New commenter profile data triggers a redraw so display
        # names + verified badges update live.
        if self._controller.commenter_profile_fetcher is not None:
            self._controller.commenter_profile_fetcher.profile_updated.connect(
                self._on_profile_updated,
            )
        # Avatar downloads from the editor's existing batcher push
        # pixmaps into the social panel as they arrive.
        avatar_store = getattr(self._main_window, "_avatars", None)
        if avatar_store is not None and hasattr(avatar_store, "avatar_added"):
            try:
                avatar_store.avatar_added.connect(self._on_avatar_added)
            except (AttributeError, TypeError):
                pass
        # NIP-51 mute updates: any change re-pushes the snapshot so the
        # MUTED badge appears/disappears live.
        mute_cache = getattr(self._main_window, "_mute_list_cache", None)
        if mute_cache is not None and hasattr(mute_cache, "mute_list_updated"):
            try:
                mute_cache.mute_list_updated.connect(self._on_mute_list_updated)
            except (AttributeError, TypeError):
                pass
        # NIP-02 follow-trust updates: the "From people I follow" chip
        # changes effect when the active profile's follow set lands.
        follow_cache = getattr(self._main_window, "_follow_trust_cache", None)
        if follow_cache is not None and hasattr(follow_cache, "follow_set_updated"):
            try:
                follow_cache.follow_set_updated.connect(self._on_follow_set_updated)
            except (AttributeError, TypeError):
                pass

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([380, 700])
        outer.addWidget(splitter, 1)
        return wrap

    def _build_filter_strip(self) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("filter-strip")
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(6)

        self._chip_all = self._make_chip("All", checked=True)
        self._chip_all.clicked.connect(lambda: self._select_category(_ALL))
        layout.addWidget(self._chip_all)

        self._chip_free = self._make_chip("Free only")
        self._chip_free.clicked.connect(self._toggle_free)
        layout.addWidget(self._chip_free)

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Sunken)
        sep.setStyleSheet("color: rgba(255,255,255,0.12);")
        layout.addWidget(sep)

        self._category_chips: dict[str, QPushButton] = {}
        for cat in CATEGORIES:
            label = cat.replace("-", " ").title()
            btn = self._make_chip(label)
            btn.clicked.connect(lambda _c=False, c=cat: self._select_category(c))
            self._category_chips[cat] = btn
            layout.addWidget(btn)

        layout.addStretch(1)
        return wrap

    def _make_chip(self, text: str, *, checked: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setChecked(checked)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
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
        return btn

    # ----------------------------------------------------------------------
    def _build_footer(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 8, 16, 12)
        layout.setSpacing(8)

        self._footer_status = QLabel("")
        self._footer_status.setStyleSheet("color: #888; font-size: 12px;")
        self._footer_status.setWordWrap(False)
        layout.addWidget(self._footer_status, 1)

        self._sources_btn = QPushButton("Sources")
        self._sources_btn.setToolTip("Add or manage plugin registries")
        self._sources_btn.clicked.connect(self._open_sources_dialog)
        layout.addWidget(self._sources_btn)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setShortcut(QKeySequence("Ctrl+R"))
        self._refresh_btn.setToolTip("Refresh the plugin index (Ctrl+R)")
        self._refresh_btn.clicked.connect(self._refresh_async)
        layout.addWidget(self._refresh_btn)

        close = QPushButton("Done")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        return bar

    # ----------------------------------------------------------------------
    # Section / filter selection
    # ----------------------------------------------------------------------

    def _select_section(self, section: Section) -> None:
        self._section = section
        # Filter strip only makes sense for Discover.
        self._filter_strip.setVisible(section == Section.DISCOVER)
        self._render()

    def _on_search_changed(self, text: str) -> None:
        self._query = text
        self._render()

    def _clear_search(self) -> None:
        self._search.clear()

    def _select_category(self, category: str) -> None:
        self._category = category
        self._chip_all.setChecked(category == _ALL)
        for cat, btn in self._category_chips.items():
            btn.setChecked(cat == category)
        self._render()

    def _toggle_free(self) -> None:
        self._free_only = self._chip_free.isChecked()
        self._render()

    def _on_listing_selected(self, listing: Optional[PluginListing]) -> None:
        self._show_detail_for(listing)

    # ----------------------------------------------------------------------
    # Rendering
    # ----------------------------------------------------------------------

    def _render(self) -> None:
        """Single render path. Picks the visible listings for the current
        section + filters, swaps the content stack, refreshes counts."""
        catalog = self._controller.current_catalog()
        installed_ids = set(self._controller.installed_plugin_ids())

        listings = self._listings_for_section(catalog, installed_ids)

        update_ids = {
            li.plugin_id for li in catalog.listings
            if li.plugin_id in installed_ids and self._controller.has_update(li)
        }
        self._update_section_badges(installed_ids, update_ids)
        self._update_error_panel(catalog)

        empty_card = self._empty_card_for_state(catalog, listings, installed_ids)

        if empty_card is None:
            self._list.populate(
                listings,
                installed_ids=installed_ids,
                update_ids=update_ids,
            )
            self._content_stack.setCurrentWidget(self._master_detail)
            self._show_detail_for(self._list.selected_listing())
        else:
            self._set_empty_card(empty_card)
            self._content_stack.setCurrentWidget(self._empty_holder)

        self._update_footer(len(listings), len(catalog.listings))

    def _listings_for_section(
        self,
        catalog: CatalogSnapshot,
        installed_ids: set[str],
    ) -> List[PluginListing]:
        if self._section == Section.DISCOVER:
            return self._controller.filter_catalog(
                query=self._query,
                category=self._category if self._category != _ALL else None,
                free_only=self._free_only,
            )
        if self._section == Section.INSTALLED:
            # Controller reads each plugin's manifest.json so bundled
            # and sideloaded plugins render with full author/description
            # /tags/license metadata, not a placeholder line.
            return _apply_search(
                self._controller.installed_listings(),
                self._query,
            )
        if self._section == Section.UPDATES:
            updated = [
                li for li in catalog.listings
                if li.plugin_id in installed_ids
                and self._controller.has_update(li)
            ]
            return _apply_search(updated, self._query)
        return []

    def _empty_card_for_state(
        self,
        catalog: CatalogSnapshot,
        listings: List[PluginListing],
        installed_ids: set[str],
    ) -> Optional[QWidget]:
        """Decide which empty-state card (if any) to show.

        Returns ``None`` when the list has plugins to render. The
        ordering matters: loading state beats "no plugins" beats "no
        matches" so a slow first fetch doesn't flash a misleading
        empty-state.
        """
        if listings:
            return None

        if self._fetch_in_flight and not self._first_fetch_done:
            return _attach(es.card_loading(), self._handle_empty_action)

        # Filtered to nothing while the catalog itself has plugins:
        # this is a "no matches" empty state. It applies on Discover
        # (chip + search filters) and on Installed/Updates (search).
        catalog_has_plugins = bool(catalog.listings) or bool(installed_ids)
        is_filtered = bool(
            self._query
            or self._free_only
            or (self._section == Section.DISCOVER and self._category != _ALL)
        )
        if catalog_has_plugins and is_filtered:
            return _attach(es.card_no_search_matches(self._query or "your filters"),
                           self._handle_empty_action)

        if self._section == Section.INSTALLED:
            return _attach(es.card_no_installed_plugins(), self._handle_empty_action)
        if self._section == Section.UPDATES:
            return _attach(es.card_no_updates(), self._handle_empty_action)

        # Discover, no listings, no filters: distinguish "registry
        # unreachable" from "registry empty".
        if catalog.fetch_errors and not catalog.listings:
            first_err = next(iter(catalog.fetch_errors.values()))
            return _attach(es.card_registry_unreachable(first_err),
                           self._handle_empty_action)
        return _attach(es.card_no_plugins_yet(), self._handle_empty_action)

    def _set_empty_card(self, card: QWidget) -> None:
        # Remove anything currently in the holder, install the new card.
        while self._empty_holder_layout.count() > 0:
            item = self._empty_holder_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._empty_holder_layout.addWidget(card)

    def _handle_empty_action(self, action_id: str) -> None:
        if action_id == es.ACTION_REFRESH or action_id == es.ACTION_RETRY:
            self._refresh_async()
        elif action_id == es.ACTION_CLEAR_FILTERS:
            self._search.clear()
            self._free_only = False
            self._chip_free.setChecked(False)
            self._select_category(_ALL)
        elif action_id == es.ACTION_GO_DISCOVER:
            self._btn_discover.setChecked(True)
            self._select_section(Section.DISCOVER)
        elif action_id == es.ACTION_OPEN_LOG:
            self._main_window.open_plugin_log_dialog()
        elif action_id == es.ACTION_MANAGE_SOURCES:
            self._open_sources_dialog()

    def _show_detail_for(self, listing: Optional[PluginListing]) -> None:
        if listing is None:
            self._detail.show_listing(None, installed=False, has_update=False, bundled=False)
            return
        installed = self._controller.is_installed(listing.plugin_id)
        has_update = installed and self._controller.has_update(listing)
        bundled = self._controller.is_bundled(listing.plugin_id)
        self._detail.show_listing(
            listing,
            installed=installed,
            has_update=has_update,
            bundled=bundled,
            installed_version=self._controller.installed_version(listing.plugin_id),
        )

    def _update_section_badges(
        self,
        installed_ids: set[str],
        update_ids: set[str],
    ) -> None:
        self._btn_installed.setText(_with_count("Installed", len(installed_ids)))
        self._btn_updates.setText(_with_count("Updates", len(update_ids)))

    def _update_error_panel(self, catalog: CatalogSnapshot) -> None:
        # Only show the inline error panel on Discover; the other
        # sections answer different questions and we don't want to
        # nag the user about a flaky third-party registry there.
        if self._section != Section.DISCOVER:
            self._error_panel.setVisible(False)
            return
        if not catalog.fetch_errors or not catalog.listings:
            # Either no errors, or no listings to render at all (in
            # which case the empty-state card carries the message).
            self._error_panel.setVisible(False)
            return
        self._error_panel.set_errors(catalog.fetch_errors)
        self._error_panel.setVisible(True)

    def _update_footer(self, shown: int, total: int) -> None:
        bits: List[str] = []
        if self._fetch_in_flight and not self._first_fetch_done:
            bits.append("Fetching plugins...")
        else:
            if total == 0 and self._section == Section.DISCOVER:
                bits.append("No plugins available")
            elif shown == total:
                bits.append(f"{total} plugin{'s' if total != 1 else ''}")
            else:
                bits.append(f"{shown} of {total} plugins")
            if self._first_fetch_done:
                bits.append(_relative_time(self._controller.current_catalog().fetched_at))
        self._footer_status.setText("  .  ".join(bits))

    # ----------------------------------------------------------------------
    # Refresh worker
    # ----------------------------------------------------------------------

    def _refresh_async(self) -> None:
        if self._fetch_in_flight:
            return
        self._fetch_in_flight = True
        self._refresh_btn.setEnabled(False)
        self._render()
        worker = CatalogRefreshWorker(self._controller, signals_parent=self)
        worker.signals.finished.connect(self._on_refresh_finished)
        worker.signals.failed.connect(self._on_refresh_failed)
        self._thread_pool.start(worker)

    def _on_refresh_finished(self, _snapshot: CatalogSnapshot) -> None:
        self._fetch_in_flight = False
        self._first_fetch_done = True
        self._refresh_btn.setEnabled(True)
        self._render()

    def _on_refresh_failed(self, message: str) -> None:
        self._fetch_in_flight = False
        self._first_fetch_done = True
        self._refresh_btn.setEnabled(True)
        # Reuse the rendering pipeline: render will see fetch_errors and
        # surface the unreachable-registry card.
        self._render()

    # ----------------------------------------------------------------------
    # Install / Uninstall
    # ----------------------------------------------------------------------

    def _on_install_requested(self, listing: PluginListing) -> None:
        if self._install_in_flight:
            return
        if not self._confirm_install(listing):
            return
        if not listing.is_free:
            receipts = self._payment_receipts()
            if receipts is None or not receipts.has_paid_for(
                listing.plugin_id, listing.version,
            ):
                self._launch_paid_install(listing)
                return
        self._begin_install(listing)

    def _payment_receipts(self):
        """Return the editor-wide receipt store, or None for stub mains."""
        return getattr(self._main_window, "_payment_receipts", None)

    def _launch_paid_install(self, listing: PluginListing) -> None:
        """Open the paid-install dialog, then proceed on success."""
        from .paid_install_dialog import PaidInstallDialog
        receipts = self._payment_receipts()
        if receipts is None:
            QMessageBox.warning(
                self, "Paid install unavailable",
                "Paid plugin installation needs the payment receipt store. "
                "Update the editor and try again.",
            )
            return
        dlg = PaidInstallDialog(
            listing=listing, receipt_store=receipts, parent=self,
        )
        dlg.payment_confirmed.connect(lambda _receipt: self._begin_install(listing))
        dlg.exec()

    def _begin_install(self, listing: PluginListing) -> None:
        """Kick off the actual download + install worker."""
        self._install_in_flight = True
        self._detail.set_busy(True, progress=0.0)
        worker = InstallWorker(self._controller, listing, signals_parent=self)
        worker.signals.progress.connect(self._on_install_progress)
        worker.signals.finished.connect(self._on_install_finished)
        worker.signals.failed.connect(self._on_install_failed)
        self._thread_pool.start(worker)

    def _confirm_install(self, listing: PluginListing) -> bool:
        if listing.source_name and listing.source_name.startswith("my-editor"):
            return True  # default registry, no scary dialog
        return QMessageBox.question(
            self,
            "Install plugin?",
            (
                f"<b>{_escape(listing.name)}</b> comes from "
                f"<b>{_escape(listing.source_name) or 'an unknown registry'}</b>.\n\n"
                "Plugins run with full access to your editor and home folder. "
                "Only install plugins from sources you trust."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) == QMessageBox.Yes

    def _on_install_progress(self, fraction: float) -> None:
        self._detail.set_busy(True, progress=fraction)

    def _on_install_finished(self, result: InstallResult) -> None:
        try:
            self._controller.hot_reload(result.install_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self, "Plugin installed",
                f"Installed {result.listing.name}, but loading it failed:\n\n{exc}\n\n"
                "Restart the editor to try again.",
            )
        else:
            self._main_window.editor_show_status(
                f"Installed {result.listing.name} {result.listing.version}", 4500,
            )
        self._install_in_flight = False
        self._detail.set_busy(False)
        self._render()

    def _on_install_failed(self, message: str) -> None:
        self._install_in_flight = False
        self._detail.set_busy(False)
        QMessageBox.critical(self, "Install failed", message)

    def _on_uninstall_requested(self, listing: PluginListing) -> None:
        if self._install_in_flight:
            return
        confirmed = QMessageBox.question(
            self,
            "Uninstall plugin?",
            (
                f"Remove <b>{_escape(listing.name)}</b> and its files?\n\n"
                "Your settings for this plugin will be kept in case you "
                "reinstall it later."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) == QMessageBox.Yes
        if not confirmed:
            return
        try:
            self._controller.uninstall(listing.plugin_id)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Uninstall failed", str(exc))
            return
        self._main_window.editor_show_status(
            f"Uninstalled {listing.name}", 4500,
        )
        self._render()

    def _on_homepage_requested(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    # ----------------------------------------------------------------------
    # Social engagement lifecycle
    # ----------------------------------------------------------------------

    def _on_detail_listing_changed(self, listing: Optional[PluginListing]) -> None:
        """Selected plugin changed; rotate the engagement subscription.

        Drops the previous anchor's subscription and opens a new one
        for the freshly-selected plugin. When the new plugin has no
        Nostr listing we still call ``show_for`` so the panel renders
        its "no listing event" notice.
        """
        # Unwatch the previous anchor unconditionally so flicking
        # between plugins never accumulates dead subscriptions.
        if self._watched_anchor is not None and self._controller.engagement_fetcher is not None:
            self._controller.engagement_fetcher.unwatch(self._watched_anchor)
            self._watched_anchor = None

        if listing is None:
            self._detail.social_panel.clear()
            return

        anchor = self._controller.anchor_for(listing)
        signed_in = self._has_active_profile()
        viewer_name, viewer_picture, viewer_pixmap = self._viewer_profile_data()
        self._detail.social_panel.show_for(
            listing=listing, anchor=anchor, signed_in=signed_in,
            viewer_display_name=viewer_name,
            viewer_picture_url=viewer_picture,
            viewer_avatar_pixmap=viewer_pixmap,
        )

        if anchor is None:
            return  # nothing to subscribe to
        fetcher = self._controller.engagement_fetcher
        if fetcher is None:
            return
        fetcher.watch(anchor)
        self._watched_anchor = anchor
        # Proactively resolve the LNURL nostrPubkey so the zap receipt
        # validator can run in strict mode without waiting for the
        # user to open the zap dialog. Cached results are a no-op.
        self._prefetch_lnurl_pubkey(listing)
        # Push whatever's already in the cache so the panel renders
        # immediately without waiting for the first relay event.
        self._push_snapshot(anchor)

    def _prefetch_lnurl_pubkey(self, listing: PluginListing) -> None:
        """Resolve and cache the LNURL service's nostrPubkey.

        Used by the NIP-57 receipt parser to strict-validate the
        receipt signer. Skipped when:

          - the listing has no lightning address,
          - the cache already has a resolution for that address,
          - the engagement cache isn't available (test stubs).

        The HTTPS call runs on a QThreadPool worker so the dialog
        never blocks. We never raise from the worker — failure leaves
        receipt validation in loose mode, which is what callers
        already tolerate.
        """
        address = (listing.lightning_address or "").strip().lower()
        if not address:
            return
        cache = self._controller.engagement_cache
        if cache is None:
            return
        if cache.get_lnurl_pubkey(address):
            return
        worker = _LnurlPubkeyResolver(address, cache, signals_parent=self)
        self._thread_pool.start(worker)

    def _on_engagement_changed(self, anchor: PluginAnchor) -> None:
        """Fetcher signalled new events. Recompute + redraw if the
        change is for the plugin currently on screen.

        Coalesces bursts of events from a relay flood: a 150 ms delay
        before re-rendering lets multiple back-to-back events merge
        into a single snapshot push. Real users perceive 150 ms as
        instant; the busy plugin's panel stays smooth instead of
        thrashing on every individual event.
        """
        if self._watched_anchor != anchor:
            return
        if not hasattr(self, "_render_debounce"):
            self._render_debounce = QTimer(self)
            self._render_debounce.setSingleShot(True)
            self._render_debounce.setInterval(150)
            self._render_debounce.timeout.connect(self._flush_pending_render)
        self._pending_render_anchor = anchor
        if not self._render_debounce.isActive():
            self._render_debounce.start()

    def _flush_pending_render(self) -> None:
        anchor = getattr(self, "_pending_render_anchor", None)
        if anchor is None or anchor != self._watched_anchor:
            return
        self._pending_render_anchor = None
        self._push_snapshot(anchor)

    def _push_snapshot(self, anchor: PluginAnchor) -> None:
        """Build a fresh snapshot from the cache and hand it to the panel."""
        cache = self._controller.engagement_cache
        if cache is None:
            return
        # If the user signed in / out since the panel last opened the
        # plugin, re-issue ``show_for`` so the composer and chip set
        # reflect the new state without forcing a navigation round-trip.
        currently_signed_in = self._has_active_profile()
        last_signed_in = getattr(self, "_panel_signed_in", None)
        if last_signed_in is None or last_signed_in != currently_signed_in:
            listing_for_show = self._listing_for_anchor(anchor)
            if listing_for_show is not None:
                viewer_name, viewer_picture, viewer_pixmap = self._viewer_profile_data()
                self._detail.social_panel.show_for(
                    listing=listing_for_show, anchor=anchor,
                    signed_in=currently_signed_in,
                    viewer_display_name=viewer_name,
                    viewer_picture_url=viewer_picture,
                    viewer_avatar_pixmap=viewer_pixmap,
                )
            self._panel_signed_in = currently_signed_in
        listing = self._listing_for_anchor(anchor)
        expected_lnurl = None
        if listing is not None and listing.lightning_address:
            expected_lnurl = cache.get_lnurl_pubkey(listing.lightning_address)
        ratings, comments, zaps, deletions = cache.parsed_for_anchor(
            anchor, expected_lnurl_pubkey=expected_lnurl,
        )
        policy = self._build_trust_policy(anchor, cache)
        aggregator = EngagementAggregator(anchor=anchor)
        snapshot = aggregator.snapshot(
            ratings=ratings, comments=comments, zaps=zaps,
            deletions=deletions, policy=policy,
        )

        verified_view = {}
        profile_view: dict = {}
        verifier = self._controller.nip05_verifier

        # Collect every pubkey we touch so we can batch-fetch their
        # profile metadata in one subscription (rather than N).
        pubkeys: set[str] = set()
        for c in comments:
            pubkeys.add(c.author_pubkey)
        for r in ratings:
            pubkeys.add(r.author_pubkey)
        # Plugin author always shown in the AUTHOR badge surface.
        pubkeys.add(anchor.author_pubkey)

        for pubkey in pubkeys:
            profile = cache.get_profile(pubkey) or {}
            profile_view[pubkey] = dict(profile)
            nip05 = profile.get("nip05")
            if nip05 and verifier is not None:
                verifier.request(pubkey, nip05)
            cached = cache.get_nip05(pubkey, nip05) if nip05 else None
            verified_view[pubkey] = cached.verified if cached is not None else None

        # Trigger background fetches for any commenter we don't yet
        # have metadata for. ``request_many`` is cheap when everyone
        # is already cached (becomes a no-op).
        profile_fetcher = self._controller.commenter_profile_fetcher
        if profile_fetcher is not None:
            profile_fetcher.request_many(pubkeys)
        # Pre-warm NIP-65 routing info so the next snapshot push can
        # consult per-author write relays rather than falling back to
        # seeds. Cache-hits are no-ops. When a 10002 lands we re-push
        # the snapshot so the new routing takes effect immediately.
        router = self._controller.outbox_router
        if router is not None:
            unresolved = [pk for pk in pubkeys
                          if router._cached(pk) is None]   # noqa: SLF001
            if unresolved:
                self._detail.set_stats_tooltip(
                    "Fetching author relay preferences (NIP-65)…"
                )
            router.prewarm(
                pubkeys, seeds=self._seed_relays(),
                on_any_resolved=self._on_outbox_resolved,
            )

    def _on_outbox_resolved(self, _pubkey: str) -> None:
        """A previously-unresolved NIP-65 list just landed.

        Re-push the snapshot so the fetcher/publisher consult the new
        routing on the next interaction. Cheap: this just reads the
        existing cache rows.
        """
        if self._watched_anchor is None:
            return
        self._push_snapshot(self._watched_anchor)

        # Avatar pipeline: hand picture URLs to the editor's batched
        # avatar loader. Already-loaded pixmaps land via
        # ``_on_avatar_added`` and update the panel live.
        self._request_avatars(profile_view)

        # Tag each commenter's profile slot with their rating, if any.
        for rating in ratings:
            slot = profile_view.setdefault(rating.author_pubkey, {})
            slot["stars"] = rating.stars.value
        profile_view["__own__"] = {"event_ids": set(snapshot.own_comment_ids)}

        cache.store_snapshot(snapshot)
        self._last_snapshot = snapshot
        self._last_verified_view = verified_view
        self._last_profile_view = profile_view
        self._last_muted_view = policy.muted_pubkeys
        self._detail.update_engagement_stats(snapshot)
        # Tell the user why the counts may differ from the raw cache.
        tooltip_bits: list[str] = []
        if policy.require_nip05:
            tooltip_bits.append("Showing only NIP-05 verified contributors.")
        if policy.require_follow_graph:
            tooltip_bits.append("Showing only accounts you follow.")
        if policy.muted_pubkeys:
            tooltip_bits.append(
                f"Hiding {len(policy.muted_pubkeys)} muted contributor(s)."
            )
        self._detail.set_stats_tooltip(" ".join(tooltip_bits))
        self._detail.social_panel.update_snapshot(
            snapshot,
            verified_view=verified_view,
            profile_view=profile_view,
            muted_view=policy.muted_pubkeys,
        )

    def _request_avatars(self, profile_view: dict) -> None:
        """Schedule avatar downloads for everyone in the profile view."""
        batcher = getattr(self._main_window, "_avatar_batcher", None)
        store = getattr(self._main_window, "_avatars", None)
        if batcher is None or store is None:
            return
        for pubkey, profile in profile_view.items():
            if pubkey == "__own__":
                continue
            picture = profile.get("picture_url") if isinstance(profile, dict) else None
            if not picture:
                continue
            # Push the existing pixmap immediately if we already have it
            # cached locally — the batcher would no-op on repeat
            # requests so we'd otherwise never refresh the panel.
            existing = store.get(pubkey)
            if existing is not None and not existing.isNull():
                self._detail.social_panel.update_avatar(pubkey, existing)
                continue
            batcher.request(pubkey, picture)

    def _on_avatar_added(self, pubkey: str, pixmap) -> None:
        """Editor-wide avatar batcher just resolved one. Hand it to the
        social panel and re-render so cards update in place."""
        self._detail.social_panel.update_avatar(pubkey, pixmap)
        # If the avatar is for the active user, hot-update the composer
        # banner too.
        if pubkey == self._active_pubkey():
            self._detail.social_panel.update_viewer_profile(
                display_name=self._viewer_profile_data()[0],
                picture_url=self._viewer_profile_data()[1],
                avatar_pixmap=pixmap,
            )
        self._redraw_social()

    def _on_profile_updated(self, pubkey: str) -> None:
        """Kind:0 just landed for a pubkey we asked about. Re-render."""
        if self._watched_anchor is None:
            return
        self._push_snapshot(self._watched_anchor)

    def _redraw_social(self) -> None:
        """Re-push the latest snapshot (panel state changed, not data)."""
        if self._watched_anchor is None:
            return
        snapshot = getattr(self, "_last_snapshot", None)
        if snapshot is None:
            return
        self._detail.social_panel.update_snapshot(
            snapshot,
            verified_view=self._last_verified_view,
            profile_view=self._last_profile_view,
            muted_view=getattr(self, "_last_muted_view", None),
        )

    def _build_trust_policy(
        self,
        anchor: PluginAnchor,
        cache,
    ) -> TrustPolicy:
        """Trust policy applied to every snapshot.

        Honors three knobs the user controls from the social panel:

          - the muted-pubkey set (always applied, even when no other
            filters are on) sourced from the active profile's NIP-51
            list cache.
          - the verified-only chip (NIP-05) — when on, only NIP-05
            verified contributors survive.
          - the "from people I follow" chip (NIP-02) — when on, only
            authors in the viewer's contact list survive.

        The viewer's own pubkey is always whitelisted: a freshly-
        published rating should not disappear from the user's own view
        while NIP-05 verification or follow-graph propagation catches up.
        """
        viewer = self._active_pubkey()
        # Filter state is owned by the marketplace dialog (this object),
        # not the panel, so trust policy never depends on transient UI
        # widget identity. The panel emits chip-toggle signals; we
        # store the booleans here and consult them every snapshot.
        require_nip05 = bool(self._filter_state.require_nip05)
        require_follow = bool(self._filter_state.require_follow_graph)

        muted = frozenset(self._muted_pubkeys_for(viewer))
        verified = frozenset(cache.verified_pubkeys())
        followed = frozenset(self._followed_pubkeys_for(viewer))

        return TrustPolicy(
            muted_pubkeys=muted,
            require_nip05=require_nip05,
            require_follow_graph=require_follow,
            verified_pubkeys=verified,
            followed_pubkeys=followed,
            viewer_pubkey=viewer,
        )

    def _muted_pubkeys_for(self, viewer_pubkey: Optional[str]):
        """Return the muted pubkey set for the active profile.

        Reads from the editor-wide mute cache populated by the
        NIP-51 fetcher. Empty when no profile is connected or the
        list hasn't been fetched yet — both are safe defaults: a user
        without a list explicitly hasn't muted anyone.
        """
        if not viewer_pubkey:
            return frozenset()
        store = getattr(self._main_window, "_mute_list_cache", None)
        if store is None:
            return frozenset()
        return store.muted_pubkeys(viewer_pubkey)

    def _followed_pubkeys_for(self, viewer_pubkey: Optional[str]):
        """Return the followed pubkey set per NIP-02, keyed on the
        active profile so profile switches never leak follows between
        identities. Empty when the user has no contact list or hasn't
        fetched one yet.
        """
        if not viewer_pubkey:
            return frozenset()
        cache = getattr(self._main_window, "_follow_trust_cache", None)
        if cache is None:
            return frozenset()
        return cache.follows(viewer_pubkey)

    def _on_verified_filter_changed(self, enabled: bool) -> None:
        self._filter_state.require_nip05 = bool(enabled)
        if self._watched_anchor is None:
            return
        self._push_snapshot(self._watched_anchor)

    def _on_follow_filter_changed(self, enabled: bool) -> None:
        self._filter_state.require_follow_graph = bool(enabled)
        if self._watched_anchor is None:
            return
        self._push_snapshot(self._watched_anchor)

    def _on_mute_requested(self, pubkey: str) -> None:
        """Add ``pubkey`` to the active profile's NIP-51 mute list.

        Delegates to the editor-wide mute-list cache, which is also
        responsible for republishing the updated kind:10000 event so
        the change persists across devices.
        """
        if not pubkey:
            return
        profile = self._active_profile()
        if profile is None:
            QMessageBox.information(
                self, "Connect with Nostr to mute",
                "Muting a contributor publishes a NIP-51 mute list. "
                "Connect a signer from the Nostr menu and try again.",
            )
            return
        cache = getattr(self._main_window, "_mute_list_cache", None)
        if cache is None:
            self._main_window.editor_show_status("Mute list unavailable", 4000)
            return
        cache.mute(profile, pubkey, on_status=self._main_window.editor_show_status)
        if self._watched_anchor is not None:
            self._push_snapshot(self._watched_anchor)

    def _on_profile_open_requested(self, pubkey: str) -> None:
        """Open the in-app author profile modal for ``pubkey``.

        The modal centralises everything we know about a reviewer:
        identity (display name, NIP-05, npub) plus a one-shot fetch of
        their reviews across every plugin in the marketplace. From
        there the user can open the profile in their Nostr client, or
        follow / mute through the existing publisher pipeline.

        Falls back to opening the profile in a Nostr client only when
        the modal can't be constructed (e.g. no relay pool wired up).
        """
        if not pubkey:
            return
        if self._author_reviews_fetcher is None:
            self._open_profile_externally(pubkey)
            return

        profile = (self._last_profile_view or {}).get(pubkey, {}) if hasattr(
            self, "_last_profile_view",
        ) else {}
        verified_view = getattr(self, "_last_verified_view", {}) or {}
        muted_view = getattr(self, "_last_muted_view", frozenset()) or frozenset()

        avatar_store = getattr(self._main_window, "_avatars", None)
        avatar_pixmap = avatar_store.get(pubkey) if avatar_store is not None else None

        viewer = self._active_pubkey()
        is_self = viewer is not None and viewer == pubkey
        followed = self._followed_pubkeys_for(viewer)

        can_follow = getattr(self._main_window, "_follow_publisher", None) is not None
        dialog = AuthorProfileDialog(
            author_pubkey=pubkey,
            display_name=profile.get("display_name", ""),
            nip05_identifier=profile.get("nip05", ""),
            nip05_verified=verified_view.get(pubkey),
            avatar_pixmap=avatar_pixmap,
            reviews_fetcher=self._author_reviews_fetcher,
            listing_resolver=self._controller.listing_by_anchor,
            is_self=is_self,
            is_signed_in=self._has_active_profile(),
            is_muted=pubkey in muted_view,
            is_followed=pubkey in followed,
            can_follow=can_follow,
            parent=self,
        )
        dialog.mute_requested.connect(self._on_mute_requested)
        dialog.unmute_requested.connect(self._on_unmute_requested)
        dialog.follow_requested.connect(self._on_follow_from_profile)
        dialog.unfollow_requested.connect(self._on_unfollow_from_profile)
        dialog.plugin_open_requested.connect(self._on_plugin_open_from_profile)
        dialog.exec()

    def _open_profile_externally(self, pubkey: str) -> None:
        """Last-resort path when the in-app modal can't be built.

        Routes through ``nostr:`` so a registered Nostr client wins,
        falling back to njump.me's HTTPS renderer.
        """
        try:
            from nostr.bech32 import encode_npub
            npub = encode_npub(pubkey)
        except Exception:  # noqa: BLE001
            npub = pubkey
        nostr_uri = f"nostr:{npub}"
        if not QDesktopServices.openUrl(QUrl(nostr_uri)):
            QDesktopServices.openUrl(QUrl(f"https://njump.me/{npub}"))

    def _on_follow_from_profile(self, pubkey: str) -> None:
        """Follow a reviewer from the profile modal.

        We don't have an in-marketplace follow-publish path yet; for
        now we surface this as a status message and route through the
        editor's existing NIP-02 publisher when present. The dialog
        closes itself, so users get clear feedback either way.
        """
        publisher = getattr(self._main_window, "_follow_publisher", None)
        profile = self._active_profile()
        if publisher is None or profile is None:
            self._main_window.editor_show_status(
                "Following a reviewer needs a connected profile.", 4000,
            )
            return
        try:
            publisher.follow(profile, pubkey)
            self._main_window.editor_show_status("Follow request published.", 3000)
        except Exception as exc:  # noqa: BLE001
            self._main_window.editor_show_status(f"Follow failed: {exc}", 4000)

    def _on_unfollow_from_profile(self, pubkey: str) -> None:
        publisher = getattr(self._main_window, "_follow_publisher", None)
        profile = self._active_profile()
        if publisher is None or profile is None:
            self._main_window.editor_show_status(
                "Unfollowing a reviewer needs a connected profile.", 4000,
            )
            return
        try:
            publisher.unfollow(profile, pubkey)
            self._main_window.editor_show_status("Unfollow request published.", 3000)
        except Exception as exc:  # noqa: BLE001
            self._main_window.editor_show_status(f"Unfollow failed: {exc}", 4000)

    def _on_plugin_open_from_profile(self, target) -> None:
        """A review-row click bubbled up from the profile modal.

        Receives either a ``PluginListing`` (catalog hit) or a
        ``PluginAnchor`` (catalog miss). For catalog hits we jump the
        master list to that plugin; for misses we surface a brief
        status hint so users understand why nothing changed on-screen.
        """
        if isinstance(target, PluginListing):
            self._list.select_listing(target)
            return
        # Unknown plugin: open its Nostr coord externally so power
        # users can still reach the listing event.
        if isinstance(target, PluginAnchor):
            self._main_window.editor_show_status(
                "This plugin is not in your local registry.", 4000,
            )

    def _on_mute_list_updated(self, owner_pubkey: str) -> None:
        """Mute-list cache observed a change; re-push the snapshot
        so the social panel reflects the new MUTED badges."""
        if self._watched_anchor is None:
            return
        if owner_pubkey != self._active_pubkey():
            return
        self._push_snapshot(self._watched_anchor)

    def _on_follow_set_updated(self, owner_pubkey: str) -> None:
        """Follow-trust cache observed a change; re-push the snapshot
        so the 'From people I follow' filter reflects the new set."""
        if self._watched_anchor is None:
            return
        if owner_pubkey != self._active_pubkey():
            return
        self._push_snapshot(self._watched_anchor)

    def _on_unmute_requested(self, pubkey: str) -> None:
        if not pubkey:
            return
        profile = self._active_profile()
        if profile is None:
            return
        cache = getattr(self._main_window, "_mute_list_cache", None)
        if cache is None:
            return
        cache.unmute(profile, pubkey, on_status=self._main_window.editor_show_status)
        if self._watched_anchor is not None:
            self._push_snapshot(self._watched_anchor)

    def _has_active_profile(self) -> bool:
        store = getattr(self._main_window, "_profile_store", None)
        return store is not None and store.default() is not None

    def _active_pubkey(self) -> Optional[str]:
        store = getattr(self._main_window, "_profile_store", None)
        profile = store.default() if store is not None else None
        return profile.user_pubkey if profile is not None else None

    def _active_profile(self):
        store = getattr(self._main_window, "_profile_store", None)
        return store.default() if store is not None else None

    def _viewer_profile_data(self):
        """Best-effort (display_name, picture_url, avatar_pixmap) for
        the signed-in user, drawn from the profile store first, the
        engagement cache second.

        Returns empty strings / None when no profile is connected so
        callers can show generic placeholders without branching."""
        profile = self._active_profile()
        if profile is None:
            return "", "", None
        display_name = (getattr(profile, "display_name", "") or "").strip()
        picture = (getattr(profile, "picture", "") or "").strip()
        # Profile store may not have the kind:0 fetched yet — fall back
        # to the engagement cache where we mirror profile metadata.
        cache = self._controller.engagement_cache
        if cache is not None:
            cached = cache.get_profile(profile.user_pubkey) or {}
            display_name = display_name or cached.get("display_name", "")
            picture = picture or cached.get("picture_url", "")
        avatar = None
        store = getattr(self._main_window, "_avatars", None)
        if store is not None:
            avatar = store.get(profile.user_pubkey)
            if avatar is None and picture:
                batcher = getattr(self._main_window, "_avatar_batcher", None)
                if batcher is not None:
                    batcher.request(profile.user_pubkey, picture)
        return display_name, picture, avatar

    # ----------------------------------------------------------------------
    # Social writes
    # ----------------------------------------------------------------------

    def _on_rate(self, stars: int) -> None:
        if self._watched_anchor is None:
            return
        publisher = self._controller.engagement_publisher
        profile = self._active_profile()
        if publisher is None or profile is None:
            return
        job = publisher.rate(
            profile=profile, anchor=self._watched_anchor, stars=stars,
        )
        # Ratings publish silently from the composer's perspective —
        # the active rating glyph already flips when the user clicks.
        # We surface the outcome in the status bar so the user knows
        # it actually landed.
        job.failed.connect(lambda reason: self._main_window.editor_show_status(
            f"Rating failed: {reason}", 5000,
        ))
        job.published.connect(lambda *_: self._main_window.editor_show_status(
            "Rating published", 3500,
        ))

    def _on_comment(self, parent_event_id: str, body: str) -> None:
        if self._watched_anchor is None:
            return
        publisher = self._controller.engagement_publisher
        profile = self._active_profile()
        if publisher is None or profile is None:
            return
        panel = self._detail.social_panel
        # Optimistic UI: mark the composer as signing so the button +
        # textarea disable, the user sees a "Signing…" status, and
        # double-clicks become no-ops.
        panel.set_publish_state(PUBLISH_SIGNING, "Signing with your remote signer…")
        job = publisher.comment(
            profile=profile, anchor=self._watched_anchor, body=body,
            parent_event_id=parent_event_id or None,
        )
        job.publishing.connect(
            lambda: panel.set_publish_state(PUBLISH_BROADCASTING, "Broadcasting to relays…")
        )

        def _on_published(_event, results) -> None:
            accepted = sum(1 for _u, ok, _msg in (results or []) if ok)
            if accepted == 0:
                panel.set_publish_state(
                    PUBLISH_FAILED,
                    "Published, but no relay confirmed receipt. Try again.",
                )
                return
            panel.set_publish_state(
                PUBLISH_PUBLISHED,
                f"Published to {accepted} relay{'s' if accepted != 1 else ''}.",
            )
            self._main_window.editor_show_status(
                f"Comment published to {accepted} relay{'s' if accepted != 1 else ''}",
                3500,
            )

        def _on_failed(reason: str) -> None:
            panel.set_publish_state(PUBLISH_FAILED, f"Publish failed: {reason}")
            self._main_window.editor_show_status(f"Comment failed: {reason}", 5000)

        job.published.connect(_on_published)
        job.failed.connect(_on_failed)

    def _on_delete(self, event_id: str) -> None:
        if self._watched_anchor is None:
            return
        publisher = self._controller.engagement_publisher
        profile = self._active_profile()
        if publisher is None or profile is None:
            return
        target_kind = self._target_kind_for_event(self._watched_anchor, event_id)
        confirmed = QMessageBox.question(
            self, "Delete this contribution?",
            "Publish a Nostr deletion request for this comment? "
            "Relays may take a moment to remove the original event.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        ) == QMessageBox.Yes
        if not confirmed:
            return
        job = publisher.delete(
            profile=profile, anchor=self._watched_anchor,
            event_id=event_id, target_kind=target_kind,
        )
        job.failed.connect(lambda reason: self._main_window.editor_show_status(
            f"Delete failed: {reason}", 5000,
        ))
        job.published.connect(lambda *_: self._main_window.editor_show_status(
            "Deletion request published", 3500,
        ))

    def _target_kind_for_event(self, anchor: PluginAnchor, event_id: str) -> int:
        """Resolve the kind of one cached event so the NIP-09 advisory
        ``k`` tag matches the target. Falls back to the comment kind
        because anything the UI surfaces a Delete button for is one
        of the author's own rating / comment events."""
        cache = self._controller.engagement_cache
        if cache is None:
            return NIP22_COMMENT_KIND
        kind = cache.event_kind(anchor, event_id)
        return kind if kind is not None else NIP22_COMMENT_KIND

    def _on_zap_requested(self, recipient_pubkey: str) -> None:
        """Open the zap dialog for the requested recipient.

        ``recipient_pubkey`` is either the plugin author's key (for
        the plugin-header zap button) or a commenter's (for per-card
        zaps in M5.5+). The dialog handles every error path inline,
        including the "no Nostr signer connected" case, so we keep
        this handler tiny.
        """
        if self._watched_anchor is None:
            return
        listing = self._listing_for_anchor(self._watched_anchor)
        if listing is None or not listing.lightning_address:
            QMessageBox.information(
                self, "No lightning address",
                "This plugin doesn't advertise a lightning address yet, "
                "so we can't route a zap to its author.",
            )
            return
        profile = self._active_profile()
        if profile is None:
            QMessageBox.information(
                self, "Connect with Nostr to zap",
                "Zaps need a Nostr profile to sign the request. "
                "Connect a signer from the Nostr menu and try again.",
            )
            return

        from .zap_dialog import ZapDialog
        # Resolve the bunker client synchronously when already connected;
        # if not connected the ZapDialog surfaces the error inline.
        session_pool = self._main_window._session_pool

        def _open_with(client) -> None:
            avatar = None
            store = getattr(self._main_window, "_avatars", None)
            if store is not None:
                avatar = store.get(self._watched_anchor.author_pubkey)
            display_name = (listing.author or _short_pubkey(self._watched_anchor.author_pubkey))
            dlg = ZapDialog(
                parent_window=self._main_window,
                anchor=self._watched_anchor,
                recipient_display_name=display_name,
                recipient_avatar_pixmap=avatar,
                lightning_address=listing.lightning_address,
                bunker_client=client,
                active_pubkey=profile.user_pubkey,
                engagement_fetcher=self._controller.engagement_fetcher,
                cache=self._controller.engagement_cache,
                parent=self,
            )
            dlg.exec()

        def _open_failed(reason: str) -> None:
            QMessageBox.warning(
                self, "Signer unreachable",
                f"Couldn't reach your Nostr signer: {reason}",
            )

        session_pool.get(profile, _open_with, _open_failed)

    def _auto_refresh_tick(self) -> None:
        """30s heartbeat: re-push the social snapshot when visible.

        Cheap: ``_push_snapshot`` recomputes from the existing local
        cache. We deliberately do NOT tear down and re-open the
        engagement subscription on each tick — that churns
        WebSockets and can trigger rate limits on busy relays. The
        live subscription already streams new events; the tick only
        catches local cache invalidations and any stats drift.
        """
        if not self.isVisible() or self._watched_anchor is None:
            return
        self._push_snapshot(self._watched_anchor)

    def _seed_relays(self):
        """The seed relay set the outbox router uses as a backstop.

        Single source of truth: the editor's main window exposes
        ``_marketplace_publish_seeds()`` which carries DEFAULT_RELAYS
        plus any user-curated extras the broader app has agreed on.
        """
        helper = getattr(self._main_window, "_marketplace_publish_seeds", None)
        if callable(helper):
            try:
                return list(helper())
            except Exception:  # noqa: BLE001
                pass
        from nostr import DEFAULT_RELAYS
        return list(DEFAULT_RELAYS)

    def _listing_for_anchor(self, anchor: PluginAnchor):
        return self._controller.listing_by_anchor(anchor)

    def _on_connect_with_nostr(self) -> None:
        """Anonymous user pressed the Connect CTA in the social panel."""
        # The existing Nostr menu entry handles the bunker connect
        # flow. We try to invoke it directly if the host exposes it;
        # otherwise show a hint.
        connect = getattr(self._main_window, "_on_nostr_connect", None)
        if callable(connect):
            connect()
        else:
            QMessageBox.information(
                self, "Connect with Nostr",
                "Open the Nostr menu in the editor to connect a signer profile.",
            )

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Drop any open engagement subscription on dialog close."""
        if self._watched_anchor is not None and self._controller.engagement_fetcher is not None:
            self._controller.engagement_fetcher.unwatch(self._watched_anchor)
            self._watched_anchor = None
        super().closeEvent(event)

    def _on_tip_copied(self, address: str) -> None:
        # Subtle confirmation in the main editor status bar so the user
        # has a second signal beyond the button label flash. Helpful
        # when the dialog is wider than the screen and the chip is off
        # in the corner.
        self._main_window.editor_show_status(
            f"Copied lightning address {address} to clipboard", 4500,
        )

    def _on_nostr_open(self, naddr: str) -> None:
        """Open a Nostr coordinate in the user's web client of choice.

        We use the ``nostr:`` URI scheme so the OS's registered handler
        picks it up (e.g. Snort, Damus, NoStrudel on macOS). Falls back
        to a public web client if no handler is registered.
        """
        nostr_uri = naddr if naddr.startswith("nostr:") else f"nostr:{naddr}"
        if not QDesktopServices.openUrl(QUrl(nostr_uri)):
            QDesktopServices.openUrl(QUrl(f"https://njump.me/{naddr}"))

    # ----------------------------------------------------------------------
    # Sources dialog
    # ----------------------------------------------------------------------

    def _open_sources_dialog(self) -> None:
        sources_before = self._controller.registry_sources()
        dlg = SourcesDialog(self, sources=sources_before)
        if dlg.exec() == QDialog.Accepted:
            self._controller.set_registry_sources(dlg.result_sources())
            self._refresh_async()


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Plain)
    line.setStyleSheet("color: rgba(255,255,255,0.08);")
    return line


def _with_count(label: str, n: int) -> str:
    if n <= 0:
        return label
    return f"{label}  ({n})"


def _relative_time(epoch: float) -> str:
    if epoch <= 0:
        return ""
    delta = max(0, int(time.time() - epoch))
    if delta < 60:
        return "updated just now"
    if delta < 3600:
        minutes = delta // 60
        return f"updated {minutes} min ago"
    hours = delta // 3600
    return f"updated {hours} h ago"


def _apply_search(listings: List[PluginListing], query: str) -> List[PluginListing]:
    q = query.strip().lower()
    if not q:
        return list(listings)
    out: List[PluginListing] = []
    for li in listings:
        hay = " ".join((
            li.plugin_id, li.name, li.author, li.description,
            " ".join(li.tags),
        )).lower()
        if q in hay:
            out.append(li)
    return out


def _attach(card, handler):
    """Wire an EmptyStateCard's action signal to a handler."""
    card.action_clicked.connect(handler)
    return card


def _short_pubkey(pubkey: str) -> str:
    if not pubkey or len(pubkey) < 12:
        return pubkey
    return f"{pubkey[:6]}…{pubkey[-4:]}"


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


# ──────────────────────────────────────────────────────────────────────
# Inline error panel (above the list when partial source failures)
# ──────────────────────────────────────────────────────────────────────

class _ErrorPanel(QFrame):
    """A subtle banner shown when one or more registries failed but
    at least one succeeded. Less aggressive than a modal."""

    retry_clicked = Signal()
    manage_clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("err-panel")
        self.setStyleSheet(
            "#err-panel {"
            "  background: rgba(220, 80, 80, 0.08);"
            "  border: 1px solid rgba(220, 80, 80, 0.30);"
            "  border-radius: 6px;"
            "}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        self._icon = QLabel("!")
        self._icon.setFixedWidth(20)
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setStyleSheet("color: #e98484; font-weight: 700; font-size: 14px;")
        layout.addWidget(self._icon)

        self._msg = QLabel("")
        self._msg.setWordWrap(True)
        self._msg.setStyleSheet("color: #ddd; font-size: 12px;")
        layout.addWidget(self._msg, 1)

        retry = QPushButton("Try again")
        retry.clicked.connect(self.retry_clicked.emit)
        layout.addWidget(retry)

        manage = QPushButton("Sources")
        manage.clicked.connect(self.manage_clicked.emit)
        layout.addWidget(manage)

    def set_errors(self, errors: dict[str, str]) -> None:
        if len(errors) == 1:
            name, msg = next(iter(errors.items()))
            self._msg.setText(f"<b>{_escape(name)}:</b> {_escape(msg)}")
        else:
            lines = [
                f"<b>{_escape(name)}:</b> {_escape(msg)}"
                for name, msg in errors.items()
            ]
            self._msg.setText("<br>".join(lines))


# ──────────────────────────────────────────────────────────────────────
# Host adapter
# ──────────────────────────────────────────────────────────────────────

class _MainWindowAdapter:
    """Glue between ``MarketplaceHost`` and ``MainWindow``."""

    def __init__(self, main_window) -> None:
        self._mw = main_window

    def reload_plugin(self, folder):
        self._mw.reload_plugin(folder)

    def unload_plugin(self, plugin_id: str) -> bool:
        return self._mw.unload_plugin(plugin_id)

    @property
    def loaded_plugins(self):
        return list(self._mw._plugin_report.loaded)


# ──────────────────────────────────────────────────────────────────────
# LNURL nostrPubkey resolver (background worker)
# ──────────────────────────────────────────────────────────────────────

from PySide6.QtCore import QRunnable as _QRunnable  # noqa: E402


class _LnurlPubkeyResolver(_QRunnable):
    """Resolve a lightning address's nostrPubkey and cache it.

    Runs on the dialog's QThreadPool. We don't surface success or
    failure — the value lands in the engagement cache and the next
    snapshot push picks it up. A failure leaves the address
    unresolved, which means strict-mode receipt validation stays off
    for it; that's a documented graceful degradation, not silent
    failure (the audit's amount-less rejection still rules out the
    most dangerous spoofs).
    """

    def __init__(self, lightning_address: str, cache, *, signals_parent=None) -> None:
        super().__init__()
        self._address = lightning_address
        self._cache = cache
        # ``signals_parent`` is unused but kept for API symmetry with
        # the other workers; lets future code attach progress signals
        # without breaking the constructor contract.
        del signals_parent

    def run(self) -> None:  # noqa: D401
        try:
            from ..social.lnurl import (
                LnurlError,
                fetch_lnurl_pay_data,
                lightning_address_to_lnurl_endpoint,
            )
            endpoint = lightning_address_to_lnurl_endpoint(self._address)
            data = fetch_lnurl_pay_data(endpoint)
        except (LnurlError, Exception):  # noqa: BLE001 — best-effort
            return
        if data.allows_nostr and data.nostr_pubkey:
            try:
                self._cache.store_lnurl_pubkey(self._address, data.nostr_pubkey)
            except Exception:  # noqa: BLE001
                # Cache write failures are non-fatal here; the receipt
                # parser tolerates a missing resolution.
                pass
