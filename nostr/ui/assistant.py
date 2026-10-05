# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shared shape of MyEditor's step-by-step windows.

Joining EINUNDZWANZIG, creating an account, restoring one: each is a short
sequence of pages in one window, and they should look and behave alike.
This module is that common part, so each window only says what its pages
contain and what happens between them.

What every assistant gets, following Apple's Human Interface Guidelines:

- One default button, at the trailing edge, where Return lands; buttons
  that back out (Cancel, Not Now) beside it; Go Back at the leading edge.
  A page lists its buttons as ``(key, label, placement, handler)`` and the
  window lays them out, so no page can put them anywhere else.
- Text that comes from outside (names, keys, server words) is plain text,
  never markup.
- Progress is shown where the work happens: an indeterminate bar while
  waiting, or a step list that marks each step pending, active, done or
  failed in words as well as colour.
- Colours come from the app's theme, light and dark.

``copy_secret`` puts a secret on the clipboard and takes it off again a
minute later, but only if it is still there: nothing the person copied
afterwards is ever cleared.
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional, Tuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import theme
from constants import (
    DARK_BORDER, DARK_MENU_BG, DARK_MUTED_FG,
    LIGHT_BORDER, LIGHT_MENU_BG, LIGHT_MUTED_FG, LIGHT_SELECTION,
)

# Button placement.
LEADING = "leading"     # Go Back: the leading edge
NORMAL = "normal"       # Not Now, Close, Cancel: beside the default
DEFAULT = "default"     # the likely action: the trailing edge, answers Return

ButtonSpec = Tuple[str, str, str, Callable[[], None]]

SECRET_CLIPBOARD_SECONDS = 60


def stylesheet(is_dark: bool) -> str:
    """The app's dialog look plus what assistants use."""
    if is_dark:
        muted, border, field = DARK_MUTED_FG, DARK_BORDER, DARK_MENU_BG
        accent, ok, err = "#007ACC", "#43A047", "#E57373"
    else:
        muted, border, field = LIGHT_MUTED_FG, LIGHT_BORDER, LIGHT_MENU_BG
        accent, ok, err = LIGHT_SELECTION, "#2E7D32", "#C62828"
    link = theme.dialog_link_color(is_dark)
    return theme.dialog_stylesheet(is_dark) + f"""
    QLabel#title {{ font-size: 16px; font-weight: 600; }}
    QLabel#muted, QLabel#help {{ color: {muted}; }}
    QLabel#help {{ font-size: 11px; }}
    QLabel#error {{ color: {err}; font-size: 11px; }}
    QLabel#item_title {{ font-weight: 600; }}
    QLabel#amount {{ font-size: 22px; font-weight: 600; }}
    QLabel#check {{ color: {ok}; font-size: 15px; font-weight: 700; }}
    QLabel#mono {{ font-family: "Menlo", "Consolas", monospace; }}
    QLabel#qr {{ background: #FFFFFF; border: 1px solid {border}; border-radius: 8px; padding: 6px; }}
    QLabel#step_badge {{
        border: 1px solid {border}; border-radius: 11px;
        color: {muted}; font-size: 11px; font-weight: 600;
    }}
    QLabel#step_badge[state="active"] {{ background: {accent}; border-color: {accent}; color: #FFFFFF; }}
    QLabel#step_badge[state="done"] {{ border-color: {ok}; color: {ok}; }}
    QLabel#step_badge[state="error"] {{ border-color: {err}; color: {err}; }}
    QLineEdit, QPlainTextEdit {{
        background: {field}; border: 1px solid {border}; border-radius: 5px; padding: 4px 6px;
    }}
    QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {accent}; }}
    QPushButton:disabled {{ color: {muted}; }}
    QPushButton:default:disabled {{ background: {field}; color: {muted}; border-color: {border}; }}
    QPushButton#link {{
        border: none; background: transparent; color: {link}; padding: 2px 0; min-width: 0;
    }}
    QPushButton#link:hover {{ text-decoration: underline; }}
    QPushButton#link:disabled {{ color: {muted}; }}
    QRadioButton {{ font-weight: 600; }}
    QProgressBar {{
        background: {field}; border: 1px solid {border}; border-radius: 3px; max-height: 6px;
    }}
    QProgressBar::chunk {{ background: {accent}; border-radius: 2px; }}
    """


def text_label(text: str = "", name: str = "", *, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    label.setTextFormat(Qt.PlainText)
    return label


def link_button(text: str, handler: Callable[[], None]) -> QPushButton:
    """A text-only button for a secondary action that should not compete
    with the page's buttons."""
    button = QPushButton(text)
    button.setObjectName("link")
    button.setAutoDefault(False)
    button.setCursor(Qt.PointingHandCursor)
    button.clicked.connect(handler)
    return button


def busy_bar(width: int = 220) -> QProgressBar:
    bar = QProgressBar()
    bar.setRange(0, 0)      # indeterminate: the wait has no known length
    bar.setTextVisible(False)
    bar.setMaximumWidth(width)
    bar.setAccessibleName("Working")
    return bar


def page(spacing: int = 10) -> Tuple[QWidget, QVBoxLayout]:
    widget = QWidget()
    column = QVBoxLayout(widget)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(spacing)
    return widget, column


def copy_secret(text: str, *, seconds: int = SECRET_CLIPBOARD_SECONDS) -> None:
    """Copy a secret, and clear it from the clipboard after ``seconds``
    unless something else was copied since."""
    clipboard = QApplication.clipboard()
    clipboard.setText(text)

    def clear():
        if clipboard.text() == text:
            clipboard.clear()

    QTimer.singleShot(seconds * 1000, clear)


def _repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class StepList(QWidget):
    """Numbered steps whose state is said in words and shown in colour."""

    _MARKS = {"done": "✓", "error": "!"}

    def __init__(self, steps: Iterable[Tuple[str, str]], parent=None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)
        self._rows: Dict[str, dict] = {}
        for number, (key, title) in enumerate(steps, start=1):
            badge = QLabel(str(number))
            badge.setObjectName("step_badge")
            badge.setFixedSize(22, 22)
            badge.setAlignment(Qt.AlignCenter)
            title_label = text_label(title, "item_title")
            detail = text_label("", "muted")
            detail.hide()
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(title_label)
            text.addWidget(detail)
            row = QHBoxLayout()
            row.setSpacing(12)
            row.addWidget(badge, 0, Qt.AlignTop)
            row.addLayout(text, 1)
            column.addLayout(row)
            self._rows[key] = {"number": number, "badge": badge, "title": title_label,
                               "detail": detail, "state": "pending"}
            self.set_state(key, "pending")

    def set_state(self, key: str, state: str, detail: str = "") -> None:
        """``state`` is pending, active, done or error."""
        row = self._rows[key]
        row["state"] = state
        badge = row["badge"]
        badge.setProperty("state", state)
        badge.setText(self._MARKS.get(state, str(row["number"])))
        spoken = {"pending": "not started", "active": "in progress", "done": "done",
                  "error": "failed"}[state]
        badge.setAccessibleName(f"Step {row['number']}, {spoken}")
        _repolish(badge)
        row["detail"].setText(detail)
        row["detail"].setVisible(bool(detail))

    def state(self, key: str) -> str:
        return self._rows[key]["state"]


class AssistantWindow(QDialog):
    """A window of pages with one row of buttons, laid out the Apple way."""

    def __init__(self, title: str, *, is_dark: bool = True, min_width: int = 520,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(min_width)
        self.setStyleSheet(stylesheet(is_dark))
        self._is_dark = is_dark
        self._page: Optional[str] = None
        self._pages: Dict[str, QWidget] = {}
        self.buttons: Dict[str, QPushButton] = {}
        self._button_widgets: List[QPushButton] = []

        self._stack = QStackedWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(18)
        layout.addWidget(self._stack, 1)
        self._button_row = QHBoxLayout()
        self._button_row.setSpacing(8)
        layout.addLayout(self._button_row)

    @property
    def page(self) -> Optional[str]:
        return self._page

    def add_page(self, key: str, widget: QWidget) -> None:
        self._pages[key] = widget
        self._stack.addWidget(widget)

    def show_page(self, key: str, buttons: Iterable[ButtonSpec]) -> None:
        self._page = key
        self._stack.setCurrentWidget(self._pages[key])
        self.set_buttons(buttons)

    def set_buttons(self, specs: Iterable[ButtonSpec]) -> None:
        """Lay out ``(key, label, placement, handler)`` buttons: Go Back at
        the leading edge, the rest at the trailing edge with the one
        default button last."""
        specs = list(specs)
        for widget in self._button_widgets:
            self._button_row.removeWidget(widget)
            # Hidden now, deleted later: a button from the previous page
            # must not stay on screen until the event loop gets to it.
            widget.hide()
            widget.deleteLater()
        self._button_widgets = []
        while self._button_row.count():
            self._button_row.takeAt(0)
        self.buttons = {}

        def make(key, label, placement, handler):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.setDefault(placement == DEFAULT)
            button.clicked.connect(handler)
            self.buttons[key] = button
            self._button_widgets.append(button)
            return button

        for spec in specs:
            if spec[2] == LEADING:
                self._button_row.addWidget(make(*spec))
        self._button_row.addStretch(1)
        for placement in (NORMAL, DEFAULT):
            for spec in specs:
                if spec[2] == placement:
                    self._button_row.addWidget(make(*spec))
