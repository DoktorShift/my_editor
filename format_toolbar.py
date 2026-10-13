# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The format toolbar: the few text controls writers reach for most.

Undo and Redo, the paragraph style (Body, Heading 1 to 3), bold, italic,
strikethrough, a link, the two kinds of list, a quote, and one Insert
button that opens the Insert menu itself (as Pages' toolbar has). Everything
else is in the menus, and the menus hold everything; this bar repeats only
what is used all the time, the way TextEdit's and Pages' format bars do.
Underline has no button: Markdown and Nostr have no underline (it stays in
the Format menu, for local files).

Undo and Redo each have an arrow beside them, as Pages' and Word's do: it
lists the steps they take back (or bring back), newest first, to go
several at once. The window fills those lists as they open (undo_menu,
redo_menu); they open with a right-click (Control-click on a Mac) too, and
with Down while the button has the keyboard focus, which Qt gives such a
button no key for.

Every button is the window's own command (commands.py): the same action,
so its state, its key and whether it applies right now are the same here
and in the menu. The bar shows each one's key in its tooltip, in the
platform's own notation (⌘B on a Mac, Ctrl+B elsewhere). It holds no
logic of its own; the window tells it the current style's name.
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QToolBar, QToolButton, QWidget

import format_icons
from constants import (
    DARK_BORDER, DARK_FG, DARK_MENU_BG, DARK_MUTED_FG, DARK_SELECTION, LIGHT_BORDER, LIGHT_FG,
    LIGHT_MENU_BG, LIGHT_MUTED_FG, LIGHT_SELECTION,
)
from i18n import _, pgettext

# The buttons before the style pop-up, each with its list of steps.
HISTORY = ("undo", "redo")
# The buttons after it, in order; None is a separator.
LAYOUT = ("bold", "italic", "strike", None, "link", None, "bullets", "numbers", "checklist",
          "quote")


# How each command's button is drawn, on the toolbar and on the format bar
# over a selection (selection_bar.py), in the ink it is given.
ICONS = {
    "undo": format_icons.undo,
    "redo": format_icons.redo,
    "bold": lambda ink: format_icons.letter(pgettext("format button", "B"), ink, bold=True),
    "italic": lambda ink: format_icons.letter(pgettext("format button", "I"), ink,
                                              italic=True),
    # A letter whose struck form still reads as a letter crossed out (a
    # struck D reads as the letter Đ).
    "strike": lambda ink: format_icons.letter(pgettext("format button", "S"), ink,
                                              strike=True),
    "code": format_icons.code_block,
    "link": format_icons.link,
    "bullets": format_icons.bulleted_list,
    "numbers": format_icons.numbered_list,
    "checklist": format_icons.checklist,
    "quote": format_icons.quote,
}


def draw_icons(actions: Dict[str, QAction], dark: bool) -> None:
    """Give each of ``actions`` (by ICONS' names) its icon, in the theme's
    ink. The menus stay text, as menus on every platform are."""
    ink = QColor(DARK_FG if dark else LIGHT_FG)
    for name, action in actions.items():
        action.setIcon(ICONS[name](ink))
        action.setIconVisibleInMenu(False)


def button_rules(scope: str, dark: bool) -> str:
    """The style of the command buttons inside ``scope`` (a selector): quiet
    until the pointer is over one, and one that is on shows an edge of at
    least 3:1 against what is behind it (WCAG 1.4.11), not a faint fill
    alone."""
    hover, checked = (("rgba(255, 255, 255, 0.08)", "rgba(255, 255, 255, 0.16)") if dark
                      else ("rgba(0, 0, 0, 0.06)", "rgba(0, 0, 0, 0.11)"))
    checked_edge = DARK_MUTED_FG if dark else LIGHT_MUTED_FG
    text = DARK_FG if dark else LIGHT_FG
    selection = DARK_SELECTION if dark else LIGHT_SELECTION
    return f"""
        {scope} QToolButton {{
            color: {text};
            background: transparent;
            border: 1px solid transparent;
            border-radius: 5px;
            padding: 3px;
        }}
        {scope} QToolButton:hover {{ background: {hover}; }}
        {scope} QToolButton:checked {{
            background: {checked};
            border-color: {checked_edge};
        }}
        {scope} QToolButton:focus {{ border-color: {selection}; }}
    """


def style_popup_rules(dark: bool) -> str:
    """The paragraph style pop-up's own sheet: it reads left to right, its
    name first and the arrow at the end, as in TextEdit and Pages. (A
    ::menu-indicator rule would make Qt drop the padding.)"""
    text, border, selection = ((DARK_FG, DARK_BORDER, DARK_SELECTION) if dark
                               else (LIGHT_FG, LIGHT_BORDER, LIGHT_SELECTION))
    hover = "rgba(255, 255, 255, 0.08)" if dark else "rgba(0, 0, 0, 0.06)"
    dim = format_icons.dimmed(QColor(text))
    dim_text = f"rgba({dim.red()}, {dim.green()}, {dim.blue()}, {dim.alphaF():.2f})"
    return f"""
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
        QPushButton:disabled {{ color: {dim_text}; background: transparent; }}
    """


def tooltip_for(action: QAction, title: str = "") -> str:
    """"Bold (⌘B)": the command's name (or ``title``) and its key, as the
    platform writes keys, or only the name when it has none."""
    title = title or action.text().replace("&", "").rstrip("…").rstrip()
    keys = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
    return _("{command} ({keys})").format(command=title, keys=keys) if keys else title


class FormatToolbar(QToolBar):
    """The format toolbar. ``actions`` maps HISTORY's and LAYOUT's names
    to the window's commands; ``style_menu`` is Format > Style and
    ``insert_menu`` the Insert menu."""

    def __init__(self, actions: Dict[str, QAction], style_menu: QMenu, insert_menu: QMenu,
                 *, dark: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(_("Format"), parent)
        self.setObjectName("FormatToolbar")
        self.setAccessibleName(_("Format"))
        self.setMovable(False)
        self.setFloatable(False)
        self.setIconSize(QSize(format_icons.SIZE, format_icons.SIZE))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._actions = actions
        self._dark = dark

        # Undo and Redo first, at the leading edge (the last a narrow window
        # moves into the chevron): a click takes one step, the arrow beside
        # lists them (the module docstring).
        self.buttons: Dict[str, QToolButton] = {}
        self._history_titles: Dict[str, str] = {}
        for name in HISTORY:
            action = actions[name]
            self.addAction(action)
            button = self.widgetForAction(action)
            button.setMenu(QMenu(button))
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(lambda _at, b=button: b.showMenu())
            button.installEventFilter(self)
            button.setAccessibleName(_("Undo") if name == "undo" else _("Redo"))
            self.buttons[name] = button
            action.changed.connect(lambda n=name: self._describe_history(n))
            self._describe_history(name)
        self.undo_menu = self.buttons["undo"].menu()
        self.redo_menu = self.buttons["redo"].menu()
        self.addSeparator()

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
        self.addSeparator()

        # Insert: the window's Insert menu itself, so the same items in the
        # same order, the Nostr ones only while an account is in use (the
        # menu fills itself as it opens).
        self.insert_button = QToolButton(self)
        self.insert_button.setObjectName("InsertButton")
        self.insert_button.setMenu(insert_menu)
        self.insert_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.insert_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.insert_button.setAccessibleName(_("Insert"))
        self.insert_button.setToolTip(_("Insert"))
        self.addWidget(self.insert_button)
        # The chevron that holds the buttons a narrow window has no room
        # for: Qt gives it no name, so a screen reader would say "button".
        self._insert_menu = insert_menu
        self._popup_entries: list = []
        more = self.findChild(QToolButton, "qt_toolbar_ext_button")
        if more is not None:
            more.setAccessibleName(_("More Formatting"))
            more.setToolTip(_("More Formatting"))
            if more.menu() is not None:
                # Once Qt has filled it again, which it does as it opens.
                more.menu().aboutToShow.connect(lambda m=more.menu(): QTimer.singleShot(
                    0, lambda: self._hold_hidden_popups(m)))
        self.set_style_name(_("Body"))
        self.set_dark(dark)

    def minimumSizeHint(self) -> QSize:  # noqa: N802, Qt's name
        """Room for Undo and Redo both, before the chevron: the narrowest
        window keeps them in sight, every other button going into the
        chevron first (Qt's own minimum holds the first button only)."""
        hint = super().minimumSizeHint()
        redo = self.buttons["redo"].sizeHint().width() + self.layout().spacing()
        return QSize(hint.width() + redo, hint.height())

    def _hold_hidden_popups(self, menu: QMenu) -> None:
        """The chevron's ``menu``, as it opens: the style pop-up and Insert
        too when they have no room, as submenus (Qt lists the hidden
        buttons only, not the pop-ups)."""
        for entry in self._popup_entries:
            menu.removeAction(entry)
        self._popup_entries = []
        first = next((a for a in menu.actions() if not a.isSeparator()), None)
        if not self.style_button.isVisible():
            self._popup_entries.append(menu.insertMenu(first, self._style_menu))
        if not self.insert_button.isVisible():
            self._popup_entries.append(menu.addMenu(self._insert_menu))

    def add_trailing(self, widget: QWidget) -> QWidget:
        """A holder for ``widget`` (the Write, Preview and Split switch), to
        go right of the toolbar, outside it, looking like its far end. A
        narrow window then moves the toolbar's own buttons into its
        chevron, never the switch (a narrow Split needs it, Q-N). Showing
        or hiding the holder shows or hides the switch."""
        holder = QWidget()
        holder.setObjectName("FormatBarTrailing")
        holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(holder)
        row.setContentsMargins(6, 3, 8, 3)
        row.addWidget(widget)
        self._trailing = holder
        self._style_trailing()
        return holder

    def _style_trailing(self) -> None:
        holder = getattr(self, "_trailing", None)
        if holder is None:
            return
        background, border = ((DARK_MENU_BG, DARK_BORDER) if self._dark
                              else (LIGHT_MENU_BG, LIGHT_BORDER))
        holder.setStyleSheet(f"#FormatBarTrailing {{ background: {background};"
                             f" border: none; border-bottom: 1px solid {border}; }}")

    # -- what the window tells it ------------------------------------------------

    def set_style_name(self, name: str) -> None:
        """The paragraph style at the caret ("Heading 2"), on the pop-up."""
        self.style_button.setText(name)

    def describe_history(self, undo: str, redo: str) -> None:
        """What Undo and Redo take now, for their tips ("Undo Typing"), on
        every platform."""
        self._history_titles = {"undo": undo, "redo": redo}
        for name in HISTORY:
            self._describe_history(name)

    def _describe_history(self, name: str) -> None:
        # Also when the command changes, which sets Qt's own tip again.
        self.buttons[name].setToolTip(tooltip_for(self._actions[name],
                                                  self._history_titles.get(name, "")))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802, Qt's name
        # Down (Option-Down, Alt+Down) opens Undo's or Redo's list from the
        # keyboard, as it opens a pop-up menu; Space takes one step.
        if (event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Down
                and watched in (self.buttons["undo"], self.buttons["redo"])):
            watched.showMenu()
            return True
        return super().eventFilter(watched, event)

    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        draw_icons({name: self._actions[name] for name in (*HISTORY, *LAYOUT) if name}, dark)
        ink = QColor(DARK_FG if dark else LIGHT_FG)
        self.insert_button.setIcon(format_icons.plus(ink))
        # The chevron in the theme's ink too: the style's own barely shows
        # on a dark toolbar.
        more = self.findChild(QToolButton, "qt_toolbar_ext_button")
        if more is not None:
            more.setIcon(format_icons.more(ink))
        background, border = (DARK_MENU_BG, DARK_BORDER) if dark else (LIGHT_MENU_BG,
                                                                       LIGHT_BORDER)
        hover = "rgba(255, 255, 255, 0.08)" if dark else "rgba(0, 0, 0, 0.06)"
        self.setStyleSheet(f"""
            #FormatToolbar {{
                background: {background};
                border: none;
                border-bottom: 1px solid {border};
                padding: 3px 8px;
                spacing: 2px;
            }}
            {button_rules("#FormatToolbar", dark)}
            #FormatToolbar QToolButton[popupMode="1"],
            #FormatToolbar #InsertButton {{ padding-right: 13px; }}
            #FormatToolbar #qt_toolbar_ext_button {{ padding: 1px; }}
            #FormatToolbar #InsertButton::menu-indicator {{
                subcontrol-origin: padding;
                subcontrol-position: center right;
            }}
            #FormatToolbar QToolButton::menu-button {{
                background: transparent;
                border: none;
                width: 12px;
            }}
            #FormatToolbar QToolButton::menu-button:hover {{
                background: {hover};
                border-radius: 4px;
            }}
            #FormatToolbar::separator {{
                background: {border};
                width: 1px;
                margin: 5px 6px;
            }}
        """)
        self.style_button.setStyleSheet(style_popup_rules(dark))
        self._style_trailing()

    @staticmethod
    def _describe(action: QAction, button: QToolButton) -> None:
        """The button says what its command is called now (Add Link turns
        into Edit Link), to the eye and to a screen reader alike."""
        button.setToolTip(tooltip_for(action))
        button.setAccessibleName(action.text().replace("&", "").rstrip("…").rstrip())
