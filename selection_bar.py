# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The format bar: a few format controls over text selected with the pointer.

When a selection made with the pointer ends (a drag, a double or a triple
click), a small bar shows centred above its first line, as the iPad's edit
menu and Word's mini toolbar do: the paragraph style pop-up (Format >
Style), Bold, Italic, Strikethrough, Code and Link. They are the window's
own commands (commands.py), so their checks, their dimming and what they
do are the menus' and the toolbar's. A selection made with the keyboard
shows none: the keyboard has the commands' keys.

The bar is a child of the text's view, so it is drawn and moves the same
on every platform (no window of its own for Cocoa, Windows, X11 or Wayland
to place), and it stays inside that view: above the selection, or below
it when there is no room above (popup_placement.place_above). It never
takes the keyboard focus. A click on one of its buttons applies the
command and keeps the selection and the bar, so Bold then Italic works.

It goes when typing starts, on Escape (which then does nothing else; the
selection stays), when the selection collapses or changes, on a scroll or
a resize, when the focus or the window goes elsewhere or the text is
hidden (another tab), and when the words change under it. A change of
format (its own buttons', a key's) keeps it, placed again. It never shows
while a pop-up (a menu, the link popover, a mention), the slash menu or an
input method's composition is up, nor where none of its buttons can act (a
code block), nor where the window does not want it (``wanted``: a
plain-text file, Settings' choice off).

Screen readers and the keyboard have every one of these commands in the
menus and the toolbar; the bar's buttons are named for them too.
"""

from __future__ import annotations

from typing import Callable, Dict

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QMenu, QToolButton,
)

import format_icons
from constants import DARK_BORDER, DARK_MENU_BG, LIGHT_BORDER, LIGHT_MENU_BG
from format_toolbar import (
    STYLE_ARROW_END, Disclosure, FormatToolbar, PopupButton, button_rules, draw_icons,
    style_popup_rules,
)
from i18n import _, pgettext
from popup_placement import place_above

# Its buttons after the style pop-up, in order (format_toolbar.ICONS' names).
BUTTONS = ("bold", "italic", "strike", "code", "link")

# Keys that change nothing pressed on their own: they keep the bar, so
# Command-B (Ctrl+B) applies Bold with the bar still up.
_MODIFIER_KEYS = frozenset({
    Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Meta, Qt.Key.Key_Alt,
    Qt.Key.Key_AltGr, Qt.Key.Key_CapsLock, Qt.Key.Key_Super_L, Qt.Key.Key_Super_R,
})

# How far it keeps from the edges of the text's view.
_MARGIN = 4


class SelectionBar(QFrame):
    """The bar over ``editor``'s selection. ``actions`` maps BUTTONS to
    the window's commands; ``style_menu`` is Format > Style; ``dark()``
    says the theme; ``wanted()`` whether the bar is wanted in this editor
    now."""

    def __init__(self, editor, actions: Dict[str, QAction], style_menu: QMenu, *,
                 dark: Callable[[], bool], wanted: Callable[[], bool]) -> None:
        super().__init__(editor.viewport())
        self.setObjectName("SelectionBar")
        self.setAccessibleName(_("Format"))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # The pointer over it is the pointer over a control, not text.
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.hide()
        self._editor = editor
        self._actions = actions
        self._style_menu = style_menu
        self._dark = dark
        self._wanted = wanted
        self._shown_dark = None
        self._selection = None          # (anchor, position) it was shown for

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 3, 4, 3)
        row.setSpacing(2)
        self.style_button = PopupButton(self)
        self.style_button.setMenu(style_menu)
        self.style_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.style_button.setAccessibleName(_("Style"))
        self.style_button.setToolTip(_("Paragraph style"))
        names = [a.text() for a in style_menu.actions()] + [pgettext("paragraph style", "Mixed")]
        width = max(self.fontMetrics().horizontalAdvance(n) for n in names)
        self.style_button.setMinimumWidth(width + 40)
        row.addWidget(self.style_button)
        self._arrow = Disclosure(self.style_button, end=STYLE_ARROW_END, dark=False)
        self.buttons: Dict[str, QToolButton] = {}
        for name in BUTTONS:
            if name in ("bold", "link"):
                row.addWidget(self._separator())
            action = actions[name]
            button = QToolButton(self)
            button.setDefaultAction(action)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setIconSize(QSize(format_icons.SIZE, format_icons.SIZE))
            # A method of the bar: the connection goes with it.
            action.changed.connect(self._describe_buttons)
            self.buttons[name] = button
            row.addWidget(button)
        self._describe_buttons()
        self.set_style_name(_("Body"))

        # The view kept by itself: the filter never asks the editor for it,
        # which is gone first when everything is taken down at the end.
        self._viewport = editor.viewport()
        editor.installEventFilter(self)
        self._viewport.installEventFilter(self)
        editor.selectionChanged.connect(self._selection_changed)
        editor.cursorPositionChanged.connect(self._selection_changed)
        editor.visible_area_changed.connect(self.hide)
        editor.document().contentsChange.connect(self._contents_changed)

    def _separator(self) -> QFrame:
        line = QFrame(self)
        line.setObjectName("SelectionBarSeparator")
        line.setFixedWidth(1)
        return line

    def _describe_buttons(self) -> None:
        """Each button says what its command is called now, as the
        toolbar's do (FormatToolbar._describe)."""
        for name, button in self.buttons.items():
            FormatToolbar._describe(self._actions[name], button)

    # -- what the window tells it ------------------------------------------------

    def set_style_name(self, name: str) -> None:
        """The paragraph style of the selection ("Heading 2"), on the pop-up."""
        self.style_button.setText(name)

    def set_dark(self, dark: bool) -> None:
        self._shown_dark = dark
        draw_icons({name: self._actions[name] for name in BUTTONS}, dark)
        background, border = (DARK_MENU_BG, DARK_BORDER) if dark else (LIGHT_MENU_BG,
                                                                       LIGHT_BORDER)
        self.setStyleSheet(f"""
            #SelectionBar {{
                background: {background};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            #SelectionBarSeparator {{ background: {border}; margin: 4px 3px; }}
            {button_rules("#SelectionBar", dark)}
        """)
        self.style_button.setStyleSheet(style_popup_rules(dark))
        self._arrow.set_dark(dark)

    # -- showing -------------------------------------------------------------------

    def _show_if_selected(self) -> None:
        """A selection made with the pointer just ended: the bar, when it
        is wanted and nothing else is up."""
        editor = self._editor
        cursor = editor.textCursor()
        if (not cursor.hasSelection() or not self._wanted() or not editor.hasFocus()
                or QApplication.activePopupWidget() is not None
                or QApplication.mouseButtons() != Qt.MouseButton.NoButton
                or editor.slash_menu() is not None or self._composing()
                # Over a code block every button would be dimmed: no bar.
                or not any(action.isEnabled() for action in self._actions.values())):
            return
        if self._shown_dark != self._dark():
            self.set_dark(self._dark())
        self._selection = (cursor.anchor(), cursor.position())
        self.show()
        self.raise_()
        self._place()

    def _composing(self) -> bool:
        """Whether an input method is composing in the text."""
        return bool(self._editor.textCursor().block().layout().preeditAreaText())

    def _place(self) -> None:
        """Centred above the selection's first line, or below its last, in
        either direction of writing."""
        if not self.isVisible():
            return
        editor = self._editor
        selection = editor.textCursor()
        start = QTextCursor(editor.document())
        start.setPosition(selection.selectionStart())
        end = QTextCursor(editor.document())
        end.setPosition(selection.selectionEnd())
        line_end = QTextCursor(start)
        line_end.movePosition(QTextCursor.MoveOperation.EndOfLine)
        if line_end.position() > end.position():
            line_end = end
        # Its two ends, whichever side each is on: right to left, the
        # selection starts at the right.
        top, other = editor.cursorRect(start), editor.cursorRect(line_end)
        left, right = sorted((top.left(), other.left()))
        first = QRect(left, top.top(), max(1, right - left), top.height())
        area = editor.viewport().rect().adjusted(_MARGIN, _MARGIN, -_MARGIN, -_MARGIN)
        place_above(self, first, editor.cursorRect(end), area)

    # -- going ---------------------------------------------------------------------

    def _selection_changed(self) -> None:
        cursor = self._editor.textCursor()
        if self.isVisible() and (cursor.anchor(), cursor.position()) != self._selection:
            self.hide()

    def _contents_changed(self, _position: int, removed: int, added: int) -> None:
        """Words added or taken away go under the bar; a change of format
        (Bold from it, a key's) keeps it, placed again once laid out."""
        if not self.isVisible():
            return
        if removed != added:
            self.hide()
        else:
            QTimer.singleShot(0, self, self._place)

    def _hide_unless_in_use(self) -> None:
        """The focus or the window went elsewhere, or a popup (a menu, a
        popover) took the keys: the bar goes, unless that is the bar's own
        style menu. (A popup leaves the text its focus and the window
        active: it is asked for by name.)"""
        popup = QApplication.activePopupWidget()
        if popup is self._style_menu or self._style_menu.isVisible():
            return
        if (popup is not None or not self._editor.hasFocus()
                or not self._editor.isActiveWindow()):
            self.hide()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802, Qt's name
        kind = event.type()
        if watched is self._viewport:
            if kind == QEvent.Type.MouseButtonPress:
                self.hide()
            elif (kind == QEvent.Type.MouseButtonRelease
                  and event.button() == Qt.MouseButton.LeftButton):
                # Once the text has handled the release (a click inside a
                # selection clears it then).
                QTimer.singleShot(0, self, self._show_if_selected)
            return False
        if self.isHidden():
            # (Not isVisible: hidden with the text, in another tab, the bar
            # is still up until it hears so, here.)
            return False
        if kind == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Escape:
            event.accept()                     # Escape comes here, to close the bar
            return True
        if kind == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.hide()
                return True
            if event.key() not in _MODIFIER_KEYS:
                self.hide()
        elif kind in (QEvent.Type.InputMethod, QEvent.Type.Hide):
            self.hide()
        elif kind in (QEvent.Type.FocusOut, QEvent.Type.WindowDeactivate):
            # Once the focus has landed (a menu opening takes it first).
            QTimer.singleShot(0, self, self._hide_unless_in_use)
        return False

    # -- a click on the bar between its buttons stays on the bar -------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802, Qt's name
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802, Qt's name
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802, Qt's name
        event.accept()
