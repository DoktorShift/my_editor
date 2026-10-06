# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Find and replace in a document, apart from any window.

The find bar (widgets.FindBar) asks; this module answers, over a plain
QTextDocument: where the text occurs, with or without matching case and
as whole words only, and what replacing it does. A replacement takes the
style of the text it replaces (bold stays bold, a link stays a link to
the same address), and Replace All is one step on the undo stack.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from typing import List, Tuple

from PySide6.QtGui import QTextCharFormat, QTextCursor, QTextDocument

Match = Tuple[int, int]          # (start, end) positions in the document


@dataclass(frozen=True)
class FindOptions:
    """How the text is looked for."""

    match_case: bool = False
    whole_words: bool = False

    def flags(self) -> QTextDocument.FindFlag:
        flags = QTextDocument.FindFlag(0)
        if self.match_case:
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if self.whole_words:
            flags |= QTextDocument.FindFlag.FindWholeWords
        return flags


def find_all(doc: QTextDocument, needle: str, options: FindOptions = FindOptions()
             ) -> List[Match]:
    """Every occurrence of ``needle``, in document order."""
    matches: List[Match] = []
    if not needle:
        return matches
    cursor = QTextCursor(doc)
    while True:
        cursor = doc.find(needle, cursor, options.flags())
        if cursor.isNull():
            return matches
        matches.append((cursor.selectionStart(), cursor.selectionEnd()))


def visible_matches(matches: List[Match], first: int, last: int) -> List[Tuple[int, Match]]:
    """The matches that touch ``first`` to ``last`` (what is on screen),
    each with its index: only those are painted, however many there are."""
    index = bisect_left(matches, first, key=lambda match: match[0])
    if index > 0 and matches[index - 1][1] > first:
        index -= 1
    shown = []
    while index < len(matches) and matches[index][0] <= last:
        shown.append((index, matches[index]))
        index += 1
    return shown


def _style_of(doc: QTextDocument, position: int) -> QTextCharFormat:
    """The style of the character at ``position``."""
    probe = QTextCursor(doc)
    probe.setPosition(position + 1)
    return QTextCharFormat(probe.charFormat())


def replace_match(doc: QTextDocument, match: Match, replacement: str) -> Match:
    """Replace one match, in the style of its first character. Returns
    where the replacement now is. One step on the undo stack."""
    start, end = match
    cursor = QTextCursor(doc)
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    style = _style_of(doc, start)
    cursor.beginEditBlock()
    cursor.insertText(replacement, style)
    cursor.endEditBlock()
    return start, start + len(replacement)


def replace_all(doc: QTextDocument, needle: str, replacement: str,
                options: FindOptions = FindOptions()) -> int:
    """Replace every occurrence; returns how many. One step on the undo
    stack, so Undo brings every one of them back."""
    matches = find_all(doc, needle, options)
    if not matches:
        return 0
    cursor = QTextCursor(doc)
    cursor.beginEditBlock()
    # From the end backwards: what is not replaced yet keeps its place.
    for start, end in reversed(matches):
        style = _style_of(doc, start)
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(replacement, style)
    cursor.endEditBlock()
    return len(matches)
