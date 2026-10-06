# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The format toolbar: the few text controls writers reach for most.

The paragraph style (Body, Heading 1 to 3), bold, italic, strikethrough,
a link, the two kinds of list and a quote. Everything else is in the
menus, and the menus hold everything; this bar repeats only what is used
all the time, the way TextEdit's and Pages' format bars do. Underline has
no button: Markdown and Nostr have no underline (it stays in the Format
menu, for local files).

Every button is the window's own command (commands.py): the same action,
so its state, its key and whether it applies right now are the same here
and in the menu. The bar shows each one's key in its tooltip, in the
platform's own notation (⌘B on a Mac, Ctrl+B elsewhere). It holds no
logic of its own; the window tells it the current style's name.
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import QMenu, QPushButton, QToolBar, QToolButton, QWidget

import format_icons
from constants import (
    DARK_BORDER, DARK_FG, DARK_MENU_BG, DARK_SELECTION, LIGHT_BORDER, LIGHT_FG, LIGHT_MENU_BG,
    LIGHT_SELECTION,
)
from i18n import _, pgettext

# The buttons, in order; None is a separator.
LAYOUT = ("bold", "italic", "strike", None, "link", None, "bullets", "numbers", "quote")


def tooltip_for(action: QAction) -> str:
    """"Bold (⌘B)": the command's name and its key, as the platform
    writes keys, or only the name when it has none."""
    title = action.text().replace("&", "").rstrip("…").rstrip()
    keys = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
    return _("{command} ({keys})").format(command=title, keys=keys) if keys else title


class FormatToolbar(QToolBar):
    """The format toolbar. ``actions`` maps LAYOUT's names to the
    window's commands; ``style_menu`` is Format > Style."""

    def __init__(self, actions: Dict[str, QAction], style_menu: QMenu, *,
                 dark: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(_("Format"), parent)
        self.setObjectName("FormatToolbar")
        self.setAccessibleName(_("Format"))
        self.setMovable(False)
        self.setFloatable(False)
        self.setIconSize(QSize(format_icons.SIZE, format_icons.SIZE))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._actions = actions
        self._dark = dark

        # The paragraph style, as a pop-up button that says which it is.
        self.style_button = QPushButton(self)
        self.style_button.setObjectName("StyleButton")
        self._style_menu = style_menu        # the window's; kept while the bar uses it
        self.style_button.setMenu(style_menu)
        self.style_button.setAccessibleName(_("Style"))
        self.style_button.setToolTip(_("Paragraph style"))
        names = [a.text() for a in style_menu.actions()] + [pgettext("paragraph style", "Mixed")]
        width = max(self.fontMetrics().horizontalAdvance(n) for n in names)
        self.style_button.setMinimumWidth(width + 40)
        self.addWidget(self.style_button)
        self.addSeparator()

        self.buttons: Dict[str, QToolButton] = {}
        for name in LAYOUT:
            if name is None:
                self.addSeparator()
                continue
            action = actions[name]
            self.addAction(action)
            button = self.widgetForAction(action)
            self.buttons[name] = button
            action.changed.connect(lambda a=action, b=button: self._describe(a, b))
            self._describe(action, button)
        # The chevron that holds the buttons a narrow window has no room
        # for: Qt gives it no name, so a screen reader would say "button".
        more = self.findChild(QToolButton, "qt_toolbar_ext_button")
        if more is not None:
            more.setAccessibleName(_("More Formatting"))
            more.setToolTip(_("More Formatting"))
        self.set_style_name(_("Body"))
        self.set_dark(dark)

    # -- what the window tells it ------------------------------------------------

    def set_style_name(self, name: str) -> None:
        """The paragraph style at the caret ("Heading 2"), on the pop-up."""
        self.style_button.setText(name)

    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        ink = QColor(DARK_FG if dark else LIGHT_FG)
        icons = {
            "bold": format_icons.letter(pgettext("format button", "B"), ink, bold=True),
            "italic": format_icons.letter(pgettext("format button", "I"), ink, italic=True),
            "strike": format_icons.letter(pgettext("format button", "S"), ink, strike=True),
            "link": format_icons.link(ink),
            "bullets": format_icons.bulleted_list(ink),
            "numbers": format_icons.numbered_list(ink),
            "quote": format_icons.quote(ink),
        }
        for name, icon in icons.items():
            self._actions[name].setIcon(icon)
            # The menus stay text, as menus on every platform are.
            self._actions[name].setIconVisibleInMenu(False)
        background, border, selection = (
            (DARK_MENU_BG, DARK_BORDER, DARK_SELECTION) if dark
            else (LIGHT_MENU_BG, LIGHT_BORDER, LIGHT_SELECTION))
        hover = "rgba(255, 255, 255, 0.08)" if dark else "rgba(0, 0, 0, 0.06)"
        checked = "rgba(255, 255, 255, 0.16)" if dark else "rgba(0, 0, 0, 0.11)"
        text = DARK_FG if dark else LIGHT_FG
        self.setStyleSheet(f"""
            #FormatToolbar {{
                background: {background};
                border: none;
                border-bottom: 1px solid {border};
                padding: 3px 8px;
                spacing: 2px;
            }}
            #FormatToolbar QToolButton {{
                color: {text};
                background: transparent;
                border: 1px solid transparent;
                border-radius: 5px;
                padding: 3px;
            }}
            #FormatToolbar QToolButton:hover {{ background: {hover}; }}
            #FormatToolbar QToolButton:checked {{ background: {checked}; }}
            #FormatToolbar QToolButton:focus {{ border-color: {selection}; }}
            #FormatToolbar::separator {{
                background: {border};
                width: 1px;
                margin: 5px 6px;
            }}
        """)
        self._style_style_button(text, border, hover, selection)

    def _style_style_button(self, text: str, border: str, hover: str, selection: str) -> None:
        # Its own sheet: a pop-up button reads left to right, its name
        # first and the arrow at the end, as in TextEdit and Pages. (A
        # ::menu-indicator rule would make Qt drop the padding.)
        self.style_button.setStyleSheet(f"""
            QPushButton {{
                color: {text};
                background: transparent;
                border: 1px solid {border};
                border-radius: 5px;
                padding: 4px 24px 4px 9px;
                text-align: left;
            }}
            QPushButton:hover {{ background: {hover}; }}
            QPushButton:focus {{ border-color: {selection}; }}
        """)

    @staticmethod
    def _describe(action: QAction, button: QToolButton) -> None:
        """The button says what its command is called now (Add Link turns
        into Edit Link), to the eye and to a screen reader alike."""
        button.setToolTip(tooltip_for(action))
        button.setAccessibleName(action.text().replace("&", "").rstrip("…").rstrip())
