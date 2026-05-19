"""Manage plugin registries.

A small modal that lets the user:

  - Toggle the default registry on/off without removing it.
  - Add additional registries by name + HTTPS URL.
  - Remove user-added registries (the default can be toggled but never removed).

Returns the new list of registries via ``result_sources()``. The
dialog itself never touches the settings store; the parent
(MarketplaceDialog) is responsible for persistence so the dialog can
be reused in tests without a live store.

Layout decisions worth knowing:

  - The list is scrollable. The inner ``QVBoxLayout`` ends with a
    ``addStretch`` so single-row cases don't get vertically expanded
    (which is what makes the checkbox visually "float" mid-row).
  - The Add Source affordance toggles between a button and an inline
    form, exclusive: never both on screen at once.
  - The form's primary action is the Add button (default + focus on
    the URL input). On macOS the focus ring drives the visual primary,
    so we set focus explicitly so Cancel doesn't steal the spotlight.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..models import RegistrySource
from ..registry import DEFAULT_REGISTRY


class SourcesDialog(QDialog):
    """Edit the list of plugin registries the marketplace fetches."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        sources: List[RegistrySource],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Plugin Sources")
        self.setMinimumSize(560, 380)

        self._rows: List[_SourceRow] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)

        intro = QLabel(
            "Sources are HTTPS registries that publish plugins. "
            "The official source ships with the editor. "
            "You can add more from communities you trust."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #aaa; font-size: 12px;")
        root.addWidget(intro)

        # Scrollable list of source rows. The crucial bit is the bottom
        # stretch we add to ``_list_layout`` so rows take their natural
        # height instead of being vertically expanded to fill the
        # scroll area.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        self._list_layout.addStretch(1)  # rows take natural size; tail absorbs extra space
        self._scroll.setWidget(self._list_widget)
        root.addWidget(self._scroll, 1)

        # Add Source toggle: button is visible when the form is hidden
        # and vice versa, so the user never sees both controls competing.
        self._add_btn = QPushButton("Add source…")
        self._add_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._add_btn.clicked.connect(self._show_add_form)
        add_row = QHBoxLayout()
        add_row.addWidget(self._add_btn)
        add_row.addStretch(1)
        self._add_btn_holder = QWidget()
        self._add_btn_holder.setLayout(add_row)
        root.addWidget(self._add_btn_holder)

        self._add_form = self._build_add_form()
        self._add_form.setVisible(False)
        root.addWidget(self._add_form)

        # Dialog OK / Cancel sit alone at the bottom. The Add Source
        # form has its own buttons (Cancel/Add) inside it, but they
        # never share screen space with these so there's no ambiguity.
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        for source in sources:
            self._add_row(source)

    # ----------------------------------------------------------------------
    def _build_add_form(self) -> QWidget:
        wrap = QFrame()
        wrap.setObjectName("add-form")
        wrap.setStyleSheet(
            "#add-form {"
            "  background: rgba(255,255,255,0.04);"
            "  border: 1px solid rgba(255,255,255,0.08);"
            "  border-radius: 8px;"
            "}"
        )
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        title = QLabel("Add a new source")
        title.setStyleSheet("font-weight: 600; font-size: 13px;")
        layout.addWidget(title)

        layout.addWidget(_form_label("Name"))
        self._add_name = QLineEdit()
        self._add_name.setPlaceholderText("Community registry")
        layout.addWidget(self._add_name)

        layout.addWidget(_form_label("HTTPS index URL"))
        self._add_url = QLineEdit()
        self._add_url.setPlaceholderText("https://example.com/plugins/index.json")
        # Enter inside either field commits the form. macOS expects the
        # focused input to drive a Return; without this users would have
        # to mouse to the button.
        self._add_name.returnPressed.connect(self._commit_add)
        self._add_url.returnPressed.connect(self._commit_add)
        layout.addWidget(self._add_url)

        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 0)
        row.addStretch(1)
        self._cancel_add_btn = QPushButton("Cancel")
        self._cancel_add_btn.setAutoDefault(False)
        self._cancel_add_btn.clicked.connect(self._hide_add_form)
        row.addWidget(self._cancel_add_btn)
        self._confirm_add_btn = QPushButton("Add")
        self._confirm_add_btn.setDefault(True)
        self._confirm_add_btn.setAutoDefault(True)
        self._confirm_add_btn.clicked.connect(self._commit_add)
        row.addWidget(self._confirm_add_btn)
        layout.addLayout(row)

        return wrap

    def _show_add_form(self) -> None:
        # Hide the "Add source" button entirely while the form is open;
        # leaving it visible was the source of the duplicate-action
        # confusion in the screenshot.
        self._add_btn_holder.setVisible(False)
        self._add_form.setVisible(True)
        # Focus the name input so Return commits Add, not Cancel. On
        # macOS this also moves the blue focus ring off Cancel.
        self._add_name.setFocus(Qt.OtherFocusReason)

    def _hide_add_form(self) -> None:
        self._add_form.setVisible(False)
        self._add_btn_holder.setVisible(True)
        self._add_name.clear()
        self._add_url.clear()

    def _commit_add(self) -> None:
        name = self._add_name.text().strip()
        url = self._add_url.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing name", "Please give the source a name.")
            self._add_name.setFocus()
            return
        if not url.lower().startswith("https://"):
            QMessageBox.warning(
                self, "HTTPS required",
                "Plugin sources must use HTTPS. Plain HTTP URLs are not "
                "accepted because plugin payloads run with full access "
                "to your files.",
            )
            self._add_url.setFocus()
            return
        if any(row.source.index_url == url for row in self._rows):
            QMessageBox.warning(self, "Already added", "That URL is already in the list.")
            return
        self._add_row(RegistrySource(name=name, index_url=url, enabled=True, trusted=False))
        self._hide_add_form()

    # ----------------------------------------------------------------------
    def _add_row(self, source: RegistrySource) -> None:
        row = _SourceRow(source, removable=source.index_url != DEFAULT_REGISTRY.index_url)
        row.remove_clicked.connect(lambda r=row: self._remove_row(r))
        # Always insert before the bottom stretch so rows stack from the top.
        insert_at = max(0, self._list_layout.count() - 1)
        self._list_layout.insertWidget(insert_at, row)
        self._rows.append(row)

    def _remove_row(self, row: "_SourceRow") -> None:
        if row not in self._rows:
            return
        self._rows.remove(row)
        self._list_layout.removeWidget(row)
        row.deleteLater()

    def result_sources(self) -> List[RegistrySource]:
        return [row.snapshot() for row in self._rows]


# ──────────────────────────────────────────────────────────────────────
# Source row
# ──────────────────────────────────────────────────────────────────────

class _SourceRow(QFrame):
    """One row in the sources list.

    Layout:
        ┌──────────────────────────────────────────────────────────┐
        │  [x]  Source name [OFFICIAL]              [Remove]       │
        │       https://example.com/index.json                     │
        └──────────────────────────────────────────────────────────┘

    Checkbox top-aligned so it pairs with the name visually; without
    that, ``Qt.AlignVCenter`` placed the checkbox between the two
    text lines and the row read as three separate stacked rows.
    """

    remove_clicked = Signal()

    def __init__(self, source: RegistrySource, *, removable: bool) -> None:
        super().__init__()
        self.source = source
        self.setObjectName("src-row")
        self.setStyleSheet(
            "#src-row {"
            "  background: rgba(255,255,255,0.04);"
            "  border-radius: 8px;"
            "}"
        )
        # Fix the row's vertical sizing so it never gets stretched when
        # the parent layout has extra height to give away.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        # Enable toggle. Top-aligned with the name line, no separate
        # label - the row IS the subject of the toggle.
        self._enabled = QCheckBox()
        self._enabled.setChecked(source.enabled)
        self._enabled.setToolTip(
            "Disable to skip this source on refresh; enable to include it again."
        )
        layout.addWidget(self._enabled, 0, Qt.AlignTop)

        # Center: name + url stacked.
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        name_label = QLabel(_escape(source.name))
        name_label.setStyleSheet("font-weight: 600; font-size: 13px;")
        name_row.addWidget(name_label, 0, Qt.AlignVCenter)

        if source.trusted or source.index_url == DEFAULT_REGISTRY.index_url:
            name_row.addWidget(_official_badge(), 0, Qt.AlignVCenter)

        name_row.addStretch(1)
        text_col.addLayout(name_row)

        url_label = QLabel(_escape(source.index_url))
        url_label.setStyleSheet("color: #888; font-size: 11px;")
        url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text_col.addWidget(url_label)

        layout.addLayout(text_col, 1)

        # Remove button (only on user-added rows).
        if removable:
            remove_btn = QPushButton("Remove")
            remove_btn.setCursor(Qt.PointingHandCursor)
            # Don't let Return on Remove accidentally close the dialog
            # via QDialog's default button propagation.
            remove_btn.setAutoDefault(False)
            remove_btn.clicked.connect(self.remove_clicked.emit)
            layout.addWidget(remove_btn, 0, Qt.AlignTop)

    def snapshot(self) -> RegistrySource:
        return RegistrySource(
            name=self.source.name,
            index_url=self.source.index_url,
            enabled=self._enabled.isChecked(),
            trusted=self.source.trusted,
        )


# ──────────────────────────────────────────────────────────────────────
# Small helpers
# ──────────────────────────────────────────────────────────────────────

def _official_badge() -> QLabel:
    """Filled pill that marks the bundled official registry.

    Filled background + border + fixed 18px height + 10px font is the
    combination that renders identically across macOS, Windows, and
    Linux Qt styles. Border-only / sub-10px QLabels render unreliably.
    """
    badge = QLabel("OFFICIAL")
    badge.setObjectName("official-badge")
    badge.setAlignment(Qt.AlignCenter)
    badge.setFixedHeight(18)
    badge.setStyleSheet(
        "#official-badge {"
        "  background-color: rgba(45,127,255,0.20);"
        "  color: #5fa0ff;"
        "  border: 1px solid rgba(45,127,255,0.55);"
        "  border-radius: 4px;"
        "  padding: 0 6px;"
        "  font-size: 10px;"
        "  font-weight: 700;"
        "  letter-spacing: 0.5px;"
        "}"
    )
    return badge


def _form_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet("color: #aaa; font-size: 11px;")
    return label


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )
