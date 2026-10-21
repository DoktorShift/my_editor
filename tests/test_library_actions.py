# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins what the folder sidebar and the file list tell a person when the
disk says no: one calm sentence in words, never the system's wording, a
list of paths or an error number; a move that is only partly possible
names what stayed, once, and still says what the links did."""

import errno
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication  # noqa: E402

import library_actions  # noqa: E402
from folder_library import MoveIncomplete, MoveResult, OriginalLeft  # noqa: E402
from library_set import LibrarySet  # noqa: E402


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
def told(monkeypatch):
    said = []
    monkeypatch.setattr(library_actions.alerts, "inform",
                        lambda parent, title, message="", **kwargs: said.append((title, message)))
    return said


@pytest.mark.parametrize("error, words", [
    (shutil.Error([("/a/x", "/b/x", "[Errno 13] Permission denied")]),
     "Some of the items could not be copied."),
    (OSError(errno.EROFS, "Read-only file system", "/Volumes/CD/x"),
     "The disk can only be read, not changed."),
    (OSError(errno.ENAMETOOLONG, "File name too long", "/x"), "The name is too long."),
    (OSError(errno.EIO, "Input/output error", "/x"), "The item could not be changed."),
    (OSError(errno.EINVAL, "Invalid argument", "/x"), "The item could not be changed."),
    (PermissionError(errno.EACCES, "Permission denied", "/x"),
     "You don't have permission to change this item."),
])
def test_what_the_disk_says_is_told_in_words(error, words):
    message = library_actions.error_message(error)
    assert message == words
    assert "Errno" not in message and "/" not in message


def test_a_partial_move_names_what_stayed_and_why():
    stayed = PermissionError(errno.EACCES, "Permission denied", "/r/locked/b.md")
    error = MoveIncomplete(MoveResult({"/r/a.md": "/d/a.md"}), [("/r/locked/b.md", stayed)])
    assert library_actions.error_message(error) == (
        "“b.md” could not be moved. You don't have permission to change this item.")
    two = MoveIncomplete(MoveResult({}), [("/r/b.md", stayed), ("/r/c.md", stayed)])
    assert library_actions.error_message(two).startswith(
        "2 items could not be moved: “b.md”, “c.md”.")
    left = MoveIncomplete(MoveResult({"/r/Album": "/d/Album"}),
                          [("/r/Album", OriginalLeft(errno.EACCES, "Permission denied", "/r"))])
    assert library_actions.error_message(left) == (
        "“Album” was moved, but some of it could not be removed from where it was.")


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="folders that refuse changes")
def test_a_move_that_is_partly_possible_says_so_once_and_the_links_follow(tmp_path, libraries,
                                                                          told):
    index = write(tmp_path / "root" / "index.md", "[a](a.md) and [b](locked/b.md)\n")
    a = write(tmp_path / "root" / "a.md", "A")
    b = write(tmp_path / "root" / "locked" / "b.md", "B")
    dest = tmp_path / "root" / "dest"
    dest.mkdir()
    libraries.add_root(str(tmp_path / "root"))
    said = []
    os.chmod(tmp_path / "root" / "locked", 0o555)
    try:
        moved = library_actions.move(None, libraries, [str(a), str(b)], str(dest),
                                     say=said.append)
    finally:
        os.chmod(tmp_path / "root" / "locked", 0o755)
    assert moved == [str(dest / "a.md")]
    assert told == [("Couldn't move the items", "“b.md” could not be moved. "
                     "You don't have permission to change this item.")]
    assert said == ["Updated links in 1 note."]
    assert index.read_text(encoding="utf-8") == "[a](dest/a.md) and [b](locked/b.md)\n"


def test_copying_a_folder_into_itself_is_told_in_one_sentence(tmp_path, libraries, told):
    write(tmp_path / "root" / "A" / "note.md")
    (tmp_path / "root" / "A" / "sub").mkdir()
    libraries.add_root(str(tmp_path / "root"))
    assert library_actions.copy_in(None, libraries, [str(tmp_path / "root" / "A")],
                                   str(tmp_path / "root" / "A" / "sub")) == []
    assert told == [("Couldn't copy the items", "“A” cannot be copied into itself.")]
    assert library_actions.goes_into_itself([str(tmp_path / "root" / "A")],
                                            str(tmp_path / "root" / "A" / "sub"))
    assert not library_actions.goes_into_itself([str(tmp_path / "root" / "A" / "note.md")],
                                                str(tmp_path / "root" / "A" / "sub"))


def test_notes_that_could_not_be_updated_are_counted():
    assert library_actions.links_message(["a.md"], [], ["b.md", "c.md"]) == (
        "Updated links in 1 note. The links in 2 notes could not be updated.")


def test_putting_several_items_in_the_trash_says_them_not_it(monkeypatch):
    import alerts
    asked = []
    monkeypatch.setattr(library_actions.sys, "platform", "darwin")
    monkeypatch.setattr(alerts, "confirm_destructive",
                        lambda parent, **words: asked.append(words) or True)
    library_actions.confirm_trash(None, ["/a/one.md"])
    library_actions.confirm_trash(None, ["/a/one.md", "/a/two.png"])
    assert asked[0]["message"] == "You can restore it from the Trash until you empty it."
    assert asked[1]["message"] == ("You can restore the 2 items from the Trash until you "
                                   "empty it.")
    assert asked[1]["title"] == "Move 2 items to the Trash?"


def test_a_failed_trash_names_the_recycle_bin_on_windows(monkeypatch, tmp_path):
    shown = []
    monkeypatch.setattr(library_actions.sys, "platform", "win32")
    monkeypatch.setattr(library_actions, "confirm_trash", lambda parent, paths: True)
    monkeypatch.setattr(library_actions, "show_error",
                        lambda parent, title, error: shown.append(title))

    class Failing:
        def trash(self, paths):
            raise OSError(13, "denied")

    assert library_actions.trash(None, Failing(), [str(tmp_path / "a.md")]) is False
    assert shown == ["Couldn't move to the Recycle Bin"]


def test_the_folder_chooser_is_titled_as_its_command(monkeypatch):
    titles = []
    monkeypatch.setattr(library_actions.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda parent, title, start: titles.append(title) or ""))
    assert library_actions.choose_folder(None, "/") is None
    assert titles == ["Move to"]
