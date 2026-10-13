# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The article details a tab holds: those set in it (Article Details, the
publish window, a draft saved, a record restored), else its file's front
matter's.

A file's details are read from its front matter only when something first
asks for them (details_of): opening a file reads none, so a file nobody
publishes pays nothing for them. This is the one place that reads and
writes them on a tab. Read anywhere else, they could be read before that,
stale (tests/test_tab_details.py holds every other module to it).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from nostr.article_details import ArticleDetails, from_front_matter, kept_in_file

# The attribute of a tab (its editor) they live in.
_HELD = "_held_details"


@dataclass(frozen=True)
class _Held:
    # Those set in the tab; None: only its file's front matter's.
    details: Optional[ArticleDetails]
    # Whether ``details`` is read: its file's details read from its front
    # matter if none were set, and what the file keeps as written with them.
    read: bool


def details_of(ed) -> Optional[ArticleDetails]:
    """The details ``ed`` holds: those set in it, else its file's front
    matter's, read now if nothing has read them yet; with what the file
    keeps as written (ArticleDetails.kept). None when it holds none."""
    held = getattr(ed, _HELD, None)
    if held is None:
        return None
    if not held.read:
        front = getattr(ed, "_front_matter", None)
        details = held.details or from_front_matter(front)
        if front is not None:
            details = details.keeping(kept_in_file(front))
        held = _Held(details if details != ArticleDetails() or details.kept else None, True)
        setattr(ed, _HELD, held)
    return held.details


def hold(ed, details: Optional[ArticleDetails]) -> None:
    """``ed`` holds ``details``, set in it (a window handed them back, a
    draft was saved, a record restored); None: only its file's, read when
    something asks (a file opened or read again). What its file keeps as
    written is worked out as they are next read."""
    setattr(ed, _HELD, _Held(details, False))


def file_changed(ed) -> None:
    """``ed``'s front matter was written again: what it keeps as written is
    worked out again as its details are next read."""
    held = getattr(ed, _HELD, None)
    if held is not None and held.read:
        setattr(ed, _HELD, _Held(held.details, False))


def untouched(ed) -> bool:
    """Whether ``ed`` holds nothing but its file's details, still unread:
    nothing set in it could have changed them, so a save writes its front
    matter as it was, reading none."""
    held = getattr(ed, _HELD, None)
    return held is None or held.details is None and not held.read


def set_in_tab(ed) -> Optional[ArticleDetails]:
    """The details set in ``ed`` (or read from its file), without reading
    any: what its record keeps (recovery.tab_identity). Never stale for
    that: the file's own are in the record as its front matter, and are
    read from it when the tab comes back."""
    held = getattr(ed, _HELD, None)
    return held.details if held is not None else None
