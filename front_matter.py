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

A ``---`` block counts as front matter when it holds at least one key, or
nothing at all; otherwise it is text (a document that opens with a divider).

What this reader makes of a file, and every change written to it, is held
against what a YAML parser (PyYAML) or tomllib reads there (parsed): a
detail they read otherwise is never read or changed by this app.
"""

from __future__ import annotations

import functools
import json
import re
import tomllib
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import AbstractSet, Any, FrozenSet, List, Optional, Sequence, Tuple, Union

import yaml

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
# A YAML key line: a key, plain or quoted ("tags", 'tags'), then ":".
_KEY = re.compile(r"""^("[^"\n]*"|'[^'\n]*'|[A-Za-z0-9_][A-Za-z0-9_.-]*)[ \t]*:(?:[ \t]+(.*?))?[ \t]*$""")
_BLOCK_SCALAR = re.compile(r"^([|>])([+-]?)(\d?)[ \t]*(?:#.*)?$")
# A plain value YAML reads as text again, as written: no indicator first,
# no ": " or " #" inside, and not a word or form YAML reads as another type.
_PLAIN = re.compile(r"^[^\s\-?:,\[\]{}#&*!|>'\"%@`][^\n]*$")
_YAML_WORDS = {"true", "false", "yes", "no", "on", "off", "y", "n", "null", "~"}
# How YAML writes "no value" (unquoted).
_YAML_NULLS = {"~", "null", "Null", "NULL"}
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
    def table(self) -> str:
        """The name of the TOML table this entry is the header of
        ("[ taxonomies ]  # terms" is "taxonomies"); "" for any other."""
        if self.syntax != TOML:
            return ""
        found = re.match(r"^\[\s*([^\[\]]+?)\s*\]\s*(?:#.*)?$", self.key)
        return found.group(1).strip("\"'") if found else ""

    def inner_entries(self) -> List["Entry"]:
        """A TOML table's own entries ("[taxonomies]" and the keys under it);
        none for any other entry."""
        if self.syntax != TOML or not self.key.startswith("["):
            return []
        return _with_trailers_apart(_toml_entries(self.lines[1:]))

    def with_inner(self, inner: Sequence["Entry"]) -> "Entry":
        """This TOML table with ``inner`` as its entries."""
        return Entry(self.key, (self.lines[0],) + tuple(
            line for entry in inner for line in entry.lines), self.syntax)

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
            flow = _flow_list(written, self.lines[1:])
            if flow is None:
                return None                 # not closed where it can be read
            return [_unquote(part) for part in _split_inline(flow[0][1:-1]) if part.strip()]
        if not written and rest:
            if all(line.lstrip().startswith("- ") or line.strip() == "-" for line in rest):
                return [_list_item(line) for line in rest]
            return None                     # a mapping or something else: unread
        if rest or written.startswith(("{", "&", "*", "!")):
            return None                     # continued over lines, or not plain YAML
        plain = _without_comment(written)
        if plain in _YAML_NULLS:
            return ""                       # YAML's "no value"
        return _unquote(plain)


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
    # The names of the details a change to was refused here, as a parser
    # would have read it otherwise (article_details): kept as written.
    kept: FrozenSet[str] = field(default=frozenset(), compare=False)

    def with_entries(self, entries: Sequence[Entry]) -> "FrontMatter":
        return FrontMatter(tuple(entries), self.opening, self.closing, self.gap, self.newline,
                           changed=tuple(entries) != self.entries or self.changed,
                           syntax=self.syntax, kept=self.kept)

    def with_kept(self, names: AbstractSet[str]) -> "FrontMatter":
        return replace(self, kept=self.kept | frozenset(names))

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
    if lines and not any(entry.key for entry in entries):
        return None, text                   # a divider, some text, a divider: text
    entries = _with_trailers_apart(entries)
    newline = "\r\n" if "\r\n" in found.group(0) else "\n"
    rest = text[found.end():]
    body = rest.lstrip("\r\n")
    return (FrontMatter(tuple(entries), found.group(1), found.group(3),
                        rest[:len(rest) - len(body)], newline, syntax=syntax), body)


def parsed(front: Optional[FrontMatter]) -> Optional[dict]:
    """``front`` as a YAML parser (or tomllib) reads it: its keys and their
    values ({} for none), or None when it reads no mapping there, or one
    too large to hold against (an alias repeated over and over). Shared:
    never changed by its reader."""
    if front is None:
        return {}
    # Each line ends in a line break, the last one too, as in the file: a
    # block text at the end ("|+") keeps its own.
    return _parsed_text(front.syntax, "".join(line + "\n" for entry in front.entries
                                              for line in entry.lines))


# The most values a front matter is held against: far more than any holds,
# far fewer than an alias repeated within itself ("billion laughs") gives.
_MOST_VALUES = 10_000


# PyYAML's own reader, never its libyaml one: that reads some files (a tab
# in a list) the other refuses, and is not on every system.
@functools.lru_cache(maxsize=64)
def _parsed_text(syntax: str, text: str) -> Optional[dict]:
    try:
        mapping = tomllib.loads(text) if syntax == TOML else yaml.load(text, Loader=yaml.SafeLoader)
        if mapping is None and syntax == YAML:
            mapping = {}                    # nothing but comments and blanks
        if not isinstance(mapping, dict) or not _within(mapping, [_MOST_VALUES]):
            return None
    except (yaml.YAMLError, ValueError, TypeError, RecursionError):
        # Not YAML or TOML; a date no calendar has; nested past counting.
        return None
    return mapping


def _within(value: Any, left: List[int]) -> bool:
    """Whether ``value`` holds no more values than ``left[0]`` (counted
    down as they are met, each alias again)."""
    left[0] -= 1
    if left[0] < 0:
        return False
    if isinstance(value, dict):
        return all(_within(key, left) and _within(item, left) for key, item in value.items())
    if isinstance(value, list):
        return all(_within(item, left) for item in value)
    return True


def _yaml_entries(lines: Sequence[str]) -> List[Entry]:
    entries: List[Entry] = []
    for line in lines:
        key = _KEY.match(line)
        if key is not None and not line[:1].isspace():
            entries.append(Entry(_unquote(key.group(1)), (line,)))
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
            entries.append(Entry(_unquote(key.group(1).strip()), (line,), TOML))
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
    if front is None or front.changed and not any(entry.key for entry in front.entries):
        return body                         # none, or none left (an empty one stays)
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


def rewritten(old: Entry, value: Union[str, Sequence[str]], *, bare: bool = False) -> Entry:
    """``old`` with ``value`` in its place, in the file's own style: the key
    and the spaces after it as written, a comment after the value kept,
    quotes of the kind it had, a block list a block list, an inline list
    inline with the items that stay as they were written, a block text a
    block text. A value written in a way this can't follow is written as
    entry() writes a new one; and in TOML, anything TOML would not read
    (never written)."""
    written = _rewritten(old, value, bare)
    return _valid(written, value, bare) if old.syntax == TOML else written


def _rewritten(old: Entry, value: Union[str, Sequence[str]], bare: bool) -> Entry:
    key_part, rest = _key_and_rest(old)
    if key_part is None:
        return entry(old.key, value, syntax=old.syntax, bare=bare)
    if old.syntax == YAML and not key_part[-1:].isspace():
        # A key left empty to fill ("title:"): YAML needs a space after it.
        key_part += " "
    written, comment = _value_and_comment(rest)
    if isinstance(value, str):
        block = _BLOCK_SCALAR.match(written) if old.syntax == YAML else None
        if block is not None:
            if block.group(1) == ">":
                # Folded, YAML reads a line break as a space, and n empty
                # lines as n line breaks.
                value = re.sub(r"\n+", lambda breaks: breaks.group(0) + "\n", value)
            return Entry(old.key, (key_part + written + comment,)
                         + _block_lines(old.lines[1:], value), old.syntax)
        if len(old.lines) > 1 and old.syntax == TOML:
            return entry(old.key, value, syntax=old.syntax, bare=bare)
        return Entry(old.key, (key_part + _like(written, value, old.syntax, bare) + comment,)
                     + tuple(old.lines[1:]), old.syntax)
    items = list(value)
    if old.syntax == TOML and written == "[" and len(old.lines) > 2 \
            and old.lines[-1].strip().startswith("]"):
        lines = _multi_line_list_like(old.lines[1:-1], items)
        if lines is not None:
            return Entry(old.key, (old.lines[0],) + lines + (old.lines[-1],), old.syntax)
        return entry(old.key, items, syntax=old.syntax, bare=bare)
    flow = _flow_list(rest, old.lines[1:]) if rest.startswith("[") else None
    if flow is not None:
        # An inline list, on its line or over lines: written on one line,
        # the whole of it, its comments after it.
        return Entry(old.key, (key_part + _inline_list_like(flow[0], items, old.syntax)
                               + flow[1],) + flow[2], old.syntax)
    if old.syntax == YAML and not written and old.value is not None:
        return Entry(old.key, (key_part.rstrip() + comment,)
                     + _block_list_like(old.lines[1:], items), old.syntax)
    return entry(old.key, items, syntax=old.syntax, bare=bare)


def _valid(written: Entry, value, bare: bool) -> Entry:
    """``written``, a TOML entry, when TOML reads it; else the value as
    entry() writes it, which TOML always does."""
    try:
        tomllib.loads("\n".join(written.lines))
    except tomllib.TOMLDecodeError:
        return entry(written.key, value, syntax=TOML, bare=bare)
    return written


def _flow_list(written: str, more: Sequence[str]) -> Optional[Tuple[str, str, Tuple[str, ...]]]:
    """A flow list ("[a, b]"), on its line or continued on the lines
    ``more``: its text on one line, what follows it as comments (the
    comment after its "]", then those inside it, each kept), and the
    comment lines after it. Read as YAML
    reads it: a "#" starts a comment only after a space or at a line's
    start, and never inside quotes; the list ends at the "]" that closes
    it, not one in a comment or a text. None when it is not closed there,
    or something other than a comment follows it."""
    content: List[str] = []
    inside: List[str] = []
    after: List[str] = []
    depth, quote, tail, closed = 0, "", "", False
    for row in [written, *more]:
        if closed:
            if row.strip() and not row.strip().startswith("#"):
                return None                 # the entry goes on after the list
            after.append(row)               # a comment line after it, or blank
            continue
        out, at = [], 0
        while at < len(row):
            char = row[at]
            if quote:
                taken, quote = _in_quote(quote, row, at)
                out.append(taken)
                at += len(taken)
                continue
            if char == "#" and (at == 0 or row[at - 1] in " \t"):
                inside.append(row[at:].strip())
                break
            out.append(char)
            if char in "\"'" and _at_item_start(out[:-1], content):
                quote = char
            elif char in "[{":
                depth += 1
                if depth > 1:
                    return None             # a list or a mapping in it: unread
            elif char in "]}":
                depth -= 1
                if depth == 0:
                    tail = row[at + 1:]
                    if tail.strip() and not re.match(r"\s+#", tail):
                        return None
                    closed = True
                    break
            at += 1
        content.append("".join(out).strip())
    if not closed or quote:
        return None
    return (" ".join(part for part in content if part),
            tail.rstrip() + "".join("  " + comment for comment in inside), tuple(after))


def _at_item_start(line: Sequence[str], before: Sequence[str]) -> bool:
    """Whether what is read of a flow list so far (``before``, its earlier
    lines, then ``line``) ends where an item starts: after "[", "{", ","
    or ":" and blanks. Only there does a quote open an item."""
    for text in ("".join(line), *reversed(before)):
        text = text.rstrip()
        if text:
            return text[-1] in "[{,:"
    return True


def _key_and_rest(old: Entry) -> Tuple[Optional[str], str]:
    """The key's part of its first line, with the spaces after ":" or "="
    as written, and the rest of the line."""
    pattern = r"^(\s*[^=]+?\s*=[ \t]*)(.*)$" if old.syntax == TOML else \
        r"^([^\s:][^:]*?[ \t]*:[ \t]*)(.*)$"
    found = re.match(pattern, old.lines[0])
    return (found.group(1), found.group(2)) if found else (None, "")


def _value_and_comment(rest: str) -> Tuple[str, str]:
    """The value as written and what follows it (spaces and a comment)."""
    if rest.startswith("#"):
        return "", " " + rest               # no value yet, a comment only
    quoted = _QUOTED.match(rest)
    flow = _flow_list(rest, ()) if rest.startswith("[") else None
    if quoted is not None:
        end = quoted.end()
    elif flow is not None:
        return flow[0], flow[1]
    else:
        found = re.search(r"\s+#", rest)
        end = found.start() if found else len(rest.rstrip())
    tail = rest[end:]
    if tail.strip() and not re.match(r"\s+#", tail):
        return rest, ""
    return rest[:end], tail


def _like(written: str, value: str, syntax: str, bare: bool) -> str:
    """``value`` quoted the way ``written`` was: in single quotes, in
    double quotes, or bare when it can be."""
    if bare:
        return value
    if written.startswith("'") and "\n" not in value and (syntax == YAML or "'" not in value):
        return "'" + (value.replace("'", "''") if syntax == YAML else value) + "'"
    if syntax == TOML or written.startswith('"'):
        return json.dumps(value, ensure_ascii=False)
    return _quoted(value)


def _inline_list_like(written: str, items: Sequence[str], syntax: str) -> str:
    """An inline list of ``items``, written as ``written`` was: each item
    that stays as it was written, the separator and the padding inside the
    brackets as they were, a new item quoted like the first one."""
    inside = written[1:written.rfind("]")] if "]" in written else written[1:]
    tokens = [token.strip() for token in _split_inline(inside) if token.strip()]
    kept = {}
    for token in tokens:
        kept.setdefault(_unquote(token), token)
    first = tokens[0] if tokens else ""
    parts = []
    for item in items:
        if item in kept:
            parts.append(kept.pop(item))
        elif syntax == TOML or first.startswith(("'", '"')):
            parts.append(_like(first, item, syntax, False) if first.startswith("'")
                         else json.dumps(item, ensure_ascii=False))
        else:
            parts.append(_quoted(item, in_list=True))
    separator = "," if inside and ", " not in inside and "," in inside else ", "
    pad = " " if inside.startswith(" ") else ""
    return "[" + pad + separator.join(parts) + (pad if inside.endswith(" ") else "") + "]"


def _block_list_like(lines: Sequence[str], items: Sequence[str]) -> Tuple[str, ...]:
    """A block list of ``items`` as ``lines`` wrote one: each item that stays
    on its own line as written, a new one indented like the first, any
    other line (a comment) kept after them."""
    item_lines = [line for line in lines if re.match(r"^\s*-(\s|$)", line)]
    others = [line for line in lines if line not in item_lines]
    lead = re.match(r"^(\s*-\s*)", item_lines[0]).group(1) if item_lines else "  - "
    kept = {}
    for line in item_lines:
        kept.setdefault(_list_item(line), line)
    return tuple(kept.pop(item) if item in kept else lead + _quoted(item) for item in items) \
        + tuple(others)


# One item on a line of a TOML array over several lines: its indent, the
# item ("..." or '...'), a comma after it or not, and a comment.
_TOML_ITEM_LINE = re.compile(
    r"""^(\s*)("(?:[^"\\]|\\.)*"|'[^']*')(\s*,)?(\s*#.*)?$""")


def _multi_line_list_like(lines: Sequence[str],
                          items: Sequence[str]) -> Optional[Tuple[str, ...]]:
    """A TOML array over lines, one item a line, as ``lines`` wrote it:
    each item that stays keeps its line and comment, a new one is indented
    and quoted like the first, and every item but the last has its comma
    (the last has one when the file's last item had one). None when the
    lines are laid out otherwise (two items on a line, leading commas,
    comment lines): the array is then written on one line."""
    parsed = []
    for line in lines:
        if not line.strip():
            continue
        found = _TOML_ITEM_LINE.match(line)
        if found is None:
            return None
        parsed.append(found.groups())
    if not parsed:
        return None
    indent, first, _comma, _comment = parsed[0]
    trailing = bool(parsed[-1][2])
    kept = {}
    for row in parsed:
        kept.setdefault(_unquote(row[1]), row)
    rows = []
    for item in items:
        if item in kept:
            rows.append(kept.pop(item))
        else:
            token = _like(first, item, TOML, False) if first.startswith("'") \
                else json.dumps(item, ensure_ascii=False)
            rows.append((indent, token, None, None))
    written = []
    for at, (row_indent, token, comma, comment) in enumerate(rows):
        wants = at < len(rows) - 1 or trailing
        written.append(row_indent + token + ((comma or ",") if wants else "")
                       + (comment or ""))
    return tuple(written)


def _block_lines(lines: Sequence[str], value: str) -> Tuple[str, ...]:
    """``value`` as the lines of a block text, indented as ``lines`` were."""
    indents = [len(line) - len(line.lstrip(" ")) for line in lines if line.strip()]
    indent = " " * (min(indents) if indents else 2)
    return tuple(indent + row if row else "" for row in value.split("\n"))


def _quoted(value: str, *, in_list: bool = False) -> str:
    """``value`` as YAML reads it back as the same text: bare when it can
    be, else in double quotes."""
    plain = (_PLAIN.match(value) is not None and value == value.strip()
             and ": " not in value and " #" not in value and not value.endswith(":")
             and value.lower() not in _YAML_WORDS and not _YAML_NUMBER.match(value)
             and not _YAML_DATE.match(value)
             and not (in_list and any(char in value for char in ",[]{}")))
    return value if plain else json.dumps(value, ensure_ascii=False)


def _list_item(line: str) -> str:
    """The text of a block list's item line ("  - \"#idea\"   # a note")."""
    return _unquote(_without_comment(line.strip()[1:].strip()))


def _block_text(lines: Sequence[str], style: str, chomping: str) -> str:
    """The text of a block scalar: its indented lines, as they are for "|",
    folded for ">" as YAML folds them."""
    body = list(lines)
    indents = [len(line) - len(line.lstrip(" ")) for line in body if line.strip()]
    cut = min(indents) if indents else 0
    rows = [line[cut:] if line.strip() else "" for line in body]
    while rows and not rows[-1]:
        rows.pop()
    if style == "|":
        text = "\n".join(rows)
    else:
        # Folded: lines next to each other joined by a space, n empty lines
        # between two read as n line breaks.
        text, empty = "", 0
        for row in rows:
            if not row:
                empty += 1
                continue
            if text:
                text += "\n" * empty if empty else " "
            text += row.strip()
            empty = 0
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
    if written.startswith("#"):
        return ""                           # no value, a comment only
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


def split_items(written: str, quotes: str = "\"'") -> List[str]:
    """The items of a comma-separated text, each as its text: one in
    quotes (of ``quotes``) keeps its commas ('a, "b, c"' is two items)."""
    return [_unquote(part) for part in _split_inline(written, quotes) if part.strip()]


def _split_inline(inside: str, quotes: str = "\"'") -> List[str]:
    """The items of an inline list, commas inside quotes kept. A quote
    opens only as an item's first character, as YAML and TOML read it: the
    apostrophe of "don't" is a letter."""
    items, current, quote, at = [], "", "", 0
    while at < len(inside):
        char = inside[at]
        if quote:
            taken, quote = _in_quote(quote, inside, at)
            current += taken
            at += len(taken)
            continue
        if char in quotes and not current.strip():
            quote = char
            current += char
        elif char == ",":
            items.append(current)
            current = ""
        else:
            current += char
        at += 1
    items.append(current)
    return items


def _in_quote(quote: str, text: str, at: int) -> Tuple[str, str]:
    """Inside a quoted text opened with ``quote``, what is read at ``at`` of
    ``text``, and the quote still open after it ("" once it closes): an
    escaped character in "..." ("\\""), and '' in '...', are read whole and
    close nothing. Every reader of quoted items reads them with this."""
    char = text[at]
    if quote == '"' and char == "\\":
        return text[at:at + 2], quote
    if char == quote and quote == "'" and text[at + 1:at + 2] == "'":
        return "''", quote
    return char, "" if char == quote else quote
