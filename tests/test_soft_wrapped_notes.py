# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A note whose paragraphs run over several lines (hard-wrapped prose, a
picture on the line under its words) opens formatted, as every note
does, however it is opened, and is saved back exactly as it was."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.office_samples import picture_bytes  # noqa: E402
from tests.window_fixture import close_window, make_window  # noqa: E402

NOTE = ("# Plan for October\n\nFinish the chapter on harbours and send it to Anna.\n"
        "![Beach](Pictures/Beach.png)\n")


@pytest.fixture
def note(tmp_path):
    folder = tmp_path / "Library"
    (folder / "Pictures").mkdir(parents=True)
    (folder / "Pictures" / "Beach.png").write_bytes(picture_bytes())
    path = folder / "Plan for October.md"
    path.write_bytes(NOTE.encode("utf-8"))
    return str(path)


@pytest.fixture
def window(tmp_path, monkeypatch, note):
    QApplication.instance() or QApplication([])
    w = make_window(tmp_path, monkeypatch,
                    settings_values={"library_folders": [os.path.dirname(note)]})
    yield w
    close_window(w)


def _is_rich(window, ed):
    return window._editor_kind(ed) == "rich" and not getattr(ed, "_markdown_source", False)


def test_a_soft_wrapped_note_opens_formatted_from_file_open(window, note):
    ed = window.open_path(note)
    assert _is_rich(window, ed)
    assert ed.document().begin().text() == "Plan for October"


def test_it_opens_the_same_from_the_list_a_double_click_and_quick_look(window, note):
    window._library_window.show_from_list(note)
    from_list = window.current_editor()
    assert _is_rich(window, from_list)
    text = from_list.toPlainText()
    window.close_tab(window.tabs.currentIndex())
    window._library_window.open_from_list(note)
    assert _is_rich(window, window.current_editor())
    assert window.current_editor().toPlainText() == text


def test_saving_it_unchanged_writes_the_same_bytes(window, note):
    ed = window.open_path(note)
    ed.document().setModified(True)
    assert window.save()
    with open(note, "rb") as f:
        assert f.read() == NOTE.encode("utf-8")
