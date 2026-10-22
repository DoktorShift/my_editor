# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the sidebar's set of folders: what can be added and removed,
which folder a path belongs to, the Library across all of them, and
that links follow a file moved from one folder to another.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from library_set import LibrarySet  # noqa: E402
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


def indexed(libraries):
    assert wait_until(lambda: not libraries.is_indexing())
    return libraries


def test_a_folder_is_added_once_and_never_inside_another(tmp_path, libraries):
    (tmp_path / "a" / "inner").mkdir(parents=True)
    (tmp_path / "b").mkdir()
    said = []
    libraries.roots_changed.connect(lambda: said.append(True))
    assert libraries.add_root(str(tmp_path / "a"))
    assert not libraries.add_root(str(tmp_path / "a"))
    assert not libraries.add_root(str(tmp_path / "a" / "inner"))
    assert not libraries.add_root(str(tmp_path / "missing"))
    assert libraries.add_root(str(tmp_path / "b"))
    assert libraries.roots() == [str(tmp_path / "a"), str(tmp_path / "b")]
    assert len(said) == 2


def test_removing_a_folder_from_the_sidebar_leaves_the_disk_alone(tmp_path, libraries):
    note = write(tmp_path / "a" / "Note.md", "text")
    libraries.add_root(str(tmp_path / "a"))
    assert libraries.remove_root(str(tmp_path / "a"))
    assert libraries.roots() == []
    assert note.read_text(encoding="utf-8") == "text"
    assert not libraries.remove_root(str(tmp_path / "a"))


def test_a_path_belongs_to_the_folder_that_holds_it(tmp_path, libraries):
    (tmp_path / "a" / "sub").mkdir(parents=True)
    (tmp_path / "b").mkdir()
    libraries.add_root(str(tmp_path / "a"))
    libraries.add_root(str(tmp_path / "b"))
    assert libraries.library_for(str(tmp_path / "a" / "sub" / "x.md")).root() == str(tmp_path / "a")
    assert libraries.root_of(str(tmp_path / "b")) == str(tmp_path / "b")
    assert libraries.library_for(str(tmp_path / "elsewhere")) is None


def test_the_library_gathers_the_files_of_every_folder(tmp_path, libraries):
    write(tmp_path / "a" / "One.md")
    write(tmp_path / "a" / "pic.png")
    write(tmp_path / "b" / "Two.md")
    old = write(tmp_path / "b" / "deep" / "old.pdf")
    os.utime(old, (1_000_000, 1_000_000))
    libraries.add_root(str(tmp_path / "a"))
    libraries.add_root(str(tmp_path / "b"))
    indexed(libraries)
    assert [e.name for e in libraries.all_files({"note"})] == ["One.md", "Two.md"]
    assert libraries.counts() == {"note": 2, "image": 1, "pdf": 1}
    assert libraries.recent(10)[-1].name == "old.pdf"
    assert len(libraries.recent(2)) == 2


def test_moving_a_note_to_another_folder_takes_its_pictures_and_links_follow_in_both(
        tmp_path, libraries):
    write(tmp_path / "a" / "Trip_media" / "beach.png")
    write(tmp_path / "a" / "Trip.md", "![](Trip_media/beach.png)\n")
    index_a = write(tmp_path / "a" / "Index.md", "[trip](Trip.md)\n")
    index_b = write(tmp_path / "b" / "Index.md", "[trip](../a/Trip.md)\n")
    (tmp_path / "b" / "Travel").mkdir()
    libraries.add_root(str(tmp_path / "a"))
    libraries.add_root(str(tmp_path / "b"))
    result = libraries.move([str(tmp_path / "a" / "Trip.md")], str(tmp_path / "b" / "Travel"))
    moved = tmp_path / "b" / "Travel" / "Trip.md"
    assert result.moved[str(tmp_path / "a" / "Trip.md")] == str(moved)
    assert (tmp_path / "b" / "Travel" / "Trip_media" / "beach.png").exists()
    assert moved.read_text(encoding="utf-8") == "![](Trip_media/beach.png)\n"
    assert index_a.read_text(encoding="utf-8") == "[trip](../b/Travel/Trip.md)\n"
    assert index_b.read_text(encoding="utf-8") == "[trip](Travel/Trip.md)\n"
    assert set(result.changed_notes) == {str(index_a), str(index_b)}


def test_renaming_a_picture_updates_notes_in_the_other_folders(tmp_path, libraries):
    pic = write(tmp_path / "a" / "pic.png")
    other = write(tmp_path / "b" / "Note.md", "![](../a/pic.png)\n")
    libraries.add_root(str(tmp_path / "a"))
    libraries.add_root(str(tmp_path / "b"))
    result = libraries.rename(str(pic), "beach.png")
    assert result.new_path == str(tmp_path / "a" / "beach.png")
    assert other.read_text(encoding="utf-8") == "![](../a/beach.png)\n"
    assert str(other) in result.changed_notes


def test_unsaved_notes_are_left_to_the_editor(tmp_path, libraries):
    pic = write(tmp_path / "a" / "pic.png")
    note = write(tmp_path / "a" / "Note.md", "![](pic.png)\n")
    libraries.add_root(str(tmp_path / "a"))
    result = libraries.rename(str(pic), "beach.png", skip_notes={str(note)})
    assert note.read_text(encoding="utf-8") == "![](pic.png)\n"
    assert result.changed_notes == []


def test_references_are_found_in_every_folder(tmp_path, libraries):
    pic = write(tmp_path / "a" / "pic.png")
    one = write(tmp_path / "a" / "One.md", "![](pic.png)\n")
    two = write(tmp_path / "b" / "Two.md", "![](../a/pic.png)\n")
    write(tmp_path / "b" / "Three.md", "nothing\n")
    libraries.add_root(str(tmp_path / "a"))
    libraries.add_root(str(tmp_path / "b"))
    assert libraries.references_to([str(pic)]) == [str(one), str(two)]


def test_each_folder_watches_its_own_folders_on_screen(tmp_path, libraries):
    (tmp_path / "a" / "x").mkdir(parents=True)
    (tmp_path / "b" / "y").mkdir(parents=True)
    libraries.add_root(str(tmp_path / "a"))
    libraries.add_root(str(tmp_path / "b"))
    libraries.watch([str(tmp_path / "a" / "x"), str(tmp_path / "b" / "y")])
    first, second = libraries.libraries()
    assert str(tmp_path / "a" / "x") in first._watcher.directories()
    assert str(tmp_path / "b" / "y") in second._watcher.directories()
    assert str(tmp_path / "b" / "y") not in first._watcher.directories()


def test_operations_go_to_the_folder_they_act_in(tmp_path, libraries):
    (tmp_path / "a").mkdir()
    outside = write(tmp_path / "outside" / "file.txt", "hi")
    libraries.add_root(str(tmp_path / "a"))
    note = libraries.new_note(str(tmp_path / "a"))
    assert os.path.basename(note) == "Untitled.md"
    copied = libraries.copy_in([str(outside)], str(tmp_path / "a"))
    assert copied == [str(tmp_path / "a" / "file.txt")]
    with pytest.raises(ValueError):
        libraries.new_note(str(tmp_path / "outside"))


# -- folders inside folders ------------------------------------------------------


def test_a_folder_that_holds_sidebar_folders_takes_their_place(tmp_path, libraries):
    write(tmp_path / "Notes" / "Work" / "Plan.md")
    write(tmp_path / "Notes" / "Home" / "List.md")
    (tmp_path / "Other").mkdir()
    libraries.add_root(str(tmp_path / "Notes" / "Work"))
    libraries.add_root(str(tmp_path / "Other"))
    libraries.add_root(str(tmp_path / "Notes" / "Home"))
    assert libraries.add_root(str(tmp_path / "Notes"))
    assert libraries.roots() == [str(tmp_path / "Other"), str(tmp_path / "Notes")]
    indexed(libraries)
    assert sorted(e.name for e in libraries.all_files()) == ["List.md", "Plan.md"]


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need rights on Windows")
def test_a_link_to_a_folder_in_the_sidebar_is_not_added_again(tmp_path, libraries):
    (tmp_path / "Notes" / "Work").mkdir(parents=True)
    os.symlink(tmp_path / "Notes" / "Work", tmp_path / "Work link")
    os.symlink(tmp_path / "Notes", tmp_path / "Notes link")
    assert libraries.add_root(str(tmp_path / "Notes"))
    assert not libraries.add_root(str(tmp_path / "Work link"))
    assert not libraries.add_root(str(tmp_path / "Notes link"))
    assert libraries.roots() == [str(tmp_path / "Notes")]


# -- moves that are only partly possible --------------------------------------------


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="folders that refuse changes")
def test_links_follow_what_moved_to_another_folder_when_another_item_stayed(tmp_path,
                                                                            libraries):
    from folder_library import MoveIncomplete
    index = write(tmp_path / "R1" / "index.md", "[a](a.md) and [b](locked/b.md)\n")
    write(tmp_path / "R1" / "a.md", "A")
    write(tmp_path / "R1" / "locked" / "b.md", "B")
    (tmp_path / "R2" / "dest").mkdir(parents=True)
    libraries.add_root(str(tmp_path / "R1"))
    libraries.add_root(str(tmp_path / "R2"))
    moved = []
    libraries.moved.connect(moved.append)
    os.chmod(tmp_path / "R1" / "locked", 0o555)
    try:
        with pytest.raises(MoveIncomplete) as raised:
            libraries.move([str(tmp_path / "R1" / "a.md"), str(tmp_path / "R1" / "locked" / "b.md")],
                           str(tmp_path / "R2" / "dest"))
    finally:
        os.chmod(tmp_path / "R1" / "locked", 0o755)
    assert index.read_text(encoding="utf-8") == "[a](../R2/dest/a.md) and [b](locked/b.md)\n"
    assert raised.value.result.changed_notes == [str(index)]
    assert moved == [{str(tmp_path / "R1" / "a.md"): str(tmp_path / "R2" / "dest" / "a.md")}]
    assert not (tmp_path / "R2" / "dest" / "b.md").exists()


# -- links followed in the background ---------------------------------------------


@pytest.fixture
def background():
    made = LibrarySet(links_in_background=True)
    yield made
    made.wait_for_links()
    for root in made.roots():
        made.remove_root(root)
    made.deleteLater()
    QCoreApplication.processEvents()


def test_links_are_read_on_a_worker_and_written_in_order_one_operation_after_another(
        tmp_path, background, monkeypatch):
    import threading
    import link_rewrite
    threads = []
    real = link_rewrite.plan_rewrite

    def watched(*args, **kwargs):
        threads.append(threading.current_thread())
        return real(*args, **kwargs)

    monkeypatch.setattr(link_rewrite, "plan_rewrite", watched)
    pic = write(tmp_path / "a" / "pic.png")
    note = write(tmp_path / "a" / "Note.md", "![](pic.png)\n")
    background.add_root(str(tmp_path / "a"))
    reports, rewritten, progress, done = [], [], [], []
    background.rewritten.connect(rewritten.append)
    background.links_progress.connect(lambda finished, total: progress.append((finished, total)))
    assert background.rename(str(pic), "beach.png", on_links=reports.append,
                             on_done=lambda result, error: done.append(result)) is None
    # The next one takes its turn after this one, links and all, so it
    # finds the note as the first left it.
    background.rename(str(tmp_path / "a" / "beach.png"), "sea.png", on_links=reports.append,
                      on_done=lambda result, error: done.append(result))
    assert background.links_pending()
    assert wait_until(lambda: len(done) == 2)
    assert [r.new_path for r in done] == [str(tmp_path / "a" / "beach.png"),
                                          str(tmp_path / "a" / "sea.png")]
    assert note.read_text(encoding="utf-8") == "![](sea.png)\n"
    assert [r.changed for r in reports] == [[str(note)], [str(note)]]
    assert rewritten == [[str(note)], [str(note)]]
    assert threads and all(t is not threading.main_thread() for t in threads)
    assert (1, 1) in progress
    assert not background.links_pending()


def test_a_second_operation_while_notes_are_read_is_queued_not_waited_for(tmp_path, background,
                                                                         monkeypatch):
    import link_rewrite
    real = link_rewrite.plan_rewrite

    def slow(*args, **kwargs):
        time.sleep(0.6)
        return real(*args, **kwargs)

    monkeypatch.setattr(link_rewrite, "plan_rewrite", slow)
    first = write(tmp_path / "a" / "one.png")
    second = write(tmp_path / "a" / "two.png")
    note = write(tmp_path / "a" / "Note.md", "![](one.png) ![](two.png)\n")
    background.add_root(str(tmp_path / "a"))
    done = []
    background.rename(str(first), "uno.png", on_done=lambda result, error: done.append(result))
    assert wait_until(lambda: (tmp_path / "a" / "uno.png").exists())     # its notes are read now
    started = time.monotonic()
    background.rename(str(second), "dos.png", on_done=lambda result, error: done.append(result))
    assert time.monotonic() - started < 0.1
    assert wait_until(lambda: len(done) == 2)
    assert note.read_text(encoding="utf-8") == "![](uno.png) ![](dos.png)\n"


def test_a_note_that_gets_unsaved_changes_meanwhile_is_left_to_the_editor_with_its_moves(
        tmp_path, background):
    pic = write(tmp_path / "a" / "pic.png")
    note = write(tmp_path / "a" / "Note.md", "![](pic.png)\n")
    other = write(tmp_path / "a" / "Other.md", "![](pic.png)\n")
    background.add_root(str(tmp_path / "a"))
    unsaved = set()
    # The person types into the open note after the rename is made, while
    # its links are still being read.
    background.moved.connect(lambda _moves: unsaved.add(str(note)))
    reports = []
    background.rename(str(pic), "beach.png", skip_notes=lambda: set(unsaved),
                      on_links=reports.append)
    assert wait_until(lambda: bool(reports))
    assert note.read_text(encoding="utf-8") == "![](pic.png)\n"
    assert other.read_text(encoding="utf-8") == "![](beach.png)\n"
    [left] = reports[0].left_open
    assert (left.note, left.was) == (str(note), str(note))
    assert left.moves == {str(pic): str(tmp_path / "a" / "beach.png")}
    assert reports[0].changed == [str(other)]


def test_the_index_answers_recents_names_and_counts_without_comparing_names(tmp_path,
                                                                           libraries,
                                                                           monkeypatch):
    import folder_library
    import library_set
    for i in range(30):
        write(tmp_path / "a" / f"n{i}.md")
    write(tmp_path / "a" / "Sub" / "pic.png")
    write(tmp_path / "b" / "x.md")
    libraries.add_root(str(tmp_path / "a"))
    libraries.add_root(str(tmp_path / "b"))
    indexed(libraries)

    def no_names(_name):
        raise AssertionError("names compared on the GUI thread")

    monkeypatch.setattr(folder_library, "natural_key", no_names)
    monkeypatch.setattr(library_set, "natural_key", no_names)
    assert len(libraries.recent(5)) == 5
    names = [e.name for e in libraries.all_files()]
    assert names[:3] == ["n0.md", "n1.md", "n2.md"] and names[-1] == "x.md"
    assert libraries.counts() == {"note": 31, "image": 1}
    assert libraries.folder_counts() == ({str(tmp_path / "a"): 30, str(tmp_path / "b"): 1},
                                         {str(tmp_path / "a" / "Sub"): 1})


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need rights on Windows")
def test_a_file_renamed_by_way_of_a_link_to_another_sidebar_folder_is_followed_there(
        tmp_path, libraries):
    a, b = tmp_path / "A", tmp_path / "B"
    write(b / "pic.png")
    b_note = write(b / "b.md", "![p](pic.png)\n")
    a.mkdir()
    os.symlink(b, a / "toB")
    a_note = write(a / "a.md", "![p](toB/pic.png) [n](n2.md)\n")
    n2 = write(a / "n2.md", "![p](toB/pic.png)\n")
    libraries.add_root(str(a))
    libraries.add_root(str(b))
    libraries.rename(str(a / "toB" / "pic.png"), "pic2.png")
    assert b_note.read_text(encoding="utf-8") == "![p](pic2.png)\n"
    assert a_note.read_text(encoding="utf-8") == "![p](toB/pic2.png) [n](n2.md)\n"
    libraries.move([str(n2)], str(a / "toB"))
    assert (b / "n2.md").read_text(encoding="utf-8") == "![p](pic2.png)\n"
    assert a_note.read_text(encoding="utf-8") == "![p](toB/pic2.png) [n](toB/n2.md)\n"


def test_a_change_of_case_only_takes_the_pictures_and_the_links_along(tmp_path, libraries):
    probe = tmp_path / "CaseProbe"
    probe.mkdir()
    folds = (tmp_path / "caseprobe").exists()
    probe.rmdir()
    if not folds:
        pytest.skip("a disk that tells cases apart")
    lib = tmp_path / "lib"
    write(lib / "Trip.md", "![a](Trip_media/a.png) [o](other.md)\n")
    write(lib / "Trip_media" / "a.png")
    other = write(lib / "other.md", "[t](Trip.md) ![x](Trip_media/a.png) [f](Docs/r.md)\n")
    write(lib / "Docs" / "r.md", "[up](../other.md)\n")
    libraries.add_root(str(lib))
    result = libraries.rename(str(lib / "Trip.md"), "trip.md")
    assert result.moves == {str(lib / "Trip.md"): str(lib / "trip.md"),
                            str(lib / "Trip_media"): str(lib / "trip_media")}
    libraries.rename(str(lib / "Docs"), "docs")
    assert sorted(os.listdir(lib)) == ["docs", "other.md", "trip.md", "trip_media"]
    assert other.read_text(encoding="utf-8") == \
        "[t](trip.md) ![x](trip_media/a.png) [f](docs/r.md)\n"
    assert (lib / "trip.md").read_text(encoding="utf-8") == "![a](trip_media/a.png) [o](other.md)\n"
