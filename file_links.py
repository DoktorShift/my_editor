# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A note's link to a file where the file lies.

A picture, a film or any other file already in the note's folders goes
into the note as a relative link to where it is: nothing is copied, and
the link keeps working wherever the folder goes, in any Markdown app.
Files from elsewhere (the Downloads folder, the clipboard) are taken into
the note's own ``<stem>_media`` folder when it is saved, as before.

Which files are linked in place: those in the library location the note
lies in (a folder the person added to the sidebar). A note outside every
location takes files in as before. Pictures are read for a note from its
own folder, or from the location it lies in, never wider: a document
received from someone else may name "../" paths, and only folders the
person chose are read for it
(shown, exported, knitted), as image_safety.ImageRootPolicy keeps them.
Paths are compared as the disk resolves them (links followed), and the
link is written relative to the note's folder with forward slashes, as
Markdown wants on every system, its address in ``<...>`` when it holds a
space or a parenthesis (markdown_writer's rule).

No widgets here: paths in, Markdown out.
"""

from __future__ import annotations

import os
from typing import Iterable, Optional

import file_kinds
from link_rewrite import is_within
from markdown_writer import image_markdown, link_markdown


def _real(path: str) -> str:
    return os.path.realpath(os.path.abspath(os.fspath(path)))


def link_root(note_path: str, library_roots: Iterable[str] = ()) -> str:
    """The folder whose files a note links where they lie: the library
    location holding the note (the innermost, should one lie in
    another), else the note's own folder."""
    note = _real(note_path)
    holding = [root for root in (_real(r) for r in library_roots if r)
               if is_within(note, root)]
    if holding:
        return max(holding, key=len)
    return os.path.dirname(note)


def links_in_place(note_path: Optional[str], file_path: str,
                   library_roots: Iterable[str] = ()) -> bool:
    """Whether ``file_path`` goes into the note at ``note_path`` as a link
    to where it lies: the note is saved on disk, the file is there, and
    both lie in the same library location. Without one (a note saved in
    the home folder, a picture from Downloads) the file is taken into the
    note's media folder as before: only folders the person added to the
    sidebar are a place files are linked in."""
    if not note_path or not os.path.isfile(note_path):
        return False
    if not (os.path.isfile(file_path) or file_kinds.is_package(file_path)):
        return False
    note = _real(note_path)
    holding = [root for root in (_real(r) for r in library_roots if r) if is_within(note, root)]
    if not holding:
        return False
    return is_within(_real(file_path), max(holding, key=len))


def relative_destination(note_path: str, file_path: str) -> str:
    """The file's place seen from the note's folder, with forward slashes
    ("Pictures/Beach.png", "../Media/Talk.mp3")."""
    start = os.path.dirname(_real(note_path))
    relative = os.path.relpath(_real(file_path), start)
    return relative.replace(os.sep, "/")


def is_picture(file_path: str) -> bool:
    """Whether a file goes into a note as a picture (shown in the text),
    not as a link to it."""
    return file_kinds.kind_of_path(file_path) == file_kinds.IMAGE


def file_markdown(note_path: str, file_path: str) -> str:
    """The Markdown that puts the file into the note where it lies: a
    picture as ``![name](place)``, its name without the extension as the
    words a screen reader says; any other file as ``[name.ext](place)``."""
    destination = relative_destination(note_path, file_path)
    name = os.path.basename(file_path.rstrip("/\\"))
    if is_picture(file_path):
        return image_markdown(os.path.splitext(name)[0] or name, destination)
    return link_markdown(name, destination)
