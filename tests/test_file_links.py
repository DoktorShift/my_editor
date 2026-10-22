# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file from a note's own folders goes into the note as a relative link
to where it lies (file_links.py): a picture as a picture, anything else
as a link called by its name, the address in <...> when it holds a space,
with forward slashes on every system."""

from __future__ import annotations

import os

import file_links


def _file(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return str(path)


def test_a_picture_in_a_nested_folder_is_linked_relative_to_the_note(tmp_path):
    note = _file(tmp_path / "Notes" / "Trip.md", b"# Trip\n")
    picture = _file(tmp_path / "Notes" / "Pictures" / "2026" / "beach.png")
    assert file_links.links_in_place(note, picture, [str(tmp_path / "Notes")])
    assert file_links.relative_destination(note, picture) == "Pictures/2026/beach.png"
    assert file_links.file_markdown(note, picture) == "![beach](Pictures/2026/beach.png)"


def test_names_with_spaces_are_written_in_angle_brackets(tmp_path):
    note = _file(tmp_path / "My Notes" / "Day one.md")
    picture = _file(tmp_path / "My Notes" / "Holiday pictures" / "On the beach.jpg")
    talk = _file(tmp_path / "My Notes" / "Sound" / "Talk (draft).mp3")
    assert (file_links.file_markdown(note, picture)
            == "![On the beach](<Holiday pictures/On the beach.jpg>)")
    assert file_links.file_markdown(note, talk) == "[Talk (draft).mp3](<Sound/Talk (draft).mp3>)"


def test_a_link_names_the_file_and_escapes_its_brackets(tmp_path):
    note = _file(tmp_path / "a.md")
    sheet = _file(tmp_path / "Budget [final].xlsx")
    assert file_links.file_markdown(note, sheet) == r"[Budget \[final\].xlsx](<Budget [final].xlsx>)"


def test_a_file_outside_the_notes_folder_is_not_linked_in_place(tmp_path):
    note = _file(tmp_path / "Notes" / "a.md")
    beside = _file(tmp_path / "Pictures" / "b.png")
    assert not file_links.links_in_place(note, beside)


def test_within_a_library_location_files_beside_the_notes_folder_are_linked(tmp_path):
    note = _file(tmp_path / "Library" / "Notes" / "a.md")
    beside = _file(tmp_path / "Library" / "Pictures" / "b c.png")
    roots = [str(tmp_path / "Library")]
    assert file_links.link_root(note, roots) == os.path.realpath(tmp_path / "Library")
    assert file_links.links_in_place(note, beside, roots)
    assert file_links.file_markdown(note, beside) == "![b c](<../Pictures/b c.png>)"


def test_an_untitled_note_or_a_missing_file_is_never_linked_in_place(tmp_path):
    picture = _file(tmp_path / "p.png")
    assert not file_links.links_in_place(None, picture)
    assert not file_links.links_in_place(str(tmp_path / "unsaved.md"), picture)
    note = _file(tmp_path / "n.md")
    assert not file_links.links_in_place(note, str(tmp_path / "gone.png"))


def test_without_a_library_location_nothing_is_linked_in_place(tmp_path):
    """A note saved in the home folder takes a picture from Downloads into
    its media folder, as before: only sidebar folders link in place."""
    note = _file(tmp_path / "home" / "a.md")
    picture = _file(tmp_path / "home" / "Downloads" / "p.png")
    assert not file_links.links_in_place(note, picture)
    assert not file_links.links_in_place(note, picture, [str(tmp_path / "elsewhere")])
    assert file_links.links_in_place(note, picture, [str(tmp_path / "home")])


def test_a_file_in_another_location_is_not_linked_in_place(tmp_path):
    note = _file(tmp_path / "One" / "a.md")
    picture = _file(tmp_path / "Two" / "p.png")
    assert not file_links.links_in_place(note, picture,
                                         [str(tmp_path / "One"), str(tmp_path / "Two")])
