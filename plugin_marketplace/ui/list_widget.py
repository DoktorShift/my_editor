"""Listing widget: the master pane of the marketplace dialog.

Each entry is a custom row that shows name, author, short
description, a price badge, and an installed-state badge.
``selectionChanged`` is the signal the dialog wires up to drive the
detail pane on the right.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..models import PluginListing
from .icons import plugin_pixmap
from .stats import CompactStatsLine


class PluginListWidget(QListWidget):
    """List of plugins with rich rows.

    Emits ``listing_selected(PluginListing)`` whenever the user picks
    a row. Empty selection emits ``listing_selected(None)``.
    """

    listing_selected = Signal(object)  # PluginListing | None

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setUniformItemSizes(False)
        self.setSpacing(2)
        self.setFrameShape(QFrame.NoFrame)
        self.itemSelectionChanged.connect(self._on_selection_changed)

    # ----------------------------------------------------------------------
    def populate(
        self,
        listings: List[PluginListing],
        *,
        installed_ids: set[str],
        update_ids: set[str],
    ) -> None:
        """Replace the list contents.

        ``installed_ids`` and ``update_ids`` are pre-computed by the
        controller so each row can render its install state without
        having to ask the controller per row.
        """
        self.clear()
        for listing in listings:
            item = QListWidgetItem(self)
            row = _PluginRow(
                listing,
                installed=listing.plugin_id in installed_ids,
                has_update=listing.plugin_id in update_ids,
            )
            item.setSizeHint(row.sizeHint())
            # Stash the listing on the item so the host can read it
            # back without re-indexing.
            item.setData(Qt.UserRole, listing)
            self.addItem(item)
            self.setItemWidget(item, row)
        if listings:
            self.setCurrentRow(0)

    # ----------------------------------------------------------------------
    def selected_listing(self) -> Optional[PluginListing]:
        item = self.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def select_listing(self, listing: PluginListing) -> bool:
        """Move the selection to the row whose plugin_id matches.

        Returns ``True`` when a match was found and selected. Used by
        the author profile modal to jump the dialog to the plugin a
        review row was clicked on. We match on plugin_id rather than
        object identity because the catalog can be refreshed in the
        background and produce a fresh PluginListing for the same id.
        """
        for index in range(self.count()):
            item = self.item(index)
            if item is None:
                continue
            row_listing = item.data(Qt.UserRole)
            if isinstance(row_listing, PluginListing) and row_listing.plugin_id == listing.plugin_id:
                self.setCurrentItem(item)
                self.scrollToItem(item)
                return True
        return False

    def _on_selection_changed(self) -> None:
        self.listing_selected.emit(self.selected_listing())


# ──────────────────────────────────────────────────────────────────────
# Row widget
# ──────────────────────────────────────────────────────────────────────

class _PluginRow(QWidget):
    """One row in the plugin list.

    Layout: icon on the left, content (name/author/description) in the
    middle, badges right-aligned on the title line. The icon is fixed
    at 32px square so every row has a consistent left edge regardless
    of whether the plugin ships its own asset or falls back to the
    generated avatar.
    """

    _ICON_SIZE = 32

    def __init__(
        self,
        listing: PluginListing,
        *,
        installed: bool,
        has_update: bool,
    ) -> None:
        super().__init__()
        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(10)

        # Icon column
        icon_label = QLabel()
        icon_label.setFixedSize(self._ICON_SIZE, self._ICON_SIZE)
        icon_label.setPixmap(plugin_pixmap(
            plugin_id=listing.plugin_id,
            name=listing.name,
            icon_path=listing.icon_path,
            size=self._ICON_SIZE,
        ))
        outer.addWidget(icon_label, 0, Qt.AlignTop)

        # Text column
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        name = QLabel(f"<b>{_escape(listing.name)}</b>")
        name.setTextInteractionFlags(Qt.NoTextInteraction)
        title_row.addWidget(name)

        if listing.author:
            author = QLabel(f"<span style='color:#888'>by {_escape(listing.author)}</span>")
            author.setTextInteractionFlags(Qt.NoTextInteraction)
            title_row.addWidget(author)

        title_row.addStretch(1)

        # Right-side badges
        if has_update:
            title_row.addWidget(_chip("Update", color="#2d7fff"))
        elif installed:
            title_row.addWidget(_chip("Installed", color="#3aa14a"))

        if listing.is_free:
            title_row.addWidget(_chip("Free", color="#666"))
        else:
            title_row.addWidget(_chip(f"{listing.price_sats} sats", color="#b8860b"))

        text_col.addLayout(title_row)

        if listing.description:
            desc = QLabel(_escape(listing.description))
            desc.setWordWrap(True)
            desc.setStyleSheet("color: #aaa;")
            desc.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            text_col.addWidget(desc)

        # Compact metrics line: only renders when at least one metric is
        # non-zero, so plugins without ratings/installs/etc. still show
        # a clean two-line row (title + description).
        stats = CompactStatsLine()
        stats.populate(listing)
        text_col.addWidget(stats)

        outer.addLayout(text_col, 1)


def _chip(text: str, *, color: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(
        f"QLabel {{ "
        f"  background: rgba(255,255,255,0.06); "
        f"  color: {color}; "
        f"  border: 1px solid {color}; "
        f"  border-radius: 6px; "
        f"  padding: 1px 6px; "
        f"  font-size: 10px; "
        f"}}"
    )
    return label


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )
