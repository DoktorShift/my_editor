# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the Media Library's actions and words.

What must hold:

  What acts on chosen files (Copy URL, Download, Open in Browser, Delete;
  the picker's Insert) is dimmed while none is chosen, as the HIG asks of
  a command that cannot act. They used to answer "Select a file first."
  after the click, with Delete always red and ready.

  Delete is a destructive button the way alerts show one: red words on an
  ordinary button, not a red slab that outshouts everything around it.

  The window is named the way its menu command is (Media Library, Insert
  Image), and speaks of media servers, not of the protocol's name.

  Every control has a name a screen reader can announce.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog, QLabel  # noqa: E402

from nostr.ui import media_library_dialog as dialog_module  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402
from test_media_library_visibility import build_dialog, media  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_the_actions_wait_for_a_chosen_file(tmp_path, monkeypatch):
    dialog = build_dialog(tmp_path, monkeypatch, records=[media()])
    actions = (dialog._copy_btn, dialog._download_btn, dialog._open_btn, dialog._delete_btn)
    assert not any(button.isEnabled() for button in actions)
    dialog._grid.setCurrentRow(0)
    assert all(button.isEnabled() for button in actions)
    dialog._grid.clearSelection()
    assert not any(button.isEnabled() for button in actions)


def test_the_pickers_insert_waits_for_a_chosen_file(tmp_path, monkeypatch):
    dialog = build_dialog(tmp_path, monkeypatch, records=[media()], pick_mode=True)
    assert not dialog._select_btn.isEnabled()
    dialog._grid.setCurrentRow(0)
    assert dialog._select_btn.isEnabled()
    assert dialog.windowTitle() == "Insert Image"


def test_delete_is_red_words_not_a_red_slab(tmp_path, monkeypatch):
    dialog = build_dialog(tmp_path, monkeypatch, records=[media()])
    assert dialog.windowTitle() == "Media Library"
    css = dialog.styleSheet()
    rule = css[css.index("QPushButton#media_destructive {"):]
    rule = rule[:rule.index("}")]
    assert "background" not in rule and "color:" in rule


def test_no_protocol_name_on_screen(tmp_path, monkeypatch):
    dialog = build_dialog(tmp_path, monkeypatch, records=[])
    words = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "Blossom" not in words and "Blossom" not in dialog.windowTitle()
    asked = []
    monkeypatch.setattr(dialog_module.QFileDialog, "getOpenFileNames",
                        staticmethod(lambda parent, title, *a, **k: asked.append(title) or
                                     ([], "")))
    dialog._on_upload_clicked()
    assert asked == ["Choose Files to Upload"]


@pytest.mark.parametrize("pick_mode", [False, True])
def test_every_control_has_a_name(tmp_path, monkeypatch, pick_mode):
    dialog = build_dialog(tmp_path, monkeypatch, records=[media()], pick_mode=pick_mode)
    assert unnamed_controls(dialog) == []
