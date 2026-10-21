# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The links of an open note follow files that moved, in the note's text.

When the library renames or moves files, it rewrites the links of every
note on the disk (link_rewrite.py), except the notes open with unsaved
changes: writing those files would put the disk and the editor at odds.
Their links follow here instead, in the editor's document, so nothing is
left pointing at the old place and what the person typed stays. A note
that moved itself has its relative links rebased to its new folder, its
pictures folder's new name included.

Two shapes of document: a note shown as its Markdown text (plain text,
rewritten with link_rewrite's own reading of Markdown), and a note shown
formatted, whose pictures and links carry their destinations in their
formats (each relative one read, followed and written back the way it
was written). This module only works out the changes; the window applies
them as one step of Undo.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Tuple
from urllib.parse import quote, unquote

import link_rewrite

_PERCENT = re.compile(r"%[0-9A-Fa-f]{2}")


def new_place(path: str, moves: Dict[str, str]):
    """Where ``path`` is after ``moves``, or None when it did not move;
    paths compared as this system compares file names."""
    for old, new in sorted(moves.items(), key=lambda item: -len(item[0])):
        if link_rewrite.is_within(path, old):
            depth = len(os.path.normpath(os.path.abspath(old)).split(os.sep))
            rest = os.path.normpath(os.path.abspath(path)).split(os.sep)[depth:]
            return os.path.join(new, *rest) if rest else new
    return None


def text_changes(text: str, old_note: str, new_note: str,
                 moves: Dict[str, str]) -> List[Tuple[int, int, str]]:
    """What changes in ``text`` (a note's Markdown, open as text) for its
    relative links to point where their files are after ``moves``, the
    note having been at ``old_note`` and being at ``new_note`` now:
    (start, end, replacement), in code points, in order."""
    return link_rewrite.link_changes(text, os.path.dirname(os.path.abspath(old_note)),
                                     os.path.dirname(os.path.abspath(new_note)),
                                     link_rewrite.Moves(moves), False)


def destination_after(destination: str, old_note: str, new_note: str,
                      moves: Dict[str, str]):
    """The new destination of a picture's or link's ``destination`` in a
    formatted note, or None when it stays as it is."""
    if not destination or not link_rewrite.is_local(destination):
        return None
    path, hash_, fragment = destination.partition("#")
    old_folder = os.path.dirname(os.path.abspath(old_note))
    new_folder = os.path.dirname(os.path.abspath(new_note))
    encoded = _PERCENT.search(path) is not None
    readings = ([unquote(path)] if encoded else []) + [path]
    moved_note = not link_rewrite.is_within(old_folder, new_folder) or \
        not link_rewrite.is_within(new_folder, old_folder)
    chosen = None
    for reading in readings:
        target = os.path.normpath(os.path.join(old_folder, reading))
        moved_to = new_place(target, moves)
        if moved_to is not None:
            chosen = moved_to
            break
    if chosen is None:
        if not moved_note:
            return None
        reading = next((r for r in readings
                        if os.path.lexists(os.path.join(old_folder, r))), None)
        if reading is None:
            return None
        chosen = os.path.normpath(os.path.join(old_folder, reading))
    try:
        relative = os.path.relpath(chosen, new_folder).replace(os.sep, "/")
    except ValueError:                        # another drive on Windows
        return None
    if encoded:
        relative = quote(relative, safe="/!$&'*+,;=:@~-._()")
    new = relative + (hash_ + fragment if hash_ else "")
    return None if new == destination else new
