# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Markdown file's front matter: the block at its top that Jekyll, Hugo,
Obsidian and iA Writer keep a document's details in. YAML between ``---``
lines (closed by ``...`` too, as Pandoc allows), or TOML between ``+++``
lines, which Hugo's own new posts use.

The front matter as written is the source of truth. It is read as entries,
each its key and the lines written for it (the key's line and the indented
lines under it), and kept with the lines that open and close the block and
the blank lines after it, so that a file saved without a change to its
details comes back byte for byte, and a change rewrites only the entries it
touches.

Values are read the way front matter in the wild writes them, not as full
YAML: a plain or quoted text, a block of text (``|``, ``>``, with ``-`` or
``+``), a list in brackets (``[a, b]``) or a list of "- item" lines. A value
this reader cannot read fully (a mapping, Hugo's ``cover:`` with ``image``
and ``alt`` under it) reads as None: it is never shown as a detail and never
replaced. Values this app writes are quoted whenever YAML would read them as
something other than the text (a date, a number, true, a comment).

TOML is read with tomllib, one entry at a time, and written in TOML
(``key = "value"``, dates bare as TOML's own); its tables (``[params]``)
are kept whole and never read, and new entries go above them, where TOML
needs its plain keys.

A ``---`` block counts as front matter only when it holds at least one key;
otherwise it is text (a document that opens with a divider).
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Sequence, Tuple, Union

YAML = "yaml"
TOML = "toml"

# The block: "---" on the first line (after a byte order mark), then
# anything, then "---" (or "...") on a line of its own; for TOML "+++" both.
_BLOCK = re.compile(r"\A(﻿?---[ \t]*)\r?\n(.*?)(?:\r?\n)?^((?:---|\.\.\.)[ \t]*)(?:\r?\n|\Z)",
                    re.DOTALL | re.MULTILINE)
_TOML_BLOCK = re.compile(r"\A(﻿?\+\+\+[ \t]*)\r?\n(.*?)(?:\r?\n)?^(\+\+\+[ \t]*)(?:\r?\n|\Z)",
                         re.DOTALL | re.MULTILINE)
# A TOML key line (a bare, quoted or dotted key, then "="), and a table.
_TOML_KEY = re.compile(r"^([^\s#\[=][^=]*?)[ \t]*=")
_KEY = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*)[ \t]*:(?:[ \t]+(.*?))?[ \t]*$")
_BLOCK_SCALAR = re.compile(r"^([|>])([+-]?)(\d?)[ \t]*(?:#.*)?$")
# A plain value YAML reads as text again, as written: no indicator first,
# no ": " or " #" inside, and not a word or form YAML reads as another type.
_PLAIN = re.compile(r"^[^\s\-?:,\[\]{}#&*!|>'\"%@`][^\n]*$")
_YAML_WORDS = {"true", "false", "yes", "no", "on", "off", "y", "n", "null", "~"}
_YAML_NUMBER = re.compile(r"^[-+]?(?:0[xob][0-9a-fA-F_]+|[0-9][0-9_]*(?:\.[0-9_]*)?"
                          r"(?:[eE][-+]?[0-9]+)?|\.[0-9]+|\.inf|\.nan)$", re.IGNORECASE)
_YAML_DATE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}(?:[Tt ].*)?$")


@dataclass(frozen=True)
class Entry:
    """One key of the front matter, with the lines written for it."""

    key: str
    lines: Tuple[str, ...]
    syntax: str = YAML

    @property
    def written(self) -> str:
        """The value on the key's line as written, without a comment after
        it ("2024-03-01", '"My post"')."""
        if self.syntax == TOML:
            after = self.lines[0].partition("=")[2].strip()
        else:
            found = _KEY.match(self.lines[0])
            after = (found.group(2) or "") if found else ""
        return _without_comment(after).strip()

    @property
    def value(self) -> Union[str, List[str], None]:
        """The value as text, or as a list of texts for a list; None for a
        value this reader cannot read fully (a mapping, anything else)."""
        if self.syntax == TOML:
            return _toml_value(self)
        first = _KEY.match(self.lines[0])
        written = (first.group(2) or "") if first else ""
        rest = [line for line in self.lines[1:] if line.strip()
                and not line.lstrip().startswith("#")]
        block = _BLOCK_SCALAR.match(written)
        if block is not None:
            return _block_text(self.lines[1:], block.group(1), block.group(2))
        if written.startswith("["):
            close = written.rfind("]")
            rest = written[close + 1:] if close > 0 else ""
            if close > 0 and (not rest.strip() or re.match(r"\s+#", rest)):
                return [_unquote(part) for part in _split_inline(written[1:close])
                        if part.strip()]
        if not written and rest:
            if all(line.lstrip().startswith("- ") or line.strip() == "-" for line in rest):
                return [_unquote(line.strip()[1:].strip()) for line in rest]
            return None                     # a mapping or something else: unread
        if rest or written.startswith(("{", "&", "*", "!")):
            return None                     # continued over lines, or not plain YAML
        return _unquote(_without_comment(written))


@dataclass(frozen=True)
class FrontMatter:
    """A file's front matter as written: its entries, the lines that open
    and close it, and the blank lines between it and the text."""

    entries: Tuple[Entry, ...]
    opening: str = "---"
    closing: str = "---"
    gap: str = "\n"
    # Line endings as the file writes them.
    newline: str = "\n"
    changed: bool = field(default=False, compare=False)
    syntax: str = YAML

    def with_entries(self, entries: Sequence[Entry]) -> "FrontMatter":
        return FrontMatter(tuple(entries), self.opening, self.closing, self.gap, self.newline,
                           changed=tuple(entries) != self.entries or self.changed,
                           syntax=self.syntax)

    def where_new_entries_go(self, entries: Sequence[Entry]) -> int:
        """Where an entry added to ``entries`` goes: at the end, or in TOML
        above its first table (and the blank or comment lines before it),
        where TOML needs its plain keys."""
        at = next((i for i, entry in enumerate(entries) if entry.key.startswith("[")),
                  len(entries))
        if at == len(entries):
            return at
        while at > 0 and not entries[at - 1].key:
            at -= 1
        return at


def split(text: str) -> Tuple[Optional[FrontMatter], str]:
    """``(front matter, body)`` of ``text``, or ``(None, text)`` when it has
    no front matter (no block at its top, or a block without a key)."""
    text = text or ""
    syntax = TOML
    found = _TOML_BLOCK.match(text)
    if found is None:
        syntax = YAML
        found = _BLOCK.match(text)
    if found is None:
        return None, text
    lines = found.group(2).splitlines()
    entries = _toml_entries(lines) if syntax == TOML else _yaml_entries(lines)
    if not any(entry.key for entry in entries):
        return None, text                   # a divider, some text, a divider: text
    entries = _with_trailers_apart(entries)
    newline = "\r\n" if "\r\n" in found.group(0) else "\n"
    rest = text[found.end():]
    body = rest.lstrip("\r\n")
    return (FrontMatter(tuple(entries), found.group(1), found.group(3),
                        rest[:len(rest) - len(body)], newline, syntax=syntax), body)


def _yaml_entries(lines: Sequence[str]) -> List[Entry]:
    entries: List[Entry] = []
    for line in lines:
        key = _KEY.match(line)
        if key is not None and not line[:1].isspace():
            entries.append(Entry(key.group(1), (line,)))
        elif entries:
            entries[-1] = Entry(entries[-1].key, entries[-1].lines + (line,))
        else:
            # Lines before any key (a comment, a blank line): kept with no key.
            entries.append(Entry("", (line,)))
    return entries


def _toml_entries(lines: Sequence[str]) -> List[Entry]:
    """TOML's entries: each plain key with its lines (a text or a list may
    go on over lines); a table, from its header on, is one entry, kept whole,
    whose key is its header."""
    entries: List[Entry] = []
    for line in lines:
        last = entries[-1] if entries else None
        open_text = last is not None and _open_multiline("\n".join(last.lines))
        key = _TOML_KEY.match(line)
        if last is not None and (open_text or last.key.startswith("[")):
            entries[-1] = Entry(last.key, last.lines + (line,), TOML)
        elif line.startswith("["):
            entries.append(Entry(line.strip(), (line,), TOML))
        elif key is not None:
            entries.append(Entry(key.group(1).strip(), (line,), TOML))
        elif last is not None:
            entries[-1] = Entry(last.key, last.lines + (line,), TOML)
        else:
            entries.append(Entry("", (line,), TOML))
    return entries


def _open_multiline(written: str) -> bool:
    """Whether a TOML multi-line text is still open at the end of ``written``."""
    return written.count('"""') % 2 == 1 or written.count("'''") % 2 == 1


def _toml_value(entry: Entry) -> Union[str, List[str], None]:
    """A TOML entry's value as text (a date or a time as ISO 8601), or a
    list of texts; None for a table or any other kind of value."""
    if not entry.key or entry.key.startswith("["):
        return None
    try:
        values = tomllib.loads("\n".join(entry.lines))
    except tomllib.TOMLDecodeError:
        return None
    if len(values) != 1:
        return None
    value = next(iter(values.values()))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


def _with_trailers_apart(entries: List[Entry]) -> List[Entry]:
    """The blank and comment lines at the end of each entry as an entry of
    their own, without a key: they introduce what follows (or close the
    block), so rewriting the entry before them leaves them where they are."""
    apart: List[Entry] = []
    for entry in entries:
        lines = list(entry.lines)
        trailer: List[str] = []
        while entry.key and len(lines) > 1 and (
                not lines[-1].strip() or lines[-1].startswith("#")):
            trailer.insert(0, lines.pop())
        apart.append(Entry(entry.key, tuple(lines), entry.syntax))
        if trailer:
            apart.append(Entry("", tuple(trailer), entry.syntax))
    return apart


def join(front: Optional[FrontMatter], body: str) -> str:
    """``body`` under ``front`` as it was written, or alone when there is
    none (or no entry is left in it)."""
    if front is None or not any(entry.key for entry in front.entries):
        return body
    nl = front.newline
    lines = [line for entry in front.entries for line in entry.lines]
    # The blank lines after it as they were: none when the text began
    # right under it.
    return (front.opening + nl + nl.join(lines) + (nl if lines else "") + front.closing
            + nl + front.gap.replace("\r\n", "\n").replace("\n", nl) + body.lstrip("\n"))


def entry(key: str, value: Union[str, Sequence[str]], *, syntax: str = YAML,
          bare: bool = False) -> Entry:
    """An entry for ``key`` written the way this app writes one: a text,
    quoted when it has to be, or a list in brackets; in TOML, ``key =``
    and its texts always quoted. ``bare``: ``value`` is written as it is
    (a date in a form both read as a date)."""
    if syntax == TOML:
        if isinstance(value, str):
            written = value if bare else json.dumps(value, ensure_ascii=False)
        else:
            written = "[" + ", ".join(json.dumps(item, ensure_ascii=False)
                                      for item in value) + "]"
        return Entry(key, (f"{key} = {written}",), TOML)
    if isinstance(value, str):
        return Entry(key, (f"{key}: {value if bare else _quoted(value)}",))
    items = ", ".join(_quoted(item, in_list=True) for item in value)
    return Entry(key, (f"{key}: [{items}]",))


def _quoted(value: str, *, in_list: bool = False) -> str:
    """``value`` as YAML reads it back as the same text: bare when it can
    be, else in double quotes."""
    plain = (_PLAIN.match(value) is not None and value == value.strip()
             and ": " not in value and " #" not in value and not value.endswith(":")
             and value.lower() not in _YAML_WORDS and not _YAML_NUMBER.match(value)
             and not _YAML_DATE.match(value)
             and not (in_list and any(char in value for char in ",[]{}")))
    return value if plain else json.dumps(value, ensure_ascii=False)


def _block_text(lines: Sequence[str], style: str, chomping: str) -> str:
    """The text of a block scalar: its indented lines, as they are for "|",
    folded into one line per paragraph for ">"."""
    body = list(lines)
    indents = [len(line) - len(line.lstrip(" ")) for line in body if line.strip()]
    cut = min(indents) if indents else 0
    rows = [line[cut:] if line.strip() else "" for line in body]
    while rows and not rows[-1]:
        rows.pop()
    if style == "|":
        text = "\n".join(rows)
    else:
        paragraphs, current = [], []
        for row in rows:
            if row:
                current.append(row.strip())
            elif current:
                paragraphs.append(" ".join(current))
                current = []
        if current:
            paragraphs.append(" ".join(current))
        text = "\n".join(paragraphs)
    return text if chomping == "-" else text + "\n" if chomping == "+" else text


# A quoted value: "..." with its escapes, or '...' with '' for a quote.
_QUOTED = re.compile(r"""^("(?:[^"\\]|\\.)*"|'(?:[^']|'')*')""")


def _without_comment(written: str) -> str:
    """A value without the comment after it ("post  # Jekyll's", or
    '"My post"  # shown on the site')."""
    quoted = _QUOTED.match(written)
    if quoted is not None:
        rest = written[quoted.end():]
        return quoted.group(1) if not rest.strip() or re.match(r"\s+#", rest) else written
    found = re.search(r"\s+#", written)
    return written[:found.start()] if found else written


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return str(json.loads(value))
        except ValueError:
            return value[1:-1]
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def _split_inline(inside: str) -> List[str]:
    """The items of an inline list, commas inside quotes kept."""
    items, current, quote = [], "", ""
    for char in inside:
        if quote:
            current += char
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
            current += char
        elif char == ",":
            items.append(current)
            current = ""
        else:
            current += char
    items.append(current)
    return items
