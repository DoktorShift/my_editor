#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Application-level color palette.

The window and its custom widgets are themed with hand-written Qt
stylesheets, but standard pop-ups (QMessageBox, QInputDialog), the
QTabBar overflow scroller and any other control nobody explicitly
skinned fall back to the platform style. With the Fusion style forced
in main.py, Qt honors the QPalette below consistently on every OS, so
those un-styled widgets follow the light/dark toggle automatically
instead of rendering in the native theme.

This is the single source of truth for the palette; colors come from
constants.py so there is no second list to keep in sync. Call
apply_app_theme() once at startup and again on every theme change.
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QProxyStyle, QStyle

from constants import (
    DARK_BG, DARK_BORDER, DARK_FG, DARK_MENU_BG, DARK_MUTED_FG, DARK_SELECTION,
    LIGHT_BG, LIGHT_BORDER, LIGHT_FG, LIGHT_MENU_BG, LIGHT_MUTED_FG, LIGHT_SELECTION,
)

# Dialog buttons, per theme: the default action in the accent color and a
# destructive action in red text, as Apple's alerts do. Every color here is
# chosen against its own theme's surfaces, so no button can end up as light
# text on a light background.
_DIALOG_ACCENT = {True: ("#007ACC", "#1177C7"), False: (LIGHT_SELECTION, "#106EBE")}
_DIALOG_DESTRUCTIVE = {True: "#FF6B5E", False: "#C0392B"}
_DIALOG_LINK = {True: "#4FA3F7", False: LIGHT_SELECTION}
# The ring around the control that has the keyboard focus: the link blue
# around a plain button (3:1 or more against the button and the window),
# and around the accent-filled default button a color that stands out
# from the accent itself.
_FOCUS_RING = {True: "#4FA3F7", False: LIGHT_SELECTION}
_FOCUS_RING_ON_ACCENT = {True: "#FFFFFF", False: "#0A2540"}


def _palette(is_dark: bool) -> QPalette:
    if is_dark:
        bg, fg, menu_bg, muted_fg, sel = (
            DARK_BG, DARK_FG, DARK_MENU_BG, DARK_MUTED_FG, DARK_SELECTION
        )
    else:
        bg, fg, menu_bg, muted_fg, sel = (
            LIGHT_BG, LIGHT_FG, LIGHT_MENU_BG, LIGHT_MUTED_FG, LIGHT_SELECTION
        )

    role = QPalette.ColorRole
    group = QPalette.ColorGroup
    p = QPalette()

    # Window / dialog surfaces and their text.
    p.setColor(role.Window, QColor(bg))
    p.setColor(role.WindowText, QColor(fg))
    # Text-entry surfaces (QLineEdit in the rename dialog, etc.).
    p.setColor(role.Base, QColor(bg))
    p.setColor(role.AlternateBase, QColor(menu_bg))
    p.setColor(role.Text, QColor(fg))
    p.setColor(role.PlaceholderText, QColor(muted_fg))
    # Push buttons (message-box Yes / No / Cancel).
    p.setColor(role.Button, QColor(menu_bg))
    p.setColor(role.ButtonText, QColor(fg))
    # Tooltips (previously a hard-coded app stylesheet in main.py).
    p.setColor(role.ToolTipBase, QColor(menu_bg))
    p.setColor(role.ToolTipText, QColor(fg))
    # Selection highlight; white reads cleanly on both selection colors.
    p.setColor(role.Highlight, QColor(sel))
    p.setColor(role.HighlightedText, QColor("#FFFFFF"))
    p.setColor(role.Link, QColor(sel))
    p.setColor(role.LinkVisited, QColor(sel))
    p.setColor(role.Accent, QColor(sel))

    # Greyed-out text (disabled buttons, disabled menu entries) stays
    # legible but clearly muted on both themes.
    for r in (role.WindowText, role.Text, role.ButtonText):
        p.setColor(group.Disabled, r, QColor(muted_fg))

    return p


def is_dark_active() -> bool:
    """Whether the dark palette is the one currently applied.

    Read back from the application palette that apply_app_theme() sets, so
    dialogs can follow the theme without it being passed to every call.
    """
    app = QApplication.instance()
    return app is not None and app.palette().color(QPalette.Window).lightness() < 128


def dialog_stylesheet(is_dark: bool) -> str:
    """Base stylesheet for the app's own dialogs (alerts.py, update_dialog.py).

    Covers the surface, text, and the three kinds of push button: normal,
    default (``setDefault(True)``), and destructive (objectName
    ``destructive``), each with a ring that shows when it has the keyboard
    focus (a thicker border, with the padding reduced to match, so the
    button keeps its size). Dialogs append their own rules after it.
    """
    if is_dark:
        bg, fg, muted, border, field = DARK_BG, DARK_FG, DARK_MUTED_FG, DARK_BORDER, DARK_MENU_BG
    else:
        bg, fg, muted, border, field = LIGHT_BG, LIGHT_FG, LIGHT_MUTED_FG, LIGHT_BORDER, LIGHT_MENU_BG
    destructive = _DIALOG_DESTRUCTIVE[is_dark]
    ring = _FOCUS_RING[is_dark]
    return f"""
    QDialog {{ background: {bg}; }}
    QLabel {{ color: {fg}; font-size: 13px; }}
    QPushButton {{
        background: {field}; color: {fg}; border: 1px solid {border};
        padding: 6px 14px; border-radius: 5px; min-width: 72px;
    }}
    QPushButton:hover {{ border-color: {muted}; }}
    QPushButton#destructive {{ color: {destructive}; }}
    QPushButton#destructive:hover {{ border-color: {destructive}; }}
    QPushButton:focus, QPushButton#destructive:focus {{
        border: 2px solid {ring}; padding: 5px 13px;
    }}
    {accent_button_css("QPushButton:default", is_dark)}
    QPushButton:disabled, QPushButton:default:disabled {{
        background: {field}; color: {muted}; border-color: {border};
    }}
    """


def accent_button_css(selector: str, is_dark: bool) -> str:
    """Style rules that draw the buttons ``selector`` matches as the
    accent-filled button (a dialog's default action): the fill, its hover,
    and, with the keyboard focus, a ring in the color that stands out from
    the fill (the plain ring would be blue on blue). The rule for the plain
    ring must come first or be less specific."""
    accent, accent_hover = _DIALOG_ACCENT[is_dark]
    ring = _FOCUS_RING_ON_ACCENT[is_dark]
    return (f"{selector} {{ background: {accent}; color: #FFFFFF; border-color: {accent}; }}\n"
            f"{selector}:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}\n"
            f"{selector}:focus {{ border: 2px solid {ring}; }}\n")


def focus_ring_color(is_dark: bool) -> str:
    """The color of the ring around a control with the keyboard focus, for
    dialogs that draw their own controls (link-like buttons)."""
    return _FOCUS_RING[is_dark]


def focus_ring_on_accent_color(is_dark: bool) -> str:
    """The ring's color on a control filled with the accent or its tint (a
    checked toolbar button), where the plain ring would not show."""
    return _FOCUS_RING_ON_ACCENT[is_dark]


def error_text_color(is_dark: bool) -> str:
    """Color of a short error said next to a field (why an address cannot
    be a link), the destructive red of the theme."""
    return _DIALOG_DESTRUCTIVE[is_dark]


def dialog_link_color(is_dark: bool) -> str:
    """Link color for rich-text labels in the app's dialogs.

    The palette's Link role doubles as the selection color, which is too
    dark to read as a link on the dark theme's background.
    """
    return _DIALOG_LINK[is_dark]


def attention_color(is_dark: bool) -> str:
    """The red that marks a problem (a destructive button, a failed check),
    readable on its theme's background."""
    return _DIALOG_DESTRUCTIVE[is_dark]


def check_box_border(palette: QPalette) -> QColor:
    """A check box's frame that people can see on either theme: the text
    colour over the field, about half and half, which keeps the 3:1 that
    controls need against the field and the window (Fusion's own frame
    is the window colour darkened, 1.07:1 on the dark theme)."""
    text = palette.color(QPalette.ColorRole.Text)
    base = palette.color(QPalette.ColorRole.Base)
    share = 0.55
    return QColor(round(text.red() * share + base.red() * (1 - share)),
                  round(text.green() * share + base.green() * (1 - share)),
                  round(text.blue() * share + base.blue() * (1 - share)))


class AppStyle(QProxyStyle):
    """Fusion, with check boxes and radio buttons whose frame can be seen
    on the dark theme too (review M5; Fusion's radio ring was as faint as
    its check box frame, so an unchecked choice in Create Account and in
    Settings showed no circle at all), and with every dialog's buttons in
    the order the HIG gives on every system: Cancel, then the default
    action at the trailing end, as MyEditor's alerts have them (a button
    box would otherwise put the default first on Windows and KDE, so one
    flow showed both orders). Everything else is Fusion's."""

    _BOXES = (QStyle.PrimitiveElement.PE_IndicatorCheckBox,
              QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck)
    _RADIO = QStyle.PrimitiveElement.PE_IndicatorRadioButton

    def styleHint(self, hint, option=None, widget=None, returnData=None):  # noqa: N802
        if hint == QStyle.StyleHint.SH_DialogButtonLayout:
            return QDialogButtonBox.ButtonLayout.MacLayout.value
        return super().styleHint(hint, option, widget, returnData)

    def drawPrimitive(self, element, option, painter, widget=None):  # noqa: N802
        super().drawPrimitive(element, option, painter, widget)
        if option is None or (element not in self._BOXES and element != self._RADIO):
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(check_box_border(option.palette), 1))
        frame = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)
        if element == self._RADIO:
            painter.drawEllipse(frame)
        else:
            painter.drawRoundedRect(frame, 2, 2)
        painter.restore()


def apply_app_theme(is_dark: bool) -> None:
    """Push the light/dark palette onto the running QApplication.

    Fetches the application instance itself so callers do not need to
    import QApplication or thread it through. A no-op if no application
    exists yet (e.g. during import in tests).
    """
    app = QApplication.instance()
    if app is None:
        return
    app.setPalette(_palette(is_dark))
