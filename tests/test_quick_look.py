# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Quick Look: a quiet preview in a floating panel. It says when it made
the widget it shows, so the window can wire it as it wires a tab, and a
spreadsheet in it shows nothing that edits."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from quick_look import QuickLookPanel  # noqa: E402
from tests.office_samples import make_docx  # noqa: E402
from tests.sheet_fixtures import simple_xlsx  # noqa: E402


@pytest.fixture
def panel():
    QApplication.instance() or QApplication([])
    made = QuickLookPanel(is_dark=False)
    yield made
    made.close()
    made.deleteLater()
    QCoreApplication.processEvents()


def test_the_panel_says_when_it_made_what_it_shows(tmp_path, panel):
    from viewer_tabs import FileViewerTab
    letter = make_docx(tmp_path / "Letter.docx")
    made = []
    panel.content_created.connect(made.append)
    panel.show_path(letter)
    assert len(made) == 1 and made[0] is panel.content()
    assert isinstance(made[0], FileViewerTab)
    panel.show_path(letter)                      # the same file: nothing new
    assert len(made) == 1


def test_a_spreadsheet_is_a_quiet_preview(tmp_path, panel):
    path = tmp_path / "Budget.xlsx"
    simple_xlsx(path)
    panel.show_path(str(path))
    QCoreApplication.processEvents()
    sheet = panel.content()
    assert sheet.read_only
    assert sheet._formula_bar.isHidden() and sheet._add_button.isHidden()
