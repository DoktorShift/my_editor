# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the one segmented control both switches are made of (Write ·
Preview · Split in the toolbar, Drafts · Published in the panel).

What must hold, from Apple's guidelines for segmented controls:

  Segments have equal widths. The chosen segment sits on a raised,
  neutral thumb (never the accent fill a button uses), the others are
  quieter text on the track. Every text pair is readable (4.5:1). The
  arrow keys move the choice, Tab reaches the control on the chosen
  segment only, and each segment has a name and a tooltip. A label that
  does not fit is shortened with an ellipsis, never clipped.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QAction, QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from constants import DARK_MENU_BG, DARK_SELECTION, LIGHT_MENU_BG, LIGHT_SELECTION  # noqa: E402
from segmented_control import SegmentedControl, colors  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    yield QApplication.instance() or QApplication(sys.argv)


def control(dark=False, items=(("drafts", "Drafts", "Your drafts"),
                               ("published", "Published", "What you published"))):
    widget = SegmentedControl(dark=dark)
    widget.set_segments(items)
    widget.set_current(items[0][0])
    widget.resize(widget.sizeHint())
    widget.show()
    QApplication.processEvents()
    return widget


def luminance(colour: QColor) -> float:
    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    return (0.2126 * channel(colour.redF()) + 0.7152 * channel(colour.greenF())
            + 0.0722 * channel(colour.blueF()))


def over(top: QColor, bottom: QColor) -> QColor:
    """``top`` composited over the opaque ``bottom``."""
    a = top.alphaF()
    return QColor.fromRgbF(top.redF() * a + bottom.redF() * (1 - a),
                           top.greenF() * a + bottom.greenF() * (1 - a),
                           top.blueF() * a + bottom.blueF() * (1 - a))


def contrast(a: QColor, b: QColor) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_segments_are_equally_wide_and_the_chosen_one_is_checked():
    widget = control()
    drafts, published = widget.segments
    assert drafts.width() == published.width() > 0
    assert drafts.isChecked() and not published.isChecked()
    assert widget.current() == "drafts"


def test_a_click_chooses_and_says_which():
    widget = control()
    heard = []
    widget.chosen.connect(heard.append)
    widget.segments[1].click()
    assert heard == ["published"] and widget.current() == "published"
    assert [s.isChecked() for s in widget.segments] == [False, True]


def test_the_arrow_keys_move_the_choice_and_tab_reaches_only_the_chosen_segment():
    widget = control(items=(("w", "Write", ""), ("p", "Preview", ""), ("s", "Split", "")))
    heard = []
    widget.chosen.connect(heard.append)
    first, second, third = widget.segments
    assert first.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert second.focusPolicy() == third.focusPolicy() == Qt.FocusPolicy.ClickFocus
    widget.set_segment_enabled("p", False)
    widget.activateWindow()
    first.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(first, Qt.Key.Key_Right)
    assert heard == ["s"], "a dimmed segment is passed over"
    assert third.hasFocus() and third.focusPolicy() == Qt.FocusPolicy.StrongFocus
    QTest.keyClick(third, Qt.Key.Key_Left)
    assert heard == ["s", "w"]


def test_every_segment_has_a_name_and_a_tooltip():
    widget = control()
    assert [s.accessibleName() for s in widget.segments] == ["Drafts", "Published"]
    assert widget.segments[0].toolTip() == "Drafts: Your drafts"


def test_a_label_too_long_for_its_segment_ends_in_an_ellipsis():
    widget = control(items=(("a", "A rather long view name", ""), ("b", "B", "")))
    widget.resize(90, widget.height())
    QApplication.processEvents()
    painted = widget.segments[0].painted_text()
    assert painted.endswith("…") and len(painted) < len("A rather long view name")


@pytest.mark.parametrize("dark", [True, False])
def test_the_chosen_segment_is_neutral_and_every_label_is_readable(dark):
    palette = colors(dark)
    band = QColor(DARK_MENU_BG if dark else LIGHT_MENU_BG)
    track = over(palette.track, band)
    # Neutral: the thumb is not the selection blue a button or a list uses.
    assert palette.thumb.name() not in (DARK_SELECTION.lower(), LIGHT_SELECTION.lower())
    assert contrast(palette.text, palette.thumb) >= 4.5
    assert contrast(palette.muted, track) >= 4.5
