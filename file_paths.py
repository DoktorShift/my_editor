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
paths are one file, so a file never opens in two tabs.
"""

from __future__ import annotations

import os
from typing import Optional


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

    True when they are spelled alike once normalized (ignoring case where
    the system does, as Windows), or when both exist and are the same
    file on the disk (a different case on a Mac, a link).
    """
    if not a or not b:
        return False
    if os.path.normcase(normalize(a)) == os.path.normcase(normalize(b)):
        return True
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return False
