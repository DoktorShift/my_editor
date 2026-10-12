# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins how Save as Nostr Draft is used without a mouse.

What must hold:

  The kinds (Short note, Long-form article) are a radio group the
  keyboard reaches: Tab gets to the chosen one and the arrow keys choose
  the other, which opens the article's fields. Each is named for a screen
  reader. (Review R4: the cards answered the mouse only, and the dialog
  opened with the focus on Cancel.)
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.ui.stash_kind_dialog import StashKindDialog  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def shown():
    dialogs = []

    def show(**kwargs):
        dialog = StashKindDialog(**kwargs)
        dialog.show()
        QApplication.processEvents()
        dialogs.append(dialog)
        return dialog

    yield show
    for dialog in dialogs:
        dialog.close()


def test_the_kinds_are_a_radio_group_the_keyboard_reaches(shown):
    dialog = shown()
    note, article = dialog._note_card.radio, dialog._article_card.radio
    assert note.focusPolicy() & Qt.FocusPolicy.TabFocus
    note.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(note, Qt.Key.Key_Right)          # the cards sit side by side
    QApplication.processEvents()
    assert article.isChecked() and article.hasFocus()
    assert dialog._article_panel.isVisible()
    QTest.keyClick(article, Qt.Key.Key_Left)
    assert note.isChecked() and not dialog._article_panel.isVisible()
    assert unnamed_controls(dialog) == []
