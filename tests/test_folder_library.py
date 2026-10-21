# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins what the folder sidebar's library does with the folder on disk.

What must hold: listings leave hidden files out, keep folders apart
from files and sort numbers as numbers; the recursive index is built
off the GUI thread, never enters hidden folders or document packages,
and stops at its limit; new notes and folders get Finder-style names;
nothing is ever replaced; renaming or moving a note takes its pictures
along and the links follow; deleting goes to the Trash; and the
library says when a folder changed, its own changes and others'.
"""

import errno
import os
import shutil
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

import folder_library  # noqa: E402
from folder_library import Entry, FolderLibrary, natural_key, safe_name  # noqa: E402
from tests.waiting import wait_until  # noqa: E402


def write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def wait_for(signal, timeout_ms=5000):
    """Run the event loop until ``signal`` fires; whether it did."""
    loop = QEventLoop()
    fired = []

    def done(*args):
        fired.append(args)
        loop.quit()
    signal.connect(done)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    signal.disconnect(done)
    return bool(fired)


@pytest.fixture
def library(tmp_path):
    lib = FolderLibrary()
    yield lib
    lib.set_root(None)
    lib.deleteLater()
    QCoreApplication.processEvents()


def indexed(lib, root):
    """Point ``lib`` at ``root`` and wait for its index."""
    lib.set_root(str(root))
    assert wait_until(lambda: not lib.is_indexing())
    return lib


def names(entries):
    return [e.name for e in entries]


# -- listings -------------------------------------------------------------------


def test_names_sort_as_people_read_them():
    ordered = sorted(["Note 10.md", "note 2.md", "Note 1.md", "Älter.md", "Zebra.md"],
                     key=natural_key)
    assert ordered[:3] == ["Note 1.md", "note 2.md", "Note 10.md"]


def test_entries_are_folders_then_files_hidden_ones_left_out(tmp_path, library):
    write(tmp_path / "Note 10.md")
    write(tmp_path / "Note 2.md")
    write(tmp_path / "Beach.png")
    write(tmp_path / ".DS_Store")
    write(tmp_path / "~$Budget.xlsx")
    write(tmp_path / ".~lock.Budget.xlsx#")
    write(tmp_path / "Thumbs.db")
    write(tmp_path / "Budget.xlsx")
    (tmp_path / "Folder 10").mkdir()
    (tmp_path / "Folder 9").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "Home.numbers" / "Data").mkdir(parents=True)
    write(tmp_path / "Home.numbers" / "Index.zip", "x" * 100)
    entries = library.entries(str(tmp_path))
    assert names(entries) == ["Folder 9", "Folder 10", "Beach.png", "Budget.xlsx",
                              "Home.numbers", "Note 2.md", "Note 10.md"]
    by_name = {e.name: e for e in entries}
    assert by_name["Folder 9"].is_dir and by_name["Folder 9"].kind == "folder"
    assert not by_name["Home.numbers"].is_dir
    assert by_name["Home.numbers"].kind == "sheet"
    # A listing never walks into a package; the index measures it.
    assert by_name["Home.numbers"].size == folder_library.UNMEASURED
    assert folder_library._package_size(str(tmp_path / "Home.numbers")) == 100
    assert by_name["Note 2.md"].kind == "note"
    assert isinstance(by_name["Beach.png"], Entry)
    assert library.subfolders(str(tmp_path)) == [str(tmp_path / "Folder 9"),
                                                 str(tmp_path / "Folder 10")]


def test_a_listing_never_walks_into_a_package(tmp_path, library, monkeypatch):
    package = tmp_path / "Big.pages"
    for number in range(2000):
        (package / f"d{number // 100}" / f"e{number}").mkdir(parents=True)
    write(tmp_path / "Note.md")
    started = time.monotonic()
    entries = library.entries(str(tmp_path))
    assert time.monotonic() - started < 0.2
    assert names(entries) == ["Big.pages", "Note.md"]
    walked = []
    real = os.scandir
    monkeypatch.setattr(os, "scandir", lambda path=".": walked.append(path) or real(path))
    library.entries(str(tmp_path))
    assert walked == [str(tmp_path)]


def test_a_package_size_counts_folders_toward_its_limit(tmp_path, monkeypatch):
    package = tmp_path / "Hollow.key"
    for number in range(100):
        (package / f"d{number:03d}").mkdir(parents=True)
    write(package / "d099" / "last.bin", "x" * 10)
    monkeypatch.setattr(folder_library, "_PACKAGE_COUNT_LIMIT", 10)
    walked = []
    real = os.scandir
    monkeypatch.setattr(os, "scandir", lambda path=".": walked.append(path) or real(path))
    folder_library._package_size(str(package))
    assert len(walked) <= 11


def test_a_folder_that_cannot_be_read_lists_nothing(tmp_path, library):
    assert library.entries(str(tmp_path / "missing")) == []


# -- the index ------------------------------------------------------------------


def test_the_index_is_built_off_the_gui_thread_and_announced(tmp_path, library):
    write(tmp_path / "a.md")
    write(tmp_path / "Sub" / "b.png")
    write(tmp_path / "Sub" / "Deeper" / "c.pdf")
    write(tmp_path / ".hidden" / "secret.md")
    write(tmp_path / "Talk.key" / "Data" / "inner.png")
    write(tmp_path / "Sub" / "~$draft.docx")
    library.set_root(str(tmp_path))
    assert library.is_indexing()
    assert library.all_files() == []                # nothing waited for on this thread
    assert wait_for(library.index_ready)
    assert names(library.all_files()) == ["a.md", "b.png", "c.pdf", "Talk.key"]
    assert names(library.all_files({"image", "pdf"})) == ["b.png", "c.pdf"]
    assert library.counts() == {"note": 1, "image": 1, "pdf": 1, "slides": 1}
    assert not library.index_truncated()
    # The worker measures a package in the index.
    talk = next(e for e in library.all_files() if e.name == "Talk.key")
    assert talk.size == os.path.getsize(tmp_path / "Talk.key" / "Data" / "inner.png")


def test_recent_is_newest_first(tmp_path, library):
    for i, name in enumerate(["old.md", "middle.md", "new.md"]):
        path = write(tmp_path / name)
        os.utime(path, (1_000_000 + i, 1_000_000 + i))
    indexed(library, tmp_path)
    assert names(library.recent()) == ["new.md", "middle.md", "old.md"]
    assert names(library.recent(limit=1)) == ["new.md"]


def test_the_index_stops_at_twenty_thousand_entries(tmp_path, library):
    assert folder_library.INDEX_LIMIT == 20_000
    for i in range(20_010):
        (tmp_path / f"f{i}.txt").touch()
    indexed(library, tmp_path)
    assert len(library.all_files()) == 20_000
    assert library.index_truncated()


def test_the_index_limit_counts_files_and_folders_have_a_limit_of_their_own(tmp_path, library,
                                                                           monkeypatch):
    monkeypatch.setattr(folder_library, "INDEX_LIMIT", 5)
    for i in range(4):
        (tmp_path / f"Folder {i}").mkdir()
        write(tmp_path / f"Folder {i}" / "deep.md")
    indexed(library, tmp_path)
    assert len(library.all_files()) == 4          # folders do not use up the files' limit
    assert not library.index_truncated()
    monkeypatch.setattr(folder_library, "FOLDER_LIMIT", 3)
    library.refresh()
    assert wait_until(lambda: not library.is_indexing())
    assert len(library.all_files()) == 2           # the root and two folders looked into
    assert library.index_truncated()


def test_twenty_thousand_files_among_many_folders_are_all_indexed(tmp_path, library):
    for i in range(400):
        folder = tmp_path / f"F{i}"
        folder.mkdir()
        for j in range(50):
            (folder / f"n{j}.md").touch()
    indexed(library, tmp_path)
    assert len(library.all_files()) == 20_000
    assert not library.index_truncated()


def test_the_counts_and_the_order_are_worked_out_with_the_index(tmp_path, library):
    write(tmp_path / "b10.md")
    write(tmp_path / "b9.png")
    write(tmp_path / "Sub" / "a.md")
    write(tmp_path / "Sub" / "Deep" / "c.md")
    indexed(library, tmp_path)
    assert library.counts() == {"note": 3, "image": 1}
    assert library.folder_counts() == (2, {str(tmp_path / "Sub"): 2})
    assert [e.name for e in library.all_files()] == ["a.md", "b9.png", "b10.md", "c.md"]


def test_no_root_means_an_empty_library(library):
    library.set_root(None)
    assert library.root() is None
    assert library.all_files() == [] and library.counts() == {} and library.recent() == []


# -- new notes and folders ------------------------------------------------------


def test_a_new_note_is_an_empty_untitled_markdown_file(tmp_path, library):
    first = library.new_note(str(tmp_path))
    second = library.new_note(str(tmp_path))
    assert first == str(tmp_path / "Untitled.md")
    assert second == str(tmp_path / "Untitled 2.md")
    assert (tmp_path / "Untitled.md").read_bytes() == b""


def test_a_new_note_is_named_after_its_title_safely(tmp_path, library):
    path = library.new_note(str(tmp_path), "Plan: Q3/Q4 <draft>?")
    assert os.path.basename(path) == "Plan Q3-Q4 draft.md"
    assert library.new_note(str(tmp_path), "Über uns (neu)") == str(tmp_path / "Über uns (neu).md")
    assert os.path.basename(library.new_note(str(tmp_path), "...")) == "Untitled.md"


def test_names_are_safe_on_every_system():
    assert safe_name("CON", "x") == "_CON"
    assert safe_name(".hidden", "x") == "hidden"
    assert safe_name("trailing. ", "x") == "trailing"
    assert safe_name("a\tb\nc", "x") == "a b c"
    assert len(safe_name("ü" * 500, "x").encode("utf-8")) <= 240
    assert safe_name("", "Untitled") == "Untitled"


def test_a_new_folder_gets_a_free_name(tmp_path, library):
    assert library.new_folder(str(tmp_path)) == str(tmp_path / "New Folder")
    assert library.new_folder(str(tmp_path)) == str(tmp_path / "New Folder 2")
    assert library.new_folder(str(tmp_path), "Trips") == str(tmp_path / "Trips")
    assert (tmp_path / "New Folder 2").is_dir()


# -- rename and move ------------------------------------------------------------


def test_renaming_a_note_takes_its_pictures_and_links_along(tmp_path, library):
    write(tmp_path / "Trip_media" / "beach.png")
    write(tmp_path / "Trip.md", "![](Trip_media/beach.png)\n")
    index = write(tmp_path / "Index.md", "[t](Trip.md) ![](Trip_media/beach.png)\n")
    library.set_root(str(tmp_path))
    result = library.rename(str(tmp_path / "Trip.md"), "Rome 2026.md")
    assert result.new_path == str(tmp_path / "Rome 2026.md")
    assert (tmp_path / "Rome 2026_media" / "beach.png").exists()
    assert not (tmp_path / "Trip_media").exists()
    assert (tmp_path / "Rome 2026.md").read_text(encoding="utf-8") == "![](<Rome 2026_media/beach.png>)\n"
    assert index.read_text(encoding="utf-8") == "[t](<Rome 2026.md>) ![](<Rome 2026_media/beach.png>)\n"
    assert sorted(result.changed_notes) == sorted([str(tmp_path / "Rome 2026.md"), str(index)])


def test_renaming_without_rewriting_links_leaves_the_notes(tmp_path, library):
    write(tmp_path / "a.png")
    note = write(tmp_path / "Note.md", "![](a.png)\n")
    result = library.rename(str(tmp_path / "a.png"), "b.png", rewrite_links=False)
    assert result.changed_notes == []
    assert note.read_text(encoding="utf-8") == "![](a.png)\n"


def test_renaming_never_replaces_a_file(tmp_path, library):
    write(tmp_path / "a.md", "a")
    write(tmp_path / "b.md", "b")
    with pytest.raises(FileExistsError):
        library.rename(str(tmp_path / "a.md"), "b.md")
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "a" and (tmp_path / "b.md").read_text(encoding="utf-8") == "b"


def test_renaming_refuses_when_the_new_media_folder_is_taken(tmp_path, library):
    write(tmp_path / "A_media" / "x.png")
    write(tmp_path / "B_media" / "y.png")
    write(tmp_path / "A.md")
    with pytest.raises(FileExistsError):
        library.rename(str(tmp_path / "A.md"), "B.md")
    assert (tmp_path / "A.md").exists()


def test_a_name_with_a_slash_is_refused(tmp_path, library):
    write(tmp_path / "a.md")
    for bad in ("x/y.md", "", ".."):
        with pytest.raises(ValueError):
            library.rename(str(tmp_path / "a.md"), bad)


WINDOWS_CHARACTERS = "A name can't contain any of these characters: \\ / : * ? \" < > |"


@pytest.mark.parametrize("name", ["Why?.md", "a:b.md", "a*.md", "<a>.md", "a|b.md",
                                  "say \"hi\".md", "tab\there.md"])
def test_windows_refuses_the_characters_explorer_names_and_says_which(name):
    assert folder_library.name_problem(name, "win32") == WINDOWS_CHARACTERS
    assert folder_library.name_problem(name, "darwin") is None
    assert folder_library.name_problem(name, "linux") is None


@pytest.mark.parametrize("name", ["CON", "con.md", "Nul .txt", "COM1.tar.gz", "lpt9.md"])
def test_windows_refuses_the_names_it_keeps_for_its_devices(name):
    assert folder_library.name_problem(name, "win32") == (
        f"“{name}” is a name Windows keeps for itself. Choose a different name.")
    assert folder_library.name_problem(name, "darwin") is None


def test_windows_refuses_a_name_that_ends_with_a_space_or_a_period():
    for name in ("Notes.", "Notes "):
        assert folder_library.name_problem(name, "win32") == \
            "A name can't end with a space or a period."
    for name in ("Console.md", "Connect", "COM10.md", ".hidden", "Notes.md"):
        assert folder_library.name_problem(name, "win32") is None


def test_a_rename_to_a_name_the_system_refuses_says_why_and_changes_nothing(tmp_path, library):
    note = write(tmp_path / "tips.md")
    if sys.platform == "win32":
        with pytest.raises(ValueError) as refused:
            library.rename(str(note), "Why?.md")
        assert str(refused.value) == WINDOWS_CHARACTERS
        assert os.listdir(tmp_path) == ["tips.md"]
    else:
        # Elsewhere the name is a name like any other.
        assert library.rename(str(note), "Why?.md").new_path == str(tmp_path / "Why?.md")
        assert os.listdir(tmp_path) == ["Why?.md"]


def test_a_change_of_case_is_a_rename(tmp_path, library):
    write(tmp_path / "note.md")
    result = library.rename(str(tmp_path / "note.md"), "Note.md")
    assert result.new_path == str(tmp_path / "Note.md")
    assert "Note.md" in os.listdir(tmp_path)


def test_moving_a_note_recomputes_its_own_links(tmp_path, library):
    write(tmp_path / "pics" / "a b.png")
    write(tmp_path / "Note.md", "![](<pics/a b.png>) [x](Other.md)\n")
    write(tmp_path / "Other.md")
    (tmp_path / "Archive").mkdir()
    library.set_root(str(tmp_path))
    result = library.move([str(tmp_path / "Note.md")], str(tmp_path / "Archive"))
    moved = tmp_path / "Archive" / "Note.md"
    assert result.moved == {str(tmp_path / "Note.md"): str(moved)}
    assert moved.read_text(encoding="utf-8") == "![](<../pics/a b.png>) [x](../Other.md)\n"
    assert result.changed_notes == [str(moved)]


def test_moving_a_note_takes_its_media_folder_along(tmp_path, library):
    write(tmp_path / "Trip_media" / "beach.png")
    write(tmp_path / "Trip.md", "![](Trip_media/beach.png)\n")
    (tmp_path / "Done").mkdir()
    library.set_root(str(tmp_path))
    result = library.move([str(tmp_path / "Trip.md")], str(tmp_path / "Done"))
    assert set(result.moved.values()) == {str(tmp_path / "Done" / "Trip.md"),
                                          str(tmp_path / "Done" / "Trip_media")}
    assert (tmp_path / "Done" / "Trip.md").read_text(encoding="utf-8") == "![](Trip_media/beach.png)\n"
    assert result.changed_notes == []


def test_moving_a_folder_with_linked_pictures(tmp_path, library):
    write(tmp_path / "Photos (2026)" / "sea.jpg")
    index = write(tmp_path / "Index.md", "![](<Photos (2026)/sea.jpg>)\n")
    (tmp_path / "Archive").mkdir()
    library.set_root(str(tmp_path))
    result = library.move([str(tmp_path / "Photos (2026)")], str(tmp_path / "Archive"))
    assert result.changed_notes == [str(index)]
    assert index.read_text(encoding="utf-8") == "![](<Archive/Photos (2026)/sea.jpg>)\n"


def test_moving_never_replaces_and_moves_nothing_when_one_would(tmp_path, library):
    write(tmp_path / "a.md", "a")
    write(tmp_path / "b.md", "b")
    write(tmp_path / "Into" / "b.md", "other b")
    with pytest.raises(FileExistsError):
        library.move([str(tmp_path / "a.md"), str(tmp_path / "b.md")], str(tmp_path / "Into"))
    assert (tmp_path / "a.md").exists() and (tmp_path / "Into" / "b.md").read_text(encoding="utf-8") == "other b"


def test_a_folder_cannot_move_into_itself(tmp_path, library):
    (tmp_path / "Outer" / "Inner").mkdir(parents=True)
    with pytest.raises(ValueError):
        library.move([str(tmp_path / "Outer")], str(tmp_path / "Outer" / "Inner"))
    with pytest.raises(ValueError):
        library.move([str(tmp_path / "Outer")], str(tmp_path / "Outer"))
    assert (tmp_path / "Outer" / "Inner").is_dir()


def test_moving_into_the_folder_it_is_in_does_nothing(tmp_path, library):
    write(tmp_path / "a.md")
    assert library.move([str(tmp_path / "a.md")], str(tmp_path)).moved == {}


# -- copy, trash, references ----------------------------------------------------


def test_copying_in_never_replaces(tmp_path, library):
    outside = tmp_path / "outside"
    write(outside / "Report.pdf", "new")
    write(outside / "Album" / "a.png", "png")
    inside = tmp_path / "inside"
    write(inside / "Report.pdf", "old")
    made = library.copy_in([str(outside / "Report.pdf"), str(outside / "Album")], str(inside))
    assert made == [str(inside / "Report 2.pdf"), str(inside / "Album")]
    assert (inside / "Report.pdf").read_text(encoding="utf-8") == "old"
    assert (inside / "Report 2.pdf").read_text(encoding="utf-8") == "new"
    assert (inside / "Album" / "a.png").read_text(encoding="utf-8") == "png"
    assert (outside / "Report.pdf").exists()


def test_a_copied_note_brings_its_pictures_and_keeps_its_links(tmp_path, library):
    outside = tmp_path / "outside"
    write(outside / "Trip_media" / "beach.png", "png")
    write(outside / "Trip.md", "![](Trip_media/beach.png)\n")
    inside = tmp_path / "inside"
    write(inside / "Trip.md", "mine")
    write(inside / "Trip_media" / "mine.png")
    made = library.copy_in([str(outside / "Trip.md")], str(inside))
    assert made == [str(inside / "Trip 2.md")]
    assert (inside / "Trip 2_media" / "beach.png").read_text(encoding="utf-8") == "png"
    assert (inside / "Trip 2.md").read_text(encoding="utf-8") == "![](<Trip 2_media/beach.png>)\n"
    assert (inside / "Trip.md").read_text(encoding="utf-8") == "mine"


def test_trash_uses_the_system_trash(tmp_path, library, monkeypatch):
    import send2trash
    trashed = []
    monkeypatch.setattr(send2trash, "send2trash", lambda path: trashed.append(path))
    write(tmp_path / "Trip_media" / "beach.png")
    write(tmp_path / "Trip.md")
    write(tmp_path / "Other.pdf")
    library.trash([str(tmp_path / "Trip.md"), str(tmp_path / "Other.pdf")])
    assert trashed == [str(tmp_path / "Trip.md"), str(tmp_path / "Other.pdf"),
                       str(tmp_path / "Trip_media")]


def test_references_are_the_notes_whose_links_would_change(tmp_path, library):
    write(tmp_path / "Album" / "sea.jpg")
    one = write(tmp_path / "One.md", "![](Album/sea.jpg)\n")
    write(tmp_path / "Album" / "About.md", "![](sea.jpg)\n")
    write(tmp_path / "Two.md", "nothing here")
    library.set_root(str(tmp_path))
    assert library.references_to([str(tmp_path / "Album")]) == [str(one)]
    assert library.references_to([str(tmp_path / "Album" / "sea.jpg")]) == [
        str(tmp_path / "Album" / "About.md"), str(one)]


# -- changes --------------------------------------------------------------------


def test_own_operations_say_the_folder_changed_at_once(tmp_path, library):
    seen = []
    library.changed.connect(seen.append)
    library.new_note(str(tmp_path))
    assert seen == [str(tmp_path)]


def test_a_change_from_outside_is_seen_settled(tmp_path, library):
    indexed(library, tmp_path)
    sub = tmp_path / "Shown"
    sub.mkdir()
    library.watch([str(sub)])
    seen = []
    library.changed.connect(seen.append)
    for i in range(5):
        write(sub / f"burst {i}.md")
    assert wait_until(lambda: str(sub) in seen)
    assert seen.count(str(sub)) == 1                    # one change for a burst
    assert wait_until(lambda: len(library.all_files()) == 5)


def test_refresh_rescans_everything(tmp_path, library):
    indexed(library, tmp_path)
    seen = []
    library.changed.connect(seen.append)
    write(tmp_path / "new.md")
    library.refresh()
    assert seen[0] == ""
    assert wait_for(library.index_ready)
    assert names(library.all_files()) == ["new.md"]


def test_a_newer_index_wins_over_an_older_one(tmp_path, library):
    write(tmp_path / "one" / "a.md")
    write(tmp_path / "two" / "b.md")
    library.set_root(str(tmp_path / "one"))
    library.set_root(str(tmp_path / "two"))
    assert wait_until(lambda: not library.is_indexing())
    assert names(library.all_files()) == ["b.md"]


# -- into itself, partial moves, other disks, names taken meanwhile --------------


def no_staging_left(folder):
    """Nothing of a copy or move half made is left in ``folder``."""
    return not [n for n in os.listdir(folder) if n.startswith(".myeditor-")]


def folders_under(path):
    return sum(len(dirs) for _folder, dirs, _files in os.walk(path))


def test_a_folder_is_never_copied_into_itself(tmp_path, library):
    write(tmp_path / "A" / "note.md", "hi")
    (tmp_path / "A" / "sub").mkdir()
    for target in (tmp_path / "A" / "sub", tmp_path / "A"):
        with pytest.raises(ValueError):
            library.copy_in([str(tmp_path / "A")], str(target))
    assert folders_under(tmp_path / "A") == 1


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need rights on Windows")
def test_a_folder_never_goes_into_itself_by_way_of_a_link(tmp_path, library):
    write(tmp_path / "A" / "note.md", "hi")
    (tmp_path / "A" / "sub").mkdir()
    os.symlink(tmp_path / "A", tmp_path / "shortcut")
    with pytest.raises(ValueError):
        library.move([str(tmp_path / "A")], str(tmp_path / "shortcut" / "sub"))
    with pytest.raises(ValueError):
        library.copy_in([str(tmp_path / "A")], str(tmp_path / "shortcut" / "sub"))
    assert folders_under(tmp_path / "A") == 1
    assert (tmp_path / "A" / "note.md").exists()


def test_a_note_never_takes_its_pictures_into_their_own_folder(tmp_path, library):
    write(tmp_path / "Trip.md", "![](Trip_media/a.png)\n")
    write(tmp_path / "Trip_media" / "a.png")
    with pytest.raises(ValueError):
        library.copy_in([str(tmp_path / "Trip.md")], str(tmp_path / "Trip_media"))
    with pytest.raises(ValueError):
        library.move([str(tmp_path / "Trip.md")], str(tmp_path / "Trip_media"))
    assert folders_under(tmp_path / "Trip_media") == 0
    assert (tmp_path / "Trip.md").exists()


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="folders that refuse changes")
def test_an_item_the_disk_refuses_stays_and_what_moved_has_its_links_followed(tmp_path,
                                                                               library):
    index = write(tmp_path / "index.md", "[a](a.md) and [b](locked/b.md)\n")
    write(tmp_path / "a.md", "A")
    write(tmp_path / "locked" / "b.md", "B")
    (tmp_path / "dest").mkdir()
    os.chmod(tmp_path / "locked", 0o555)
    try:
        with pytest.raises(folder_library.MoveIncomplete) as raised:
            library.move([str(tmp_path / "a.md"), str(tmp_path / "locked" / "b.md")],
                         str(tmp_path / "dest"))
    finally:
        os.chmod(tmp_path / "locked", 0o755)
    assert raised.value.result.moved == {str(tmp_path / "a.md"): str(tmp_path / "dest" / "a.md")}
    assert [path for path, _error in raised.value.failed] == [str(tmp_path / "locked" / "b.md")]
    assert raised.value.result.changed_notes == [str(index)]
    assert index.read_text(encoding="utf-8") == "[a](dest/a.md) and [b](locked/b.md)\n"
    # Never a copy of what stayed.
    assert (tmp_path / "locked" / "b.md").exists()
    assert not (tmp_path / "dest" / "b.md").exists()


def other_disk(monkeypatch):
    """Make every rename say the target is on another disk, as a move
    between two volumes does."""
    real = folder_library.rename_exclusive

    def across(old, new):
        if ".myeditor-" not in old:
            raise OSError(errno.EXDEV, os.strerror(errno.EXDEV), old)
        return real(old, new)

    monkeypatch.setattr(folder_library, "rename_exclusive", across)


def test_a_move_to_another_disk_copies_checks_and_then_removes(tmp_path, library, monkeypatch):
    other_disk(monkeypatch)
    write(tmp_path / "Album" / "a.png", "png")
    write(tmp_path / "Album" / "Sub" / "b.png", "png2")
    (tmp_path / "dest").mkdir()
    result = library.move([str(tmp_path / "Album")], str(tmp_path / "dest"))
    assert result.moved == {str(tmp_path / "Album"): str(tmp_path / "dest" / "Album")}
    assert (tmp_path / "dest" / "Album" / "Sub" / "b.png").read_text(encoding="utf-8") == "png2"
    assert not (tmp_path / "Album").exists()
    assert no_staging_left(tmp_path / "dest")


def test_an_incomplete_copy_to_another_disk_leaves_the_original_alone(tmp_path, library,
                                                                      monkeypatch):
    other_disk(monkeypatch)
    write(tmp_path / "Album" / "a.png", "png")
    write(tmp_path / "Album" / "b.png", "png2")
    (tmp_path / "dest").mkdir()
    real_copy = folder_library._copy

    def copy_that_loses_a_file(source, target):
        real_copy(source, target)
        os.remove(os.path.join(target, "b.png"))

    monkeypatch.setattr(folder_library, "_copy", copy_that_loses_a_file)
    with pytest.raises(folder_library.MoveIncomplete) as raised:
        library.move([str(tmp_path / "Album")], str(tmp_path / "dest"))
    assert raised.value.result.moved == {}
    assert sorted(os.listdir(tmp_path / "Album")) == ["a.png", "b.png"]
    assert os.listdir(tmp_path / "dest") == []


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="folders that refuse changes")
def test_a_move_to_another_disk_copies_nothing_when_the_original_cannot_go(tmp_path, library,
                                                                           monkeypatch):
    other_disk(monkeypatch)
    write(tmp_path / "Album" / "Locked" / "a.png", "png")
    (tmp_path / "dest").mkdir()
    os.chmod(tmp_path / "Album" / "Locked", 0o555)
    try:
        with pytest.raises(folder_library.MoveIncomplete):
            library.move([str(tmp_path / "Album")], str(tmp_path / "dest"))
    finally:
        os.chmod(tmp_path / "Album" / "Locked", 0o755)
    assert (tmp_path / "Album" / "Locked" / "a.png").exists()
    assert os.listdir(tmp_path / "dest") == []


def test_a_name_taken_in_the_moment_before_a_rename_is_never_replaced(tmp_path, library,
                                                                      monkeypatch):
    note = write(tmp_path / "Draft.md", "draft")
    monkeypatch.setattr(folder_library, "_refuse_taken", lambda path, new_path: None)
    taken = write(tmp_path / "Final.md", "another app wrote this")
    with pytest.raises(FileExistsError):
        library.rename(str(note), "Final.md")
    assert taken.read_text(encoding="utf-8") == "another app wrote this"
    assert note.read_text(encoding="utf-8") == "draft"


def test_a_name_taken_in_the_moment_before_a_copy_gets_the_next_free_one(tmp_path, library,
                                                                         monkeypatch):
    source = write(tmp_path / "outside" / "Report.pdf", "mine")
    inside = tmp_path / "inside"
    inside.mkdir()
    real_free_path = folder_library.free_path
    first = []

    def free_path_then_taken(path, taken=()):
        found = real_free_path(path, taken)
        if not first:
            first.append(found)
            write(tmp_path / "inside" / "Report.pdf", "another app wrote this")
        return found

    monkeypatch.setattr(folder_library, "free_path", free_path_then_taken)
    made = library.copy_in([str(source)], str(inside))
    assert made == [str(inside / "Report 2.pdf")]
    assert (inside / "Report.pdf").read_text(encoding="utf-8") == "another app wrote this"
    assert (inside / "Report 2.pdf").read_text(encoding="utf-8") == "mine"
    assert no_staging_left(inside)


def test_a_change_of_case_only_is_still_a_rename(tmp_path):
    note = write(tmp_path / "plan.md", "x")
    folder_library.rename_exclusive(str(note), str(tmp_path / "Plan.md"))
    assert os.listdir(tmp_path) == ["Plan.md"]


def case_insensitive(folder):
    probe = folder / "CaseProbe"
    probe.mkdir()
    try:
        return (folder / "caseprobe").exists()
    finally:
        probe.rmdir()


def test_a_note_and_its_pictures_named_in_another_case_go_to_the_trash_once(tmp_path, library,
                                                                           monkeypatch):
    if not case_insensitive(tmp_path):
        pytest.skip("a disk that tells cases apart has two folders here")
    import send2trash
    trashed = []

    def trash(path):
        if not os.path.lexists(path):
            raise OSError(errno.ENOENT, "gone", path)
        trashed.append(path)
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)

    monkeypatch.setattr(send2trash, "send2trash", trash)
    write(tmp_path / "Trip.md", "![](trip_media/a.png)")
    write(tmp_path / "trip_media" / "a.png")
    library.trash([str(tmp_path / "Trip.md"), str(tmp_path / "trip_media")])
    assert trashed == [str(tmp_path / "Trip.md"), str(tmp_path / "trip_media")]
    assert os.listdir(tmp_path) == []


def test_a_note_moves_with_its_pictures_folder_named_in_another_case_as_it_is(tmp_path,
                                                                             library):
    if not case_insensitive(tmp_path):
        pytest.skip("a disk that tells cases apart has no pictures folder for this note")
    note = write(tmp_path / "Trip.md", "![](trip_media/a.png)\n")
    write(tmp_path / "trip_media" / "a.png")
    (tmp_path / "dest").mkdir()
    result = library.move([str(note)], str(tmp_path / "dest"))
    assert sorted(os.listdir(tmp_path / "dest")) == ["Trip.md", "trip_media"]
    assert result.moved[str(tmp_path / "trip_media")] == str(tmp_path / "dest" / "trip_media")
    assert (tmp_path / "dest" / "Trip.md").read_text(encoding="utf-8") == \
        "![](trip_media/a.png)\n"


def test_a_copied_note_and_its_pictures_get_names_that_belong_together(tmp_path, library,
                                                                      monkeypatch):
    import send2trash
    source = tmp_path / "src"
    lib = tmp_path / "lib"
    write(source / "X.md", "![a](X_media/a.png)\n")
    write(source / "X_media" / "a.png", "copy")
    write(lib / "X.md", "![a](X_media/a.png)\n")
    write(lib / "X_media" / "a.png", "mine")
    precious = write(lib / "X 2_media" / "precious.txt", "a folder of someone else")
    made = library.copy_in([str(source / "X.md")], str(lib))
    assert made == [str(lib / "X 3.md")]
    assert (lib / "X 3_media" / "a.png").read_text(encoding="utf-8") == "copy"
    assert (lib / "X 3.md").read_text(encoding="utf-8") == "![a](<X 3_media/a.png>)\n"
    trashed = []
    monkeypatch.setattr(send2trash, "send2trash", trashed.append)
    library.trash(made)
    assert trashed == [str(lib / "X 3.md"), str(lib / "X 3_media")]
    assert precious.exists()


def test_a_folder_read_again_keeps_what_did_not_change_and_sees_what_did(tmp_path, library,
                                                                         monkeypatch):
    for i in range(5):
        write(tmp_path / f"n{i}.md", "x")
    first = library.entries(str(tmp_path))
    compared = []
    real_key = folder_library.natural_key
    monkeypatch.setattr(folder_library, "natural_key",
                        lambda name: compared.append(name) or real_key(name))
    again = library.entries(str(tmp_path))
    assert again == first and all(a is b for a, b in zip(again, first))
    assert compared == []                       # nothing compared again
    # A save replaces the file (a new file number); a name changes.
    write(tmp_path / "n1.new", "longer text")
    os.replace(tmp_path / "n1.new", tmp_path / "n1.md")
    os.rename(tmp_path / "n2.md", tmp_path / "a.md")
    names = [(e.name, e.size) for e in library.entries(str(tmp_path))]
    assert names == [("a.md", 1), ("n0.md", 1), ("n1.md", 11), ("n3.md", 1), ("n4.md", 1)]
    # Written into in place, by another app: seen when the listing is old.
    (tmp_path / "n3.md").write_text("in place", encoding="utf-8")
    monkeypatch.setattr(folder_library, "FULL_LOOK_SECONDS", 0.0)
    assert ("n3.md", 8) in [(e.name, e.size) for e in library.entries(str(tmp_path))]
