"""Empty-state cards rendered when the plugin list has nothing to show.

Real product marketplaces (App Store, GNOME Software, VS Code) never
leave a blank panel - they explain what the section *is*, why it's
empty, and what to do next. This module ships a small set of pre-
built cards used by the dialog.

Each card is a ``QWidget`` with a centered icon glyph, a title, a
body, and zero-to-two action buttons. Cards emit ``action_clicked``
with the action's string id so the parent dialog can route the
behavior (refresh, switch section, open settings…).
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


# Action ids the dialog branches on. Strings rather than an Enum so a
# new card can introduce a new id without touching this module.
ACTION_REFRESH = "refresh"
ACTION_RETRY = "retry"
ACTION_CLEAR_FILTERS = "clear_filters"
ACTION_GO_DISCOVER = "go_discover"
ACTION_OPEN_LOG = "open_log"
ACTION_MANAGE_SOURCES = "manage_sources"


class EmptyStateCard(QWidget):
    """A centered icon + title + body + 0..2 buttons.

    Layout deliberately constrains its content to a column with a
    max-readable width, so the card looks intentional inside a wide
    panel instead of stretching its text edge-to-edge.
    """

    action_clicked = Signal(str)  # ACTION_*

    def __init__(
        self,
        *,
        icon: str,
        title: str,
        body: str,
        primary: Optional[tuple[str, str]] = None,    # (label, action_id)
        secondary: Optional[tuple[str, str]] = None,  # (label, action_id)
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        # Vertically centered, content limited to a comfortable width.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 48, 48, 48)
        outer.addStretch(1)

        wrapper = QFrame()
        wrapper.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        wrapper.setMaximumWidth(440)
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(12)
        wrapper_layout.setAlignment(Qt.AlignCenter)

        icon_label = QLabel(icon)
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setStyleSheet("font-size: 48px; color: #888;")
        wrapper_layout.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setWordWrap(True)
        title_label.setStyleSheet("font-size: 16px; font-weight: 600;")
        wrapper_layout.addWidget(title_label)

        body_label = QLabel(body)
        body_label.setAlignment(Qt.AlignCenter)
        body_label.setWordWrap(True)
        body_label.setStyleSheet("color: #888; font-size: 13px;")
        body_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        wrapper_layout.addWidget(body_label)

        if primary or secondary:
            btn_row = QHBoxLayout()
            btn_row.setSpacing(8)
            btn_row.addStretch(1)
            if secondary is not None:
                lbl, action_id = secondary
                btn = QPushButton(lbl)
                btn.clicked.connect(lambda *_a, a=action_id: self.action_clicked.emit(a))
                btn_row.addWidget(btn)
            if primary is not None:
                lbl, action_id = primary
                btn = QPushButton(lbl)
                btn.setDefault(True)
                btn.clicked.connect(lambda *_a, a=action_id: self.action_clicked.emit(a))
                btn_row.addWidget(btn)
            btn_row.addStretch(1)
            wrapper_layout.addLayout(btn_row)

        # Center the wrapper inside the outer column.
        center_row = QHBoxLayout()
        center_row.addStretch(1)
        center_row.addWidget(wrapper)
        center_row.addStretch(1)
        outer.addLayout(center_row)
        outer.addStretch(1)


# ──────────────────────────────────────────────────────────────────────
# Pre-built cards. Factories rather than constants so each call yields
# a fresh QWidget the dialog can free without holding shared state.
# ──────────────────────────────────────────────────────────────────────

def card_loading(message: str = "Fetching plugins…") -> EmptyStateCard:
    return EmptyStateCard(
        icon="⏳",
        title=message,
        body="Reaching out to your configured registries. This usually takes a moment.",
    )


def card_registry_unreachable(detail: str) -> EmptyStateCard:
    return EmptyStateCard(
        icon="📡",
        title="The plugin registry is unreachable",
        body=(
            f"{detail}\n\n"
            "You can still use bundled plugins. The marketplace will "
            "show plugins once the registry is reachable again."
        ),
        primary=("Try again", ACTION_RETRY),
        secondary=("Manage sources…", ACTION_MANAGE_SOURCES),
    )


def card_no_plugins_yet() -> EmptyStateCard:
    """Default registry returned 200 but advertised nothing - or no
    registry yet. Empty state from the *registry's* side."""
    return EmptyStateCard(
        icon="📦",
        title="No plugins published yet",
        body=(
            "The official registry is empty for now. As plugins are "
            "published you'll find them here."
        ),
        primary=("Refresh", ACTION_REFRESH),
        secondary=("Manage sources…", ACTION_MANAGE_SOURCES),
    )


def card_no_search_matches(query: str) -> EmptyStateCard:
    return EmptyStateCard(
        icon="🔍",
        title=f"No plugins match “{query}”",
        body="Try a different search, or clear the filters to see everything.",
        primary=("Clear filters", ACTION_CLEAR_FILTERS),
    )


def card_no_installed_plugins() -> EmptyStateCard:
    return EmptyStateCard(
        icon="📭",
        title="You haven't installed any plugins yet",
        body=(
            "Plugins add features without changing the editor itself - "
            "things like RSS import, wallet connections, and themes. "
            "Pick one from Discover to get started."
        ),
        primary=("Go to Discover", ACTION_GO_DISCOVER),
    )


def card_no_updates() -> EmptyStateCard:
    return EmptyStateCard(
        icon="✓",
        title="Everything is up to date",
        body="All of your installed plugins are running their latest version.",
        primary=("Check again", ACTION_REFRESH),
    )


def card_install_error_history() -> EmptyStateCard:
    """Used when the failure log has entries but the user is on Discover."""
    return EmptyStateCard(
        icon="⚠️",
        title="Some plugins failed to load",
        body="Open the plugin log to see what happened - you can fix or remove the offending plugin.",
        primary=("Open log", ACTION_OPEN_LOG),
    )
