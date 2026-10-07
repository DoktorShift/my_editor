# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One spelling for each of the person's files, however it arrived.

A file reaches MyEditor in many ways: the command line, a second launch
handing it to the running window, Finder, a drop, the Open and Save
dialogs, the recent files list, the last session, a crash backup. They
do not all spell it alike. Qt's dialogs and drops write ``C:/Users/...``
on Windows where the system writes ``C:\\Users\\...``; a command line
can be relative to a folder the running window never saw; and Windows
(like a Mac, by default) does not tell ``Notes.md`` from ``notes.md``.

So every path that comes in goes through ``normalize`` once, where it
comes in, and the app keeps that one spelling: absolute, with the
system's own separators. ``same_file`` is the one test for whether two
paths are one file, so a file never opens in two tabs, and
``is_case_change`` tells a rename that only changes the case of a name
from a rename onto another file.

Where it can, the disk decides: two names are one file when the disk
gives both the same identity (its device and file number). Not every
disk gives one: network and cloud drives on Windows (WebDAV, Google
Drive, folders kept offline by Sync Center) report a file number of 0
for every file, so a 0 is never taken for an identity, and the
spellings decide there instead.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple


def normalize(path: str) -> str:
    """``path`` as the app keeps it: absolute, with this system's
    separators and without ``.`` or ``..`` steps.

    A relative path is read from the current folder, so a path from
    another process (a second launch) must be normalized there, before
    it is handed over. Links are kept as they are: the person chose the
    name. An empty path stays empty.
    """
    if not path:
        return path
    return os.path.abspath(path)


def same_file(a: Optional[str], b: Optional[str]) -> bool:
    """Whether ``a`` and ``b`` name the same file.

    When both exist and the disk gives both an identity, the identities
    decide: another case of a name on a Mac, a link and its file, and two
    names of one file are one file; two names a Windows folder that tells
    case apart keeps separately are two. Otherwise (a missing file, a
    disk without identities) the one spellings decide, ignoring case where
    the system does (Windows).
    """
    if not a or not b:
        return False
    first, second = _identity(a, follow_links=True), _identity(b, follow_links=True)
    if first is not None and second is not None:
        return first == second
    return os.path.normcase(normalize(a)) == os.path.normcase(normalize(b))


def is_case_change(old: str, new: str) -> bool:
    """Whether renaming ``old`` to ``new`` only changes the case of its
    name, on a disk that ignores case, so that ``new`` finds ``old``
    itself.

    Judged without following links: a link and the file it points to are
    two entries, and renaming one onto the other would replace a file.
    Where the disk gives no identity, nothing shows that the two names are
    one entry, so the answer is no.
    """
    old, new = normalize(old), normalize(new)
    if old == new or old.casefold() != new.casefold():
        return False
    first = _identity(old, follow_links=False)
    return first is not None and first == _identity(new, follow_links=False)


def _identity(path: str, *, follow_links: bool) -> Optional[Tuple[int, int]]:
    """The disk's identity of ``path``: its device and file number, or
    None when the file is missing or the disk gives no file number."""
    try:
        result = os.stat(path) if follow_links else os.lstat(path)
    except (OSError, ValueError):
        return None
    if not result.st_ino:
        return None
    return result.st_dev, result.st_ino
