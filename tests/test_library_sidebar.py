# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the folder sidebar: the Library rows and their counts, the
folders two levels deep and no deeper, the empty state, what a choice
says, the folder menu, and that files dropped on a folder move there
(from the list) or are copied in (from the Finder).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication, QMimeData, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

import library_actions  # noqa: E402
from library_set import LibrarySet  # noqa: E402
from library_sidebar import (  # noqa: E402
    FOLDER, HEADER, LIBRARY, LibrarySidebar, Row, accessible_text,
)
from tests.accessibility import unnamed_controls  # noqa: E402
from tests.waiting import wait_until  # noqa: E402


def write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def libraries():
    made = LibrarySet()
    yield made
    for root in made.roots():
        made.remove_root(root)
    made.deleteLater()
    QCoreApplication.processEvents()


@pytest.fixture
def sidebar(libraries):
    widget = LibrarySidebar(libraries, is_dark=False)
    widget.resize(240, 600)
    widget.show()
    yield widget
    widget.close()
    widget.deleteLater()
    QCoreApplication.processEvents()


def settle(libraries, sidebar):
    assert wait_until(lambda: not libraries.is_indexing())
    QCoreApplication.processEvents()
    sidebar.rebuild()


def rows(sidebar):
    return sidebar.model_.rows()


def row_titles(sidebar, kind):
    return [r.title for r in rows(sidebar) if r.kind == kind]


def counts(sidebar):
    return {r.key: r.count for r in rows(sidebar) if r.kind == LIBRARY}


def drop(sidebar, folder, mime, modifiers=Qt.KeyboardModifier.NoModifier):
    number = sidebar.model_.row_of(FOLDER, folder)
    point = QPointF(sidebar.view.visualRect(sidebar.model_.index(number)).center())
    actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction
    # A drag enters before it moves: Qt delivers moves to the widget entered.
    for kind in (QDragEnterEvent, QDragMoveEvent):
        QCoreApplication.sendEvent(sidebar.view.viewport(), kind(
            point.toPoint(), actions, mime, Qt.MouseButton.LeftButton, modifiers))
    highlighted = sidebar.drop_target()
    event = QDropEvent(point, actions, mime, Qt.MouseButton.LeftButton, modifiers)
    QCoreApplication.sendEvent(sidebar.view.viewport(), event)
    return highlighted


def test_with_no_folder_the_sidebar_offers_to_add_one_at_the_top(sidebar):
    assert sidebar.stack.currentWidget() is sidebar.empty
    assert sidebar.empty_label.wordWrap()
    asked = []
    sidebar.add_folder_requested.connect(lambda: asked.append(True))
    sidebar.empty_button.click()
    assert asked == [True]
    assert sidebar.empty_button.y() < sidebar.height() / 2


def test_the_library_rows_count_every_folder(tmp_path, libraries, sidebar):
    write(tmp_path / "a" / "One.md")
    write(tmp_path / "a" / "notes.txt")
    write(tmp_path / "a" / "pic.jpg")
    write(tmp_path / "b" / "song.mp3")
    write(tmp_path / "b" / "doc.pdf")
    write(tmp_path / "b" / "table.xlsx")
    write(tmp_path / "b" / "thing.xyz")
    libraries.add_root(str(tmp_path / "a"))
    libraries.add_root(str(tmp_path / "b"))
    settle(libraries, sidebar)
    assert sidebar.stack.currentWidget() is sidebar.view
    assert row_titles(sidebar, HEADER) == ["Library", "Folders"]
    assert counts(sidebar) == {"recents": 7, "notes": 2, "media": 2, "documents": 3}


def test_folders_show_two_levels_and_no_more(tmp_path, libraries, sidebar):
    write(tmp_path / "root" / "top.md")
    write(tmp_path / "root" / "Drafts" / "a.md")
    write(tmp_path / "root" / "Drafts" / "Old" / "b.md")
    (tmp_path / "root" / "Research").mkdir()
    libraries.add_root(str(tmp_path / "root"))
    settle(libraries, sidebar)
    folders = [(r.title, r.level, r.count) for r in rows(sidebar) if r.kind == FOLDER]
    # Each counts its own files, as the file list shows them when it is chosen.
    assert folders == [("root", 1, 1), ("Drafts", 2, 1), ("Research", 2, 0)]
    sidebar.expand(str(tmp_path / "root"), False)
    assert [r.title for r in rows(sidebar) if r.kind == FOLDER] == ["root"]


def test_a_deeper_folder_is_shown_by_the_subfolder_that_holds_it(tmp_path, libraries, sidebar):
    (tmp_path / "root" / "Drafts" / "Old").mkdir(parents=True)
    libraries.add_root(str(tmp_path / "root"))
    sidebar.expand(str(tmp_path / "root"), False)
    assert sidebar.select("folder", str(tmp_path / "root" / "Drafts" / "Old"))
    assert sidebar.current() == ("folder", str(tmp_path / "root" / "Drafts"))


def test_choosing_a_row_says_what_to_show(tmp_path, libraries, sidebar):
    (tmp_path / "root" / "Drafts").mkdir(parents=True)
    libraries.add_root(str(tmp_path / "root"))
    sidebar.rebuild()
    said = []
    sidebar.selection_changed.connect(lambda kind, path: said.append((kind, path)))
    sidebar.view.setCurrentIndex(sidebar.model_.index(sidebar.model_.row_of(LIBRARY, "notes")))
    sidebar.view.setCurrentIndex(sidebar.model_.index(
        sidebar.model_.row_of(FOLDER, str(tmp_path / "root" / "Drafts"))))
    assert said == [("notes", ""), ("folder", str(tmp_path / "root" / "Drafts"))]
    assert sidebar.select("media", announce=True)
    assert said[-1] == ("media", "")


def test_the_keyboard_moves_opens_and_closes(tmp_path, libraries, sidebar):
    (tmp_path / "root" / "Drafts").mkdir(parents=True)
    root = str(tmp_path / "root")
    libraries.add_root(root)
    sidebar.select("folder", root)
    sidebar.view.setFocus()
    QTest.keyClick(sidebar.view, Qt.Key.Key_Left)
    assert [r.title for r in rows(sidebar) if r.kind == FOLDER] == ["root"]
    QTest.keyClick(sidebar.view, Qt.Key.Key_Right)
    assert len([r for r in rows(sidebar) if r.kind == FOLDER]) == 2
    QTest.keyClick(sidebar.view, Qt.Key.Key_Down)
    assert sidebar.current() == ("folder", str(tmp_path / "root" / "Drafts"))
    activated = []
    sidebar.activated.connect(lambda kind, path: activated.append(path))
    QTest.keyClick(sidebar.view, Qt.Key.Key_Return)
    assert activated == [str(tmp_path / "root" / "Drafts")]
    QTest.keyClick(sidebar.view, Qt.Key.Key_Up)
    QTest.keyClick(sidebar.view, Qt.Key.Key_Up)
    assert sidebar.current()[0] == "documents"


def test_a_top_folder_is_removed_from_the_sidebar_not_trashed(tmp_path, libraries, sidebar):
    (tmp_path / "root" / "Drafts").mkdir(parents=True)
    root = str(tmp_path / "root")
    libraries.add_root(root)
    menu = sidebar.menu_for(sidebar.model_.rows()[sidebar.model_.row_of(FOLDER, root)])
    names = [a.objectName() for a in menu.actions() if a.objectName()]
    assert names == ["new_note", "new_folder", "add_files", "reveal", "copy_path", "remove_root"]
    next(a for a in menu.actions() if a.objectName() == "remove_root").trigger()
    assert libraries.roots() == []
    assert os.path.isdir(root)
    sub_menu = sidebar.menu_for(None)
    assert [a.objectName() for a in sub_menu.actions()] == ["add_folder"]


def test_a_subfolder_menu_renames_and_links_follow_without_asking(tmp_path, libraries, sidebar,
                                                                 monkeypatch):
    write(tmp_path / "root" / "Pics" / "pic.png")
    note = write(tmp_path / "root" / "Note.md", "![](Pics/pic.png)\n")
    libraries.add_root(str(tmp_path / "root"))
    sidebar.rebuild()
    pics = str(tmp_path / "root" / "Pics")
    sidebar.select("folder", pics)
    monkeypatch.setattr(library_actions, "ask_name", lambda *a: "Photos")
    said, chosen = [], []
    sidebar.status_message.connect(said.append)
    sidebar.selection_changed.connect(lambda kind, path: chosen.append(path))
    menu = sidebar.menu_for(sidebar.model_.rows()[sidebar.model_.row_of(FOLDER, pics)])
    next(a for a in menu.actions() if a.objectName() == "rename").trigger()
    assert note.read_text(encoding="utf-8") == "![](Photos/pic.png)\n"
    assert said == ["Updated links in 1 note."]
    assert chosen == [str(tmp_path / "root" / "Photos")]


def test_unsaved_open_notes_are_named_in_the_message(tmp_path, libraries, sidebar, monkeypatch):
    write(tmp_path / "root" / "Pics" / "pic.png")
    note = write(tmp_path / "root" / "Note.md", "![](Pics/pic.png)\n")
    libraries.add_root(str(tmp_path / "root"))
    sidebar.unsaved_notes = lambda: {str(note)}
    monkeypatch.setattr(library_actions, "ask_name", lambda *a: "Photos")
    said = []
    sidebar.status_message.connect(said.append)
    sidebar.rename_folder(str(tmp_path / "root" / "Pics"))
    assert note.read_text(encoding="utf-8") == "![](Pics/pic.png)\n"
    assert said == ["1 open note with unsaved changes was not updated."]


def test_trashing_a_folder_asks_first(tmp_path, libraries, sidebar, monkeypatch):
    import send2trash
    (tmp_path / "root" / "Old").mkdir(parents=True)
    libraries.add_root(str(tmp_path / "root"))
    trashed = []
    monkeypatch.setattr(send2trash, "send2trash", trashed.append)
    monkeypatch.setattr(library_actions, "confirm_trash", lambda parent, paths: False)
    assert not sidebar.trash_folder(str(tmp_path / "root" / "Old"))
    assert trashed == []
    monkeypatch.setattr(library_actions, "confirm_trash", lambda parent, paths: True)
    assert sidebar.trash_folder(str(tmp_path / "root" / "Old"))
    assert trashed == [str(tmp_path / "root" / "Old")]


def test_files_from_the_list_dropped_on_a_folder_move_there(tmp_path, libraries, sidebar):
    pic = write(tmp_path / "root" / "pic.png")
    note = write(tmp_path / "root" / "Note.md", "![](pic.png)\n")
    (tmp_path / "root" / "Pics").mkdir()
    libraries.add_root(str(tmp_path / "root"))
    sidebar.rebuild()
    pics = str(tmp_path / "root" / "Pics")
    highlighted = drop(sidebar, pics, library_actions.mime_for([str(pic)]))
    assert highlighted == pics
    assert sidebar.drop_target() is None
    assert (tmp_path / "root" / "Pics" / "pic.png").exists()
    assert not pic.exists()
    assert note.read_text(encoding="utf-8") == "![](Pics/pic.png)\n"


def test_files_from_the_finder_dropped_on_a_folder_are_copied_in(tmp_path, libraries, sidebar):
    outside = write(tmp_path / "outside" / "photo.jpg", "x")
    (tmp_path / "root" / "Pics").mkdir(parents=True)
    libraries.add_root(str(tmp_path / "root"))
    sidebar.rebuild()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(outside))])
    drop(sidebar, str(tmp_path / "root" / "Pics"), mime)
    assert (tmp_path / "root" / "Pics" / "photo.jpg").exists()
    assert outside.exists()


def test_new_note_from_the_menu_is_made_and_announced(tmp_path, libraries, sidebar):
    (tmp_path / "root").mkdir()
    root = str(tmp_path / "root")
    libraries.add_root(root)
    made = []
    sidebar.note_created.connect(made.append)
    menu = sidebar.menu_for(sidebar.model_.rows()[sidebar.model_.row_of(FOLDER, root)])
    next(a for a in menu.actions() if a.objectName() == "new_note").trigger()
    assert made == [os.path.join(root, "Untitled.md")]


def test_widths_and_names(tmp_path, libraries, sidebar):
    assert (LibrarySidebar.MIN_WIDTH, LibrarySidebar.DEFAULT_WIDTH,
            LibrarySidebar.MAX_WIDTH) == (180, 232, 360)
    assert sidebar.minimumWidth() == 180 and sidebar.maximumWidth() == 360
    assert unnamed_controls(sidebar) == []
    (tmp_path / "root").mkdir()
    libraries.add_root(str(tmp_path / "root"))
    sidebar.rebuild()
    assert unnamed_controls(sidebar) == []
    sidebar.update_theme(True)
    assert not sidebar.grab().isNull()


def test_a_folder_dropped_into_itself_is_refused_before_anything_happens(tmp_path, libraries,
                                                                         sidebar, monkeypatch):
    told = []
    monkeypatch.setattr(library_actions.alerts, "inform", lambda *a, **k: told.append(k))
    write(tmp_path / "root" / "A" / "note.md", "hi")
    (tmp_path / "root" / "A" / "sub").mkdir()
    libraries.add_root(str(tmp_path / "root"))
    sidebar.rebuild()
    folder = str(tmp_path / "root" / "A")
    mime = library_actions.mime_for([folder])
    assert drop(sidebar, folder, mime) is None
    assert drop(sidebar, folder, mime, Qt.KeyboardModifier.AltModifier) is None
    assert sorted(os.listdir(folder)) == ["note.md", "sub"]
    assert told == []


def test_the_sidebar_says_nothing_while_it_is_built(tmp_path, libraries):
    """Nobody listens to a widget being built: its first choice is made
    quietly, and the host asks current() once it has wired the sidebar."""
    (tmp_path / "root").mkdir()
    libraries.add_root(str(tmp_path / "root"))
    said = []

    class Listening(LibrarySidebar):
        def rebuild(self):
            # The constructor's own rebuild, heard as a host would hear it
            # had it been connected already.
            if not said and not hasattr(self, "_heard"):
                self._heard = True
                self.selection_changed.connect(lambda kind, path: said.append((kind, path)))
            super().rebuild()

    widget = Listening(libraries, is_dark=False)
    try:
        assert said == []
        assert widget.current() == ("folder", str(tmp_path / "root"))
    finally:
        widget.deleteLater()


def test_clicking_the_chosen_row_says_it_again(tmp_path, libraries, sidebar):
    (tmp_path / "root").mkdir()
    libraries.add_root(str(tmp_path / "root"))
    sidebar.rebuild()
    assert sidebar.current() == ("folder", str(tmp_path / "root"))
    said = []
    sidebar.selection_changed.connect(lambda kind, path: said.append((kind, path)))
    index = sidebar.model_.index(sidebar.model_.row_of(FOLDER, str(tmp_path / "root")))
    QTest.mouseClick(sidebar.view.viewport(), Qt.MouseButton.LeftButton,
                     pos=sidebar.view.visualRect(index).center())
    assert said == [("folder", str(tmp_path / "root"))]


@pytest.mark.parametrize("dark", [True, False])
def test_the_glyphs_stand_out_from_the_pane_in_either_theme(dark):
    import theme
    from PySide6.QtGui import QColor
    from constants import DARK_BG, DARK_MENU_BG, LIGHT_BG, LIGHT_MENU_BG
    ink = theme.accent_icon_color(dark)
    for pane in ((DARK_BG, DARK_MENU_BG) if dark else (LIGHT_BG, LIGHT_MENU_BG)):
        assert theme.contrast_ratio(ink, QColor(pane)) >= 3.0, (dark, pane)


def test_a_row_tells_a_screen_reader_its_name_count_and_whether_it_is_open():
    assert accessible_text(Row(LIBRARY, "notes", "Notes", count=3)) == "Notes, 3"
    assert accessible_text(Row(LIBRARY, "notes", "Notes")) == "Notes"
    folder = Row(FOLDER, "/a", "Trips", count=2, expandable=True, expanded=True)
    assert accessible_text(folder) == "Trips, 2, expanded"
    assert accessible_text(Row(FOLDER, "/a", "Trips", expandable=True)) == "Trips, collapsed"
