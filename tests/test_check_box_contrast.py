# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check boxes can be seen on both themes (review M5).

Fusion draws a check box's frame in the window colour darkened, 1.07:1
on the dark theme: the Imports list's main control was nearly
invisible. The app's style keeps Fusion and gives the frame 3:1, what
controls need against what surrounds them.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtWidgets import QCheckBox, QStyle, QStyleFactory, QStyleOptionButton

import theme
from tests.widget_lifetime import delete_new_windows


@pytest.fixture(autouse=True)
def _windows_deleted():
    """Every window and panel a test makes is deleted after it: left to
    the cycle collector, one without a parent can crash it."""
    yield from delete_new_windows()


def luminance(color: QColor) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return (0.2126 * channel(color.red()) + 0.7152 * channel(color.green())
            + 0.0722 * channel(color.blue()))


def contrast(a: QColor, b: QColor) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


@pytest.mark.parametrize("dark", [True, False])
def test_the_frame_keeps_three_to_one(dark):
    palette = theme._palette(dark)
    frame = theme.check_box_border(palette)
    for role in (QPalette.ColorRole.Base, QPalette.ColorRole.Window):
        assert contrast(frame, palette.color(role)) >= 3.0


def edge_contrast(style) -> float:
    """How the top edge of an unchecked box stands out from the dark
    window, as ``style`` draws it."""
    palette = theme._palette(True)
    box = QCheckBox()
    option = QStyleOptionButton()
    option.initFrom(box)
    option.palette = palette
    option.rect = QRect(2, 2, 16, 16)
    option.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Off
    image = QImage(20, 20, QImage.Format.Format_ARGB32)
    image.fill(palette.color(QPalette.ColorRole.Window))
    painter = QPainter(image)
    style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorCheckBox, option, painter, box)
    painter.end()
    return contrast(image.pixelColor(QPoint(10, 2)), palette.color(QPalette.ColorRole.Window))


def test_the_app_style_draws_the_frame():
    assert edge_contrast(QStyleFactory.create("Fusion")) < 1.5      # what it was
    assert edge_contrast(theme.AppStyle("Fusion")) >= 2.5
