# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins that Publish as Note and Publish as Article show what the keyboard
will do, as every other dialog does.

What must hold, light and dark: Publish, the default, is drawn in the
accent, and the button with the keyboard focus has the ring, Cancel
included. (Review R3: their own stylesheets drew both buttons alike and
no focus at all, in the two dialogs whose action cannot be taken back.)
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import alerts  # noqa: E402
import theme  # noqa: E402
from tests.test_publish_job import _dialog  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def edge(button):
    return button.grab().toImage().pixelColor(1, button.height() // 2)


def middle(button):
    return button.grab().toImage().pixelColor(4, button.height() // 2)


@pytest.mark.parametrize("dark", [True, False])
@pytest.mark.parametrize("kind", ["note", "article"])
def test_the_default_and_the_focus_can_be_seen(tmp_path, monkeypatch, kind, dark):
    monkeypatch.setattr(alerts, "IS_MAC", True)       # the default stays on Publish
    dialog = _dialog(kind, tmp_path, None)
    dialog._is_dark = dark
    dialog._apply_theme()
    if kind == "article":
        dialog._title_edit.setText("My title")     # Publish needs a title
    dialog.show()
    QApplication.processEvents()
    try:
        publish, cancel = dialog._publish_btn, dialog._cancel_btn
        assert publish.isDefault()
        assert middle(publish) == QColor(theme._DIALOG_ACCENT[dark][0])
        assert middle(cancel) != middle(publish)
        unfocused = edge(cancel)
        cancel.setFocus(Qt.FocusReason.TabFocusReason)
        QApplication.processEvents()
        assert edge(cancel) == QColor(theme.focus_ring_color(dark))
        assert edge(cancel) != unfocused
    finally:
        dialog.close()
