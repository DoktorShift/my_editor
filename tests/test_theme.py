# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Application palette behavior and contrast guarantees."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

import theme
from constants import (
    DARK_BG,
    DARK_FG,
    DARK_MENU_BG,
    DARK_MUTED_FG,
    DARK_SELECTION,
    LIGHT_BG,
    LIGHT_FG,
    LIGHT_MENU_BG,
    LIGHT_MUTED_FG,
    LIGHT_SELECTION,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    original_palette = app.palette()
    yield app
    app.setPalette(original_palette)


@pytest.mark.parametrize(
    ("is_dark", "bg", "fg", "menu_bg", "muted_fg", "selection"),
    [
        (True, DARK_BG, DARK_FG, DARK_MENU_BG, DARK_MUTED_FG, DARK_SELECTION),
        (False, LIGHT_BG, LIGHT_FG, LIGHT_MENU_BG, LIGHT_MUTED_FG, LIGHT_SELECTION),
    ],
)
def test_palette_maps_semantic_colors(
    is_dark, bg, fg, menu_bg, muted_fg, selection
):
    palette = theme._palette(is_dark)
    role = QPalette.ColorRole
    group = QPalette.ColorGroup

    expected_roles = {
        role.Window: bg,
        role.WindowText: fg,
        role.Base: bg,
        role.AlternateBase: menu_bg,
        role.Text: fg,
        role.PlaceholderText: muted_fg,
        role.Button: menu_bg,
        role.ButtonText: fg,
        role.ToolTipBase: menu_bg,
        role.ToolTipText: fg,
        role.Highlight: selection,
        role.HighlightedText: "#FFFFFF",
        role.Link: selection,
        role.LinkVisited: selection,
        role.Accent: selection,
    }
    for color_role, expected in expected_roles.items():
        assert palette.color(group.Active, color_role) == QColor(expected)

    for color_role in (role.WindowText, role.Text, role.ButtonText):
        assert palette.color(group.Disabled, color_role) == QColor(muted_fg)


@pytest.mark.parametrize(
    ("foreground", "background"),
    [
        (DARK_MUTED_FG, DARK_BG),
        (DARK_MUTED_FG, DARK_MENU_BG),
        (LIGHT_MUTED_FG, LIGHT_BG),
        (LIGHT_MUTED_FG, LIGHT_MENU_BG),
    ],
)
def test_muted_text_meets_normal_text_contrast(foreground, background):
    assert _contrast_ratio(foreground, background) >= 4.5


@pytest.mark.parametrize("is_dark", [True, False])
def test_apply_app_theme_updates_running_application(qapp, is_dark):
    theme.apply_app_theme(is_dark)
    assert qapp.palette() == theme._palette(is_dark)


def _contrast_ratio(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


def _relative_luminance(hex_color: str) -> float:
    color = QColor(hex_color)
    channels = (color.redF(), color.greenF(), color.blueF())
    linear = [
        value / 12.92
        if value <= 0.04045
        else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


# -- the keyboard focus ring -----------------------------------------------------------
# A dialog button with the keyboard focus must be told apart from the others
# (WCAG 2.4.7), by a ring with 3:1 against what is next to it (WCAG 1.4.11).
# The stylesheet drew none, so Tab moved through alerts and assistants with
# nothing to show where Space would act.

@pytest.mark.parametrize("is_dark", [True, False])
def test_the_focus_ring_stands_out_from_the_button_and_the_window(is_dark):
    bg, field = (DARK_BG, DARK_MENU_BG) if is_dark else (LIGHT_BG, LIGHT_MENU_BG)
    accent = theme._DIALOG_ACCENT[is_dark][0]
    ring, on_accent = theme._FOCUS_RING[is_dark], theme._FOCUS_RING_ON_ACCENT[is_dark]
    assert _contrast_ratio(ring, field) >= 3
    assert _contrast_ratio(ring, bg) >= 3
    assert _contrast_ratio(on_accent, accent) >= 3      # around the default button
    assert _contrast_ratio(on_accent, bg) >= 3
    assert theme.focus_ring_color(is_dark) == ring


@pytest.mark.parametrize("is_dark", [True, False])
def test_a_focused_dialog_button_is_drawn_with_the_ring(qapp, is_dark):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton

    dialog = QDialog()
    dialog.setStyleSheet(theme.dialog_stylesheet(is_dark))
    row = QHBoxLayout(dialog)
    plain, default = QPushButton("Cancel"), QPushButton("Save")
    for button in (plain, default):
        button.setAutoDefault(False)    # as the app's dialogs make them
    default.setDefault(True)
    row.addWidget(plain)
    row.addWidget(default)
    dialog.show()
    dialog.activateWindow()
    qapp.processEvents()
    try:
        for button, ring in ((plain, theme._FOCUS_RING[is_dark]),
                             (default, theme._FOCUS_RING_ON_ACCENT[is_dark])):
            size_before = button.size()
            button.setFocus(Qt.TabFocusReason)
            qapp.processEvents()
            edge = button.grab().toImage().pixelColor(1, button.height() // 2)
            assert edge == QColor(ring), button.text()
            assert button.size() == size_before        # the ring takes no extra room
    finally:
        dialog.close()


@pytest.mark.parametrize("base", ["Fusion", "Windows"])
def test_every_button_box_puts_cancel_first_and_the_default_trailing(qapp, base):
    # Review L8: a button box follows the platform's order, which on Windows
    # and KDE puts the default first, while the alerts follow the HIG on
    # every system; one flow showed both orders. The app's style decides.
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout

    dialog = QDialog()
    dialog.setStyle(theme.AppStyle(base))
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                           | QDialogButtonBox.StandardButton.Cancel
                           | QDialogButtonBox.StandardButton.Help)
    box.setStyle(dialog.style())
    QVBoxLayout(dialog).addWidget(box)
    dialog.show()
    qapp.processEvents()
    try:
        left_to_right = sorted(box.buttons(),
                               key=lambda b: b.mapTo(dialog, b.rect().topLeft()).x())
        roles = [box.buttonRole(b) for b in left_to_right]
        assert roles == [QDialogButtonBox.ButtonRole.HelpRole,
                         QDialogButtonBox.ButtonRole.RejectRole,
                         QDialogButtonBox.ButtonRole.AcceptRole]
    finally:
        dialog.close()


def test_a_window_given_a_style_sheet_is_polished_at_once():
    """Qt keeps a window's parsed style sheet under the window's address
    once anything in it is measured, and forgets it only for a window that
    was polished. A window given a style sheet and dropped unshown (a
    dialog built and not needed) left its style under that address, and
    the next window allocated there drew with it: on Linux, Publish as
    Note's Publish came out in the Media Library's grey after the public
    copy dialog had been built (review M1). The app polishes a widget as
    soon as it is given a style sheet (theme.install_style_sheet_guard,
    which main.py and the tests install)."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout
    dialog = QDialog()
    QVBoxLayout(dialog).addWidget(QLabel("x"))
    dialog.setStyleSheet("QLabel { color: #2D2D30; }")
    assert dialog.testAttribute(Qt.WidgetAttribute.WA_WState_Polished)
    import inspect
    import main
    assert "install_style_sheet_guard(app)" in inspect.getsource(main)


def test_a_widget_given_a_style_sheet_under_a_styled_parent_is_polished_too():
    """Review round 2: Qt sends nothing to a widget given a style sheet
    while it sits under a styled parent, so the guard's filter missed it;
    every style sheet set from Python is covered now."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QFrame
    parent = QDialog()
    parent.setStyleSheet("QLabel { }")
    child = QFrame(parent)
    child.setStyleSheet("QPushButton { color: #2D2D30; }")
    assert child.testAttribute(Qt.WidgetAttribute.WA_WState_Polished)


def test_every_style_sheet_goes_through_the_one_that_polishes():
    """Review round 3, low 5: the guard's application-wide event filter was
    redundant beside the setStyleSheet wrapper, and cost something on every
    event; it is gone. What holds instead: the wrapper is installed (a
    keyword call and a reload of theme keep working), a widget whose style
    sheet was set before it is polished by the helper, and no module sets
    an application style sheet or loads .ui files, which would set style
    sheets past the wrapper."""
    import importlib
    import pathlib
    import re
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QFrame, QWidget
    assert theme.style_sheet_guard_installed()
    assert not hasattr(QApplication.instance(), "_style_sheet_guard")    # no filter
    frame = QFrame()
    frame.setStyleSheet(styleSheet="QFrame { border: none; }")
    assert frame.testAttribute(Qt.WidgetAttribute.WA_WState_Polished)
    early = QFrame()
    theme._set_style_sheet(early, "QFrame { border: none; }")         # past the wrapper
    assert not early.testAttribute(Qt.WidgetAttribute.WA_WState_Polished)
    theme.polish_style_sheets(early)
    assert early.testAttribute(Qt.WidgetAttribute.WA_WState_Polished)
    importlib.reload(theme)
    theme.install_style_sheet_guard(QApplication.instance())
    again = QFrame()
    again.setStyleSheet("QFrame { border: none; }")                  # no recursion
    assert again.testAttribute(Qt.WidgetAttribute.WA_WState_Polished)
    assert QWidget.setStyleSheet is not theme._set_style_sheet
    root = pathlib.Path(__file__).resolve().parent.parent
    banned = re.compile(r"(QApplication|QCoreApplication|\bapp|instance\(\))\.setStyleSheet\("
                        r"|QtUiTools|loadUiType\(")
    found = [str(path.relative_to(root)) for path in sorted(root.glob("*.py"))
             + sorted((root / "nostr").rglob("*.py")) + sorted((root / "spelling").rglob("*.py"))
             if banned.search(path.read_text(encoding="utf-8"))]
    assert found == []


def test_the_live_flows_draw_as_the_app_does():
    import inspect
    from tests import smoke_ndak_common
    source = inspect.getsource(smoke_ndak_common.qt_app)
    assert "install_style_sheet_guard" in source and "AppStyle" in source
