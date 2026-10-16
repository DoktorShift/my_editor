# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where the words are in Markdown text, for spell checking.

A writer's text holds more than words: web, e-mail and Nostr addresses,
``nostr:`` references, mentions and hashtags, code, numbers, Markdown
and HTML markup. None of that is checked. :func:`scan` finds the words
that are, one block of text at a time, and carries what a line leaves
open (a code fence, front matter, an HTML comment) to the next block in
a :class:`State`, so a document is scanned block by block in order.

What counts as a word:

- letters of any script, with their combining marks, so "naïve",
  "Straße" and "Grüße" are one word even when typed decomposed;
- joined by apostrophes ("don't", "geht’s"), hyphens ("E-Mail-Adresse",
  checked whole and then part by part) and soft hyphens;
- not single letters, not abbreviations in capitals ("NASA", "BTC", as
  the macOS checker does), not anything with a digit ("mp3", "2nd"),
  an underscore ("snake_case") or a dot inside ("example.com",
  "z.B.", "v3.3").

Offsets are Python string indices. Qt, macOS and Windows count in
UTF-16 units; :func:`utf16_offsets` translates where a character
outside the Basic Multilingual Plane (an emoji) makes them differ.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from typing import Iterable, List, NamedTuple, Optional, Sequence, Tuple

from doc_walk import LINE_SEPARATOR, OBJECT_REPLACEMENT

from .backends import MAX_WORD_LENGTH

Span = Tuple[int, int]

SOFT_HYPHEN = "\u00ad"
_HYPHENS = "-\u2010\u2011"

MAX_BLOCK_LENGTH = 20_000
"""A paragraph longer than this (some 3,000 words, six pages without a
break) is a pasted dump, not prose: it is not read for words, so no
text can make checking it hold up typing."""


class State(NamedTuple):
    """What the text so far leaves open for the next line."""

    fence: str = ""
    """The code fence that opened a code block ("```", "~~~~"), or ""."""
    front_matter: bool = False
    """Inside the YAML details that open a document."""
    comment: bool = False
    """Inside an HTML comment."""
    blank: bool = True
    """The line before was blank (or there was none): an indented line
    starts a code block here."""
    indented_code: bool = False
    """The line before was indented code."""
    first: bool = True
    """No line yet: front matter can still begin."""
    in_list: bool = False
    """Inside a list, whose items' own paragraphs are indented too."""


START = State()
"""The state at the top of a document."""


class Scan(NamedTuple):
    """The words of one block of text, and what it leaves open."""

    words: Tuple[Span, ...]
    """(start, end) of every word to check, in order."""
    skipped: Tuple[Span, ...]
    """(start, end) of everything that is not prose, merged, in order."""
    state: State
    """The state at the end of the block, for the next one."""


# Combining marks (Unicode categories Mn, Mc and Me) of the Basic
# Multilingual Plane. Python's \w leaves them out, which would cut
# "naïve" typed with a combining diaeresis, and every word of Hindi or
# Thai, into pieces. Computing them at import takes a tenth of a
# second, so they are listed. Generated from unicodedata 15.0:
#   [c for c in range(0x10000) if unicodedata.category(chr(c))[0] == "M"]
_MARKS = (
    r"\u0300-\u036f\u0483-\u0489\u0591-\u05bd\u05bf\u05c1\u05c2\u05c4\u05c5"
    r"\u05c7\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06dc\u06df-\u06e4"
    r"\u06e7\u06e8\u06ea-\u06ed\u0711\u0730-\u074a\u07a6-\u07b0\u07eb-\u07f3"
    r"\u07fd\u0816-\u0819\u081b-\u0823\u0825-\u0827\u0829-\u082d\u0859-\u085b"
    r"\u0898-\u089f\u08ca-\u08e1\u08e3-\u0903\u093a-\u093c\u093e-\u094f"
    r"\u0951-\u0957\u0962\u0963\u0981-\u0983\u09bc\u09be-\u09c4\u09c7\u09c8"
    r"\u09cb-\u09cd\u09d7\u09e2\u09e3\u09fe\u0a01-\u0a03\u0a3c\u0a3e-\u0a42"
    r"\u0a47\u0a48\u0a4b-\u0a4d\u0a51\u0a70\u0a71\u0a75\u0a81-\u0a83\u0abc"
    r"\u0abe-\u0ac5\u0ac7-\u0ac9\u0acb-\u0acd\u0ae2\u0ae3\u0afa-\u0aff"
    r"\u0b01-\u0b03\u0b3c\u0b3e-\u0b44\u0b47\u0b48\u0b4b-\u0b4d\u0b55-\u0b57"
    r"\u0b62\u0b63\u0b82\u0bbe-\u0bc2\u0bc6-\u0bc8\u0bca-\u0bcd\u0bd7"
    r"\u0c00-\u0c04\u0c3c\u0c3e-\u0c44\u0c46-\u0c48\u0c4a-\u0c4d\u0c55\u0c56"
    r"\u0c62\u0c63\u0c81-\u0c83\u0cbc\u0cbe-\u0cc4\u0cc6-\u0cc8\u0cca-\u0ccd"
    r"\u0cd5\u0cd6\u0ce2\u0ce3\u0cf3\u0d00-\u0d03\u0d3b\u0d3c\u0d3e-\u0d44"
    r"\u0d46-\u0d48\u0d4a-\u0d4d\u0d57\u0d62\u0d63\u0d81-\u0d83\u0dca"
    r"\u0dcf-\u0dd4\u0dd6\u0dd8-\u0ddf\u0df2\u0df3\u0e31\u0e34-\u0e3a"
    r"\u0e47-\u0e4e\u0eb1\u0eb4-\u0ebc\u0ec8-\u0ece\u0f18\u0f19\u0f35\u0f37"
    r"\u0f39\u0f3e\u0f3f\u0f71-\u0f84\u0f86\u0f87\u0f8d-\u0f97\u0f99-\u0fbc"
    r"\u0fc6\u102b-\u103e\u1056-\u1059\u105e-\u1060\u1062-\u1064\u1067-\u106d"
    r"\u1071-\u1074\u1082-\u108d\u108f\u109a-\u109d\u135d-\u135f\u1712-\u1715"
    r"\u1732-\u1734\u1752\u1753\u1772\u1773\u17b4-\u17d3\u17dd\u180b-\u180d"
    r"\u180f\u1885\u1886\u18a9\u1920-\u192b\u1930-\u193b\u1a17-\u1a1b"
    r"\u1a55-\u1a5e\u1a60-\u1a7c\u1a7f\u1ab0-\u1ace\u1b00-\u1b04\u1b34-\u1b44"
    r"\u1b6b-\u1b73\u1b80-\u1b82\u1ba1-\u1bad\u1be6-\u1bf3\u1c24-\u1c37"
    r"\u1cd0-\u1cd2\u1cd4-\u1ce8\u1ced\u1cf4\u1cf7-\u1cf9\u1dc0-\u1dff"
    r"\u20d0-\u20f0\u2cef-\u2cf1\u2d7f\u2de0-\u2dff\u302a-\u302f\u3099\u309a"
    r"\ua66f-\ua672\ua674-\ua67d\ua69e\ua69f\ua6f0\ua6f1\ua802\ua806\ua80b"
    r"\ua823-\ua827\ua82c\ua880\ua881\ua8b4-\ua8c5\ua8e0-\ua8f1\ua8ff"
    r"\ua926-\ua92d\ua947-\ua953\ua980-\ua983\ua9b3-\ua9c0\ua9e5\uaa29-\uaa36"
    r"\uaa43\uaa4c\uaa4d\uaa7b-\uaa7d\uaab0\uaab2-\uaab4\uaab7\uaab8"
    r"\uaabe\uaabf\uaac1\uaaeb-\uaaef\uaaf5\uaaf6\uabe3-\uabea\uabec\uabed"
    r"\ufb1e\ufe00-\ufe0f\ufe20-\ufe2f"
)

# Letters, digits and underscores (sorted out later), with their marks,
# joined by an apostrophe, a hyphen, a soft hyphen, a middle dot (Catalan
# "col·lecció") or a dot ("example.com", sorted out later).
_LETTERS = rf"[\w{_MARKS}]+"
_TOKEN = re.compile(rf"{_LETTERS}(?:['\u2019\-\u2010\u2011\u00ad\u00b7.]{_LETTERS})*")
_DIGIT = re.compile(r"\d")

# What is not prose inside a line, in one pass.
_NOT_PROSE = re.compile("|".join(f"(?:{pattern})" for pattern in (
    # Inline code, between equal runs of backticks.
    r"(?<!`)(?P<ticks>`+)(?!`).+?(?<!`)(?P=ticks)(?!`)",
    # Autolinks: <https://example.com>, <alice@example.com>.
    r"<(?:[A-Za-z][A-Za-z0-9+.\-]{1,31}:[^\s<>]*|[^\s<>@]+@[^\s<>@]+)>",
    # HTML tags with their attributes.
    r"</?[A-Za-z][A-Za-z0-9\-]*(?:\s[^<>]*)?/?>",
    # A link's or an image's address: [text](address "title"); the text
    # in the brackets is prose.
    r"\]\((?:<[^<>]*>|[^\s()]*(?:\([^\s()]*\)[^\s()]*)*)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^()]*\)))?\s*\)",
    # Reference labels [text][label] and footnotes [^note].
    r"\]\[[^\[\]]*\]",
    r"\[\^[^\[\]\s]+\]",
    # Addresses: with a scheme, nostr: and other references, www. A
    # pattern that can start inside a long run of letters, dots and
    # hyphens starts only where the run starts: tried from every position
    # of a pasted token, it would take time in the square of its length.
    r"(?i:(?<![\w+.\-])[a-z][a-z0-9+.\-]*://[^\s<>\"]+)",
    r"(?i:\b(?:mailto|nostr|lightning|bitcoin|magnet|tel|sms|geo|urn|cashu|lnurl[a-z]*)"
    r":[^\s<>\"]+)",
    r"(?i:\bwww\.[^\s<>\"]+)",
    # Addresses written without a scheme, with a path: github.com/rinbal.
    r"(?<![\w.\-/@])[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}/[^\s<>\"]*",
    # E-mail and Nostr addresses (alice@example.com, _@example.com).
    r"(?<![\w.+\-])[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+",
    # Mentions (@alice, @npub1...) and hashtags (#bitcoin).
    r"(?<![\w@])@[\w.\-]+",
    r"(?<![\w&#/])#\w[\w\-]*",
    # Nostr keys and references written without nostr:.
    r"(?i:\b(?:npub|nsec|note|nprofile|nevent|naddr|nrelay|ncryptsec)1[02-9ac-hj-np-z]{6,})",
    # Character references: &amp; &#8212; &#x2014;.
    r"&(?:#\d+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);",
    # Paths: /usr/local/bin, ~/notes/draft.md, C:\Users.
    r"(?<![\w/~.])(?:~|\.{1,2})?(?:/[\w.\-~%]+){2,}/?",
    r"\b[A-Za-z]:\\\S*",
    # Emoji shortcodes (:zap:), which Nostr clients show as pictures.
    r"(?<![\w:]):[\w+\-]+:(?![\w:])",
)))

# Lines that are not prose as a whole.
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
_LIST_ITEM = re.compile(r"\s*(?:[-*+\u2022]|\d{1,9}[.)])(?:\s|$)")
# A link reference definition, whole: [label]: address "title". Not a
# footnote ([^1]: ...), whose text is prose, and not a line that only
# begins like one ("[Note]: Read this first.").
_REFERENCE = re.compile(r" {0,3}\[(?!\^)[^\]]+\]:[ \t]*(?:<[^<>]*>|\S+)"
                        r"(?:[ \t]+(?:\"[^\"]*\"|'[^']*'|\([^()]*\)))?[ \t]*")
_FRONT_MATTER = re.compile(r"---[ \t]*")
_FRONT_MATTER_END = re.compile(r"(?:---|\.\.\.)[ \t]*")
# What YAML lines look like: indented, a comment, a list item, a key.
# Keys start in lower case (title, tags, output), so a document that
# begins with a rule and then "Note: ..." is read as prose.
_YAML_LINE = re.compile(r"(?:\s.*|#.*|-(?:\s.*)?|[a-z_\"'][\w .\"'\-]*:(?:\s.*)?|)")

# What a system checker must not read: line and object breaks become spaces.
_BLANKS = str.maketrans({LINE_SEPARATOR: " ", OBJECT_REPLACEMENT: " ", "\x00": " "})


# -- scanning -----------------------------------------------------------------

def scan(text: str, state: State = START, skipped: Iterable[Span] = ()) -> Scan:
    """The words to check in ``text`` (one block: a paragraph, or one
    line of a plain text document), starting in ``state``. ``skipped``
    adds spans the caller knows are not prose (formatted code)."""
    if len(text) > MAX_BLOCK_LENGTH:
        return Scan((), ((0, len(text)),), advance(text, state))
    spans: List[Span] = list(skipped)
    offset = 0
    for line in text.split(LINE_SEPARATOR):
        state, whole_line, partial = _line(line, state)
        if whole_line:
            spans.append((offset, offset + len(line)))
        spans.extend((offset + start, offset + end) for start, end in partial)
        offset += len(line) + 1
    spans.extend(match.span() for match in _NOT_PROSE.finditer(text))
    candidates: List[Span] = []
    for match in _TOKEN.finditer(text):
        start, end = match.span()
        word_start, word_end = start, end
        while word_start < word_end and text[word_start] == "_":
            word_start += 1
        while word_end > word_start and text[word_end - 1] == "_":
            word_end -= 1
        # Underscores around a word are Markdown emphasis (_word_): markup,
        # which a system checker must not read as part of the word.
        if word_start > start:
            spans.append((start, word_start))
        if word_end < end:
            spans.append((word_end, end))
        if word_start < word_end and checkable(text[word_start:word_end]):
            candidates.append((word_start, word_end))
    merged = _merged(spans)
    return Scan(tuple(_outside(candidates, merged)), tuple(merged), state)


def advance(text: str, state: State = START) -> State:
    """The state after ``text``, without looking for words: the cheap
    pass that keeps code fences right across a whole document."""
    for line in text.split(LINE_SEPARATOR):
        state = _line(line, state)[0]
    return state


def _indent(line: str) -> int:
    """How many columns a line is indented (a tab reaches the next four)."""
    column = 0
    for ch in line:
        if ch == " ":
            column += 1
        elif ch == "\t":
            column += 4 - column % 4
        else:
            break
    return column


def passed_code(state: State) -> State:
    """The state after a block that is code by its formatting (a code
    block Qt read from Markdown): it opens or closes nothing."""
    return state._replace(first=False, blank=False, indented_code=False)


def _line(line: str, state: State) -> Tuple[State, bool, List[Span]]:
    """The state after one line, whether the whole line is not prose,
    and the spans of it that are not (HTML comments)."""
    if state.fence:
        match = _FENCE.fullmatch(line)
        if (match and match.group(1)[0] == state.fence[0]
                and len(match.group(1)) >= len(state.fence) and not match.group(2).strip()):
            return state._replace(fence="", blank=False, first=False), True, []
        return state._replace(first=False), True, []
    if state.front_matter:
        if _FRONT_MATTER_END.fullmatch(line):
            return State(blank=False, first=False), True, []
        if _YAML_LINE.fullmatch(line):
            return state, True, []
        # Not YAML after all: a text that begins with a rule. Read on as text.
        state = State(first=False)
    if state.first and _FRONT_MATTER.fullmatch(line):
        return State(front_matter=True, first=False), True, []

    blank = not line.strip()
    indent = _indent(line)
    item = bool(_LIST_ITEM.match(line))
    # A list goes on through blank and indented lines, and ends at the
    # first line back at the margin after a blank line.
    in_list = item or (state.in_list and (blank or indent > 0 or not state.blank))
    partial: List[Span] = []
    position = 0
    if state.comment:
        end = line.find("-->")
        if end < 0:
            return state._replace(first=False, in_list=in_list), True, []
        partial.append((0, end + 3))
        position = end + 3
    else:
        match = _FENCE.fullmatch(line)
        if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
            return State(fence=match.group(1), blank=False, first=False,
                         in_list=in_list), True, []
        # Indented code: four columns in after a blank line. In a list,
        # where an item's further paragraphs are indented by four, code
        # needs four more.
        if (not blank and not item and (state.blank or state.indented_code)
                and indent >= (8 if state.in_list else 4)):
            return State(indented_code=True, blank=False, first=False,
                         in_list=in_list), True, []
        if _REFERENCE.fullmatch(line):
            return State(blank=False, first=False, in_list=in_list), True, []

    comment = False
    while True:
        start = line.find("<!--", position)
        if start < 0:
            break
        end = line.find("-->", start + 4)
        if end < 0:
            partial.append((start, len(line)))
            comment = True
            break
        partial.append((start, end + 3))
        position = end + 3
    return State(comment=comment, blank=blank, first=False, in_list=in_list), False, partial


def checkable(word: str) -> bool:
    """Whether ``word`` is a word to check: not a single letter, an
    abbreviation in capitals, a number or anything with a digit, an
    identifier with an underscore, or a name with a dot inside."""
    letters = word.replace(SOFT_HYPHEN, "")
    if not (2 <= len(letters) <= MAX_WORD_LENGTH):
        return False
    if "_" in letters or "." in letters or _DIGIT.search(letters):
        return False
    if sum(1 for ch in letters if ch.isalpha()) < 2:
        return False
    return not letters.isupper()


def hyphen_parts(text: str, start: int, end: int) -> List[Span]:
    """The parts of a hyphenated word that are words themselves, to
    check one by one when the whole word is not known."""
    parts: List[Span] = []
    part_start = start
    for index in range(start, end + 1):
        if index == end or text[index] in _HYPHENS:
            if checkable(text[part_start:index]):
                parts.append((part_start, index))
            part_start = index + 1
    return parts


def is_hyphenated(word: str) -> bool:
    return any(ch in _HYPHENS for ch in word)


def clean(word: str) -> str:
    """A word as a checker should see it: without soft hyphens, with its
    letters composed (a + combining diaeresis becomes ä)."""
    return unicodedata.normalize("NFC", word.replace(SOFT_HYPHEN, ""))


def masked(text: str, skipped: Sequence[Span]) -> str:
    """``text`` with everything that is not prose blanked out, for a
    system checker that reads whole paragraphs. Same length, so the
    offsets it reports point into ``text``."""
    parts: List[str] = []
    position = 0
    for start, end in skipped:
        parts.append(text[position:start])
        parts.append(" " * (end - start))
        position = end
    parts.append(text[position:])
    return "".join(parts).translate(_BLANKS)


def _merged(spans: List[Span]) -> List[Span]:
    out: List[Span] = []
    for start, end in sorted(span for span in spans if span[1] > span[0]):
        if out and start <= out[-1][1]:
            if end > out[-1][1]:
                out[-1] = (out[-1][0], end)
        else:
            out.append((start, end))
    return out


def _outside(words: Iterable[Span], skipped: Sequence[Span]) -> List[Span]:
    """The words that touch nothing skipped (both in order)."""
    out: List[Span] = []
    index = 0
    for word in words:
        start, end = word
        while index < len(skipped) and skipped[index][1] <= start:
            index += 1
        if index < len(skipped) and skipped[index][0] < end:
            continue
        out.append(word)
    return out


# -- UTF-16 ------------------------------------------------------------------

_ASTRAL = re.compile("[\U00010000-\U0010ffff]")


def utf16_offsets(text: str) -> Optional[List[int]]:
    """For every index into ``text`` (and its end), the UTF-16 offset
    Qt, macOS and Windows count; None when they are all the same."""
    if text.isascii() or not _ASTRAL.search(text):
        return None
    offsets = [0] * (len(text) + 1)
    unit = 0
    for index, ch in enumerate(text):
        offsets[index] = unit
        unit += 2 if ord(ch) > 0xFFFF else 1
    offsets[len(text)] = unit
    return offsets


def to_utf16(offsets: Optional[List[int]], index: int) -> int:
    return index if offsets is None else offsets[index]


def from_utf16(offsets: Optional[List[int]], unit: int) -> int:
    return unit if offsets is None else bisect.bisect_left(offsets, unit)
