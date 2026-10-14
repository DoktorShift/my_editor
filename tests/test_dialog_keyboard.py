# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins that a person on the keyboard reaches every control of MyEditor's
main dialogs and windows.

What must hold:

  Every control shown (on the page up) takes the focus with Tab: none is
  left to the pointer only.

  Save As's choice between a file and a private Nostr draft is reached
  with Tab, and the arrow keys move the choice and the focus together.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.accessibility import out_of_tab_reach  # noqa: E402
from tests.test_accessibility import WINDOWS  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def save_destination(tmp_path):
    from nostr.ui.save_destination_dialog import SaveDestinationDialog
    return SaveDestinationDialog()


def page_setup(tmp_path):
    from page_setup_dialog import PageSetupDialog
    return PageSetupDialog(is_dark=False)


def settings(tmp_path):
    from tests.test_settings_window import Fixture
    return Fixture(nostr=True).window


def publish_note(tmp_path):
    from tests.test_publish_job import _dialog
    return _dialog("note", tmp_path, None)


def publish_article(tmp_path):
    from tests.test_publish_job import _dialog
    return _dialog("article", tmp_path, None)


DIALOGS = [save_destination, page_setup, settings, publish_note, publish_article, *WINDOWS]


@pytest.mark.parametrize("make", DIALOGS, ids=[d.__name__ for d in DIALOGS])
def test_tab_reaches_every_control(make, tmp_path):
    window = make(tmp_path)
    try:
        assert out_of_tab_reach(window) == []
    finally:
        window.close()
        window.deleteLater()


def test_the_save_destination_is_chosen_from_the_keyboard(qt_app):
    from nostr.ui.save_destination_dialog import SaveDestination, SaveDestinationDialog
    dialog = SaveDestinationDialog()
    dialog.show()
    qt_app.processEvents()
    dialog._local_card.radio.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(qt_app.focusWidget(), Qt.Key.Key_Down)
    assert dialog._destination is SaveDestination.NOSTR_DRAFT
    assert qt_app.focusWidget() is dialog._draft_card.radio
    QTest.keyClick(qt_app.focusWidget(), Qt.Key.Key_Up)
    assert dialog._destination is SaveDestination.LOCAL
    dialog._draft_card.radio.click()
    assert dialog._destination is SaveDestination.NOSTR_DRAFT
    dialog.close()
    dialog.deleteLater()
