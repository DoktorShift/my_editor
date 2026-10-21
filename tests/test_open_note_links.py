# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The links of an open note with unsaved changes follow a rename or move
in the note's text (open_note_links.py, MainWindow._follow_file_moves):
the library writes no such note, so nothing is left pointing at the old
place, what was typed stays, and one Undo takes the change back. Tabs
follow their files whatever Unicode form or case the path was written
in, and the progress of a link update never stays behind."""

from __future__ import annotations

import os
import unicodedata

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import library_actions  # noqa: E402
import open_note_links  # noqa: E402
from tests.office_samples import picture_bytes  # noqa: E402
from tests.window_fixture import close_window, make_window  # noqa: E402


def settle(rounds=5):
    for _ in range(rounds):
        QApplication.processEvents()


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "Library"
    (root / "sub").mkdir(parents=True)
    (root / "pic.png").write_bytes(picture_bytes())
    (root / "other.md").write_text("hello\n", encoding="utf-8")
    (root / "n.md").write_text("# N\n\n![p](pic.png)\n\nSee [other](other.md).\n",
                               encoding="utf-8")
    return str(root)


@pytest.fixture
def window(tmp_path, monkeypatch, library):
    QApplication.instance() or QApplication([])
    w = make_window(tmp_path, monkeypatch, settings_values={"library_folders": [library]})
    yield w
    close_window(w)


def _typed(window, path):
    window.open_path(path)
    ed = window._editor_from_widget(window.tabs.currentWidget())
    cursor = ed.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    cursor.insertText("more typing")
    assert ed.document().isModified()
    return ed


def _move(window, paths, folder):
    said = []
    library_actions.move(window, window._library, paths, folder,
                         window._library_window.unsaved_notes,
                         said.append)
    window._library.wait_for_links()
    settle()
    return said


def test_a_moved_note_with_changes_keeps_its_links_and_its_typing(window, library):
    ed = _typed(window, os.path.join(library, "n.md"))
    said = _move(window, [os.path.join(library, "n.md")], os.path.join(library, "sub"))
    moved = os.path.join(library, "sub", "n.md")
    assert ed._file_path == moved and ed.document().isModified()
    assert not any("not updated" in s for s in said)
    assert window.save()
    text = open(moved, encoding="utf-8").read()
    assert "![p](../pic.png)" in text and "[other](../other.md)" in text
    assert "more typing" in text


def test_one_undo_takes_the_link_update_back(window, library):
    ed = _typed(window, os.path.join(library, "n.md"))
    _move(window, [os.path.join(library, "pic.png")], os.path.join(library, "sub"))
    names = [f.toImageFormat().name() for f in _formats(ed) if f.isImageFormat()]
    assert names == ["sub/pic.png"]
    assert ed.undo_name() == "Update Links"
    ed.undo()
    names = [f.toImageFormat().name() for f in _formats(ed) if f.isImageFormat()]
    assert names == ["pic.png"]


def _formats(ed):
    doc = ed.document()
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            yield it.fragment().charFormat()
            it += 1
        block = block.next()


def test_a_renamed_note_with_changes_points_at_its_renamed_pictures_folder(window, library):
    media = os.path.join(library, "n_media")
    os.makedirs(media)
    with open(os.path.join(media, "a.png"), "wb") as f:
        f.write(picture_bytes())
    with open(os.path.join(library, "n.md"), "w", encoding="utf-8") as f:
        f.write("# N\n\n![a](n_media/a.png)\n")
    ed = _typed(window, os.path.join(library, "n.md"))
    window._library.rename(os.path.join(library, "n.md"), "m.md",
                           skip_notes=window._library_window.unsaved_notes())
    window._library.wait_for_links()
    settle()
    assert ed._file_path == os.path.join(library, "m.md")
    assert window.save()
    assert "![a](m_media/a.png)" in open(os.path.join(library, "m.md"), encoding="utf-8").read()


def test_a_note_shown_as_its_text_follows_too(window, library):
    with open(os.path.join(library, "r.Rmd"), "w", encoding="utf-8") as f:
        f.write("Plot ![p](pic.png) and ünïcode ✓ text\n")
    ed = _typed(window, os.path.join(library, "r.Rmd"))
    assert window._editor_kind(ed) == "source"
    _move(window, [os.path.join(library, "pic.png")], os.path.join(library, "sub"))
    assert "![p](sub/pic.png) and ünïcode ✓ text" in ed.toPlainText()
    assert ed.toPlainText().endswith("more typing")


def test_a_tab_follows_its_file_whatever_unicode_form_it_was_opened_with(window, library):
    nfd = os.path.join(library, unicodedata.normalize("NFD", "Müller.md"))
    with open(nfd, "w", encoding="utf-8") as f:
        f.write("hello\n")
    nfc = os.path.join(library, unicodedata.normalize("NFC", "Müller.md"))
    if not os.path.exists(nfc):
        # Only where the disk takes both spellings for one name (APFS and
        # HFS+ on a Mac); elsewhere the other spelling is another file.
        pytest.skip("this disk tells the two Unicode forms of a name apart")
    ed = window.open_path(nfc)
    window._library.rename(nfd, "Meier.md", skip_notes=window._library_window.unsaved_notes())
    window._library.wait_for_links()
    settle()
    assert os.path.basename(ed._file_path) == "Meier.md"


def test_the_progress_of_a_link_update_goes_when_it_is_done(window):
    from library_set import LinkReport
    window._library_window.links_progress(0, 5)
    assert window.status.currentMessage().startswith("Updating links")
    window._library_window.links_done(LinkReport())
    assert window.status.currentMessage() == ""
    window._library_window.links_progress(0, 5)
    window.status.showMessage("Updated links in 2 notes.")
    window._library_window.links_done(LinkReport())
    assert window.status.currentMessage() == "Updated links in 2 notes."


def test_a_destination_is_rebased_the_way_it_was_written(tmp_path):
    (tmp_path / "a b.png").write_bytes(b"x")
    old = str(tmp_path / "n.md")
    new = str(tmp_path / "sub" / "n.md")
    assert open_note_links.destination_after("a%20b.png", old, new, {old: new}) == "../a%20b.png"
    assert open_note_links.destination_after("a b.png#x", old, new, {old: new}) == "../a b.png#x"
    assert open_note_links.destination_after("https://x.org/a.png", old, new, {old: new}) is None
    assert open_note_links.destination_after("missing.png", old, new, {old: new}) is None
