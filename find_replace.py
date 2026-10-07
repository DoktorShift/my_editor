# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Find and replace in a document, apart from any window.

The find bar (widgets.FindBar) asks; this module answers, over a plain
QTextDocument: where the text occurs, with or without matching case and
as whole words only, and what replacing it does. A replacement takes the
style of the text it replaces (bold stays bold, a link stays a link to
the same address), and Replace All is one step on the undo stack.

The text is searched as one string with a regular expression rather
than with QTextDocument.find, which goes back into Python for every
match (1.6 seconds for 55,000 matches in a long chapter, on every pause
in typing while the find bar is open; a few milliseconds this way).

Letters with accents can be stored two ways: as one character (é) or as
a letter followed by a combining accent (e and ´, common in text from
macOS file names and some PDFs). Both ways are found, whichever way the
search is typed, and "Cafe" does not find the "Cafe" inside "Café"
written the second way: the accent belongs to the last letter.
"""

from __future__ import annotations

import re
import unicodedata
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



def _in_word(char: str) -> bool:
    """Letters, numbers and the accents on them make words (Qt's own
    rule for whole words, with accents added)."""
    return char.isalnum() or unicodedata.combining(char) != 0


def _pattern(needle: str, options: FindOptions) -> re.Pattern:
    """Every place ``needle`` starts, written either way (one character
    per accented letter, or letter and accent)."""
    forms = sorted({needle, unicodedata.normalize("NFC", needle),
                    unicodedata.normalize("NFD", needle)}, key=len, reverse=True)
    flags = 0 if options.match_case else re.IGNORECASE
    # A lookahead finds overlapping places too, so a place that is not a
    # whole word cannot hide one that starts inside it.
    return re.compile("(?=(" + "|".join(re.escape(form) for form in forms) + "))", flags)


def find_all(doc: QTextDocument, needle: str, options: FindOptions = FindOptions()
             ) -> List[Match]:
    """Every occurrence of ``needle``, in document order, none
    overlapping the one before."""
    matches: List[Match] = []
    if not needle:
        return matches
    # The document's characters, one per position (paragraph ends and
    # table edges included), so string indexes are document positions.
    text = doc.toRawText()
    length = len(text)
    taken_until = 0
    for found in _pattern(needle, options).finditer(text):
        start, end = found.start(1), found.end(1)
        if start < taken_until or start == end:
            continue
        if end < length and unicodedata.combining(text[end]):
            continue                          # the last letter has an accent the search has not
        if options.whole_words and ((start > 0 and _in_word(text[start - 1]))
                                    or (end < length and _in_word(text[end]))):
            continue
        matches.append((start, end))
        taken_until = end
    return matches


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
