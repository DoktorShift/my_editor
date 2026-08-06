#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Page Setup dialog for PDF export.

A small custom dialog instead of QPageSetupDialog: Qt's native one
requires a valid printer and rejects immediately on Linux systems
without CUPS, while this one works everywhere (including offscreen
in tests) and persists to ~/.config/my_editor/page_setup.json.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from export_pdf import PAGE_SIZES, load_page_setup, save_page_setup

_DARK_CSS = """
QDialog { background: #1E1E1E; }
QLabel { color: #D4D4D4; font-size: 12px; }
QLabel#page_setup_title { color: #FFFFFF; font-size: 16px; font-weight: 600; }
QComboBox, QDoubleSpinBox {
    background: #2D2D30;
    color: #D4D4D4;
    border: 1px solid #3C3C3C;
    border-radius: 4px;
    padding: 4px 8px;
    min-width: 140px;
}
QComboBox QAbstractItemView {
    background: #252526;
    color: #D4D4D4;
    selection-background-color: #264F78;
}
QPushButton {
    background: #2D2D30;
    color: #D4D4D4;
    border: 1px solid #3C3C3C;
    padding: 6px 14px;
    border-radius: 4px;
    min-width: 86px;
}
QPushButton:hover { background: #3C3C3C; }
QPushButton:pressed { background: #1E1E1E; }
QPushButton:default { background: #007ACC; color: #FFFFFF; border-color: #1177C7; }
QPushButton:default:hover { background: #1177C7; }
"""

_LIGHT_CSS = """
QDialog { background: #FFFFFF; }
QLabel { color: #333333; font-size: 12px; }
QLabel#page_setup_title { color: #1A1A1A; font-size: 16px; font-weight: 600; }
QComboBox, QDoubleSpinBox {
    background: #FFFFFF;
    color: #333333;
    border: 1px solid #C5C5C5;
    border-radius: 4px;
    padding: 4px 8px;
    min-width: 140px;
}
QComboBox QAbstractItemView {
    background: #FFFFFF;
    color: #333333;
    selection-background-color: #CCE4F7;
}
QPushButton {
    background: #F3F3F3;
    color: #333333;
    border: 1px solid #C5C5C5;
    padding: 6px 14px;
    border-radius: 4px;
    min-width: 86px;
}
QPushButton:hover { background: #E5E5E5; }
QPushButton:pressed { background: #D5D5D5; }
QPushButton:default { background: #0078D4; color: #FFFFFF; border-color: #106EBE; }
QPushButton:default:hover { background: #106EBE; }
"""


class PageSetupDialog(QDialog):
    """Edit the persisted PDF page setup (size, orientation, margins)."""

    def __init__(self, is_dark: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Page Setup")
        self.setModal(True)
        self.setStyleSheet(_DARK_CSS if is_dark else _LIGHT_CSS)

        setup = load_page_setup()

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        title = QLabel("Page Setup")
        title.setObjectName("page_setup_title")
        root.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)

        self._size_combo = QComboBox()
        self._size_combo.addItems(list(PAGE_SIZES.keys()))
        self._size_combo.setCurrentText(setup["page_size"])
        form.addRow("Paper size:", self._size_combo)

        self._orient_combo = QComboBox()
        self._orient_combo.addItems(["Portrait", "Landscape"])
        self._orient_combo.setCurrentText(setup["orientation"].capitalize())
        form.addRow("Orientation:", self._orient_combo)

        self._margin_spin = QDoubleSpinBox()
        self._margin_spin.setRange(5.0, 50.0)
        self._margin_spin.setDecimals(0)
        self._margin_spin.setSingleStep(5.0)
        self._margin_spin.setSuffix(" mm")
        self._margin_spin.setValue(setup["margins_mm"])
        form.addRow("Margins:", self._margin_spin)

        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def current_setup(self) -> dict:
        return {
            "page_size": self._size_combo.currentText(),
            "orientation": self._orient_combo.currentText().lower(),
            "margins_mm": float(self._margin_spin.value()),
        }

    def _save_and_accept(self):
        save_page_setup(self.current_setup())
        self.accept()
