"""Shared dark/light stylesheets and color tokens for Telegram dialogs.

Palette borrows the VS-Code-flavored colors the rest of the editor
uses (see ``nostr/ui/publish_note_dialog.py``) so Telegram dialogs
feel native alongside the existing ones.

Style notes:

* Code / monospace fonts are applied ONLY to widgets whose
  ``font-role`` property is set to ``"code"``. The Telegram body
  editor is prose by default, not source code.
* Cards use a ``QFrame`` with ``card="true"`` so a panel can have a
  subtle border + padding without nesting QGroupBox after QGroupBox.
* The primary action button (Send, Add, etc.) uses
  ``role="primary"`` for a filled accent style. Destructive buttons
  use ``role="destructive"``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QFrame, QWidget


DARK = {
    "bg": "#1E1E1E",
    "panel": "#252526",
    "card": "#2A2A2C",
    "card_hover": "#323234",
    "border": "#3C3C3C",
    "border_subtle": "#2E2E2E",
    "text": "#D4D4D4",
    "text_strong": "#FFFFFF",
    "muted": "#888888",
    "accent": "#4F86C6",
    "accent_hover": "#5C95D9",
    "accent_text": "#FFFFFF",
    "success": "#73C991",
    "warning": "#FFB347",
    "error": "#F48771",
    "selection": "#264F78",
    "button": "#2D2D30",
    "button_hover": "#3C3C3C",
    "button_pressed": "#1E1E1E",
}

LIGHT = {
    "bg": "#FFFFFF",
    "panel": "#F6F6F6",
    "card": "#FAFAFA",
    "card_hover": "#F0F0F0",
    "border": "#E1E1E1",
    "border_subtle": "#ECECEC",
    "text": "#202020",
    "text_strong": "#000000",
    "muted": "#777777",
    "accent": "#0066B8",
    "accent_hover": "#0078D4",
    "accent_text": "#FFFFFF",
    "success": "#2E8540",
    "warning": "#A05000",
    "error": "#B22222",
    "selection": "#CCE4F7",
    "button": "#ECECEC",
    "button_hover": "#E1E1E1",
    "button_pressed": "#D0D0D0",
}


def is_dark(widget: QWidget) -> bool:
    """Return ``True`` when the application palette is dark-themed."""
    app = QApplication.instance()
    palette = (app or widget).palette() if widget is None else widget.palette()
    window = palette.color(QPalette.Window)
    luma = 0.2126 * window.redF() + 0.7152 * window.greenF() + 0.0722 * window.blueF()
    return luma < 0.5


def palette_for(widget: QWidget) -> dict:
    return DARK if is_dark(widget) else LIGHT


def dialog_stylesheet(p: dict) -> str:
    return f"""
    QDialog, QWidget#tgRoot {{
        background: {p["bg"]};
        color: {p["text"]};
    }}
    QLabel {{
        color: {p["text"]};
        font-size: 13px;
    }}
    QLabel[role="title"] {{
        color: {p["text_strong"]};
        font-size: 18px;
        font-weight: 600;
    }}
    QLabel[role="subtitle"] {{
        color: {p["muted"]};
        font-size: 12px;
    }}
    QLabel[role="section"] {{
        color: {p["muted"]};
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 1px;
    }}
    QLabel[status="info"]   {{ color: {p["accent"]};  }}
    QLabel[status="ok"]     {{ color: {p["success"]}; }}
    QLabel[status="warn"]   {{ color: {p["warning"]}; }}
    QLabel[status="error"]  {{ color: {p["error"]};   }}

    QFrame[card="true"] {{
        background: {p["card"]};
        border: 1px solid {p["border_subtle"]};
        border-radius: 8px;
    }}
    QFrame[divider="true"] {{
        background: {p["border_subtle"]};
        max-height: 1px;
        min-height: 1px;
    }}

    QLineEdit, QPlainTextEdit, QListWidget, QListView,
    QTreeWidget, QTreeView, QComboBox, QSpinBox, QDateTimeEdit, QDateEdit {{
        background: {p["panel"]};
        color: {p["text"]};
        border: 1px solid {p["border"]};
        border-radius: 6px;
        padding: 6px 8px;
        selection-background-color: {p["selection"]};
        selection-color: {p["text_strong"]};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QDateTimeEdit:focus,
    QListWidget:focus, QTreeWidget:focus {{
        border-color: {p["accent"]};
    }}

    QTextEdit {{
        background: {p["panel"]};
        color: {p["text"]};
        border: 1px solid {p["border"]};
        border-radius: 6px;
        padding: 10px 12px;
        selection-background-color: {p["selection"]};
        selection-color: {p["text_strong"]};
        font-size: 14px;
    }}
    QTextEdit[font-role="code"] {{
        font-family: "Menlo", "Consolas", "Noto Sans Mono", monospace;
        font-size: 12px;
    }}

    QListWidget::item, QTreeWidget::item, QTreeView::item, QListView::item {{
        padding: 6px 8px;
        border-radius: 4px;
    }}
    QListWidget::item:hover, QTreeWidget::item:hover {{
        background: {p["card_hover"]};
    }}
    QListWidget::item:selected, QTreeWidget::item:selected,
    QListView::item:selected, QTreeView::item:selected {{
        background: {p["selection"]};
        color: {p["text_strong"]};
    }}

    QPushButton {{
        background: {p["button"]};
        color: {p["text"]};
        border: 1px solid {p["border"]};
        padding: 8px 16px;
        border-radius: 6px;
        font-size: 13px;
    }}
    QPushButton:hover {{ background: {p["button_hover"]}; }}
    QPushButton:pressed {{ background: {p["button_pressed"]}; }}
    QPushButton:disabled {{
        background: {p["panel"]};
        color: {p["muted"]};
        border-color: {p["border_subtle"]};
    }}
    QPushButton[role="primary"] {{
        background: {p["accent"]};
        color: {p["accent_text"]};
        border: 1px solid {p["accent"]};
        font-weight: 600;
    }}
    QPushButton[role="primary"]:hover {{
        background: {p["accent_hover"]};
        border-color: {p["accent_hover"]};
    }}
    QPushButton[role="primary"]:disabled {{
        background: {p["button"]};
        color: {p["muted"]};
        border-color: {p["border_subtle"]};
    }}
    QPushButton[role="destructive"] {{
        color: {p["error"]};
        border-color: {p["border"]};
    }}
    QPushButton[role="ghost"] {{
        background: transparent;
        border: 1px solid transparent;
        color: {p["accent"]};
        padding: 6px 10px;
    }}
    QPushButton[role="ghost"]:hover {{
        background: {p["card_hover"]};
    }}

    QToolButton {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 6px;
        padding: 6px 10px;
        color: {p["text"]};
    }}
    QToolButton:hover {{
        background: {p["card_hover"]};
        border-color: {p["border_subtle"]};
    }}
    QToolButton::menu-indicator {{ image: none; width: 0; }}

    QCheckBox, QRadioButton {{ color: {p["text"]}; padding: 4px 0px; spacing: 8px; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 16px; height: 16px;
    }}
    QCheckBox::indicator:unchecked, QTreeWidget::indicator:unchecked,
    QListWidget::indicator:unchecked {{
        border: 1.5px solid {p["border"]};
        background: {p["panel"]};
        border-radius: 3px;
    }}
    QCheckBox::indicator:checked, QTreeWidget::indicator:checked,
    QListWidget::indicator:checked {{
        background: {p["accent"]};
        border: 1.5px solid {p["accent"]};
        border-radius: 3px;
        image: url();
    }}
    QTreeWidget::indicator, QListWidget::indicator {{ width: 16px; height: 16px; }}

    QSplitter::handle {{ background: {p["border_subtle"]}; }}
    QSplitter::handle:horizontal {{ width: 1px; }}
    QSplitter::handle:vertical {{ height: 1px; }}

    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {p["border"]}; border-radius: 5px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {p["muted"]}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

    QMessageBox {{ background: {p["bg"]}; }}
    QMessageBox QLabel {{ color: {p["text"]}; }}
    """


def apply(widget: QWidget) -> None:
    """Set the standard Telegram-dialog stylesheet on ``widget``."""
    widget.setStyleSheet(dialog_stylesheet(palette_for(widget)))


def make_card(parent: QWidget) -> QFrame:
    """Create a styled card frame (used as a row in lists)."""
    frame = QFrame(parent)
    frame.setProperty("card", True)
    return frame


def make_divider(parent: QWidget) -> QFrame:
    line = QFrame(parent)
    line.setProperty("divider", True)
    line.setFrameShape(QFrame.HLine)
    return line
