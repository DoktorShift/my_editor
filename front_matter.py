# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Markdown file's front matter: the ``---`` block at its top that Jekyll,
Hugo, Obsidian and iA Writer keep a document's details in.

Read as entries, each its key and the lines written for it (the key's line
and the indented lines under it), so that an entry this app does not know
is written back exactly as it was. Values are read the way front matter in
the wild writes them, not as full YAML: a plain or quoted text, a list in
brackets (``[a, b]``) or a list of "- item" lines. Values this app writes
are quoted whenever YAML would read them as something else.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, Union

# The block: "---" on the first line (after a byte order mark), then
# anything, then "---" on a line of its own.
_BLOCK = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)(?:\r?\n)?^---[ \t]*(?:\r?\n|\Z)",
                    re.DOTALL | re.MULTILINE)
_KEY = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*)[ \t]*:(?:[ \t]+(.*?))?[ \t]*$")
# A plain value YAML would read as text again, as written.
_PLAIN = re.compile(r"^[A-Za-z0-9À-￿][^:#\n]*$")
_YAML_WORDS = {"true", "false", "yes", "no", "on", "off", "null", "~"}


@dataclass(frozen=True)
class Entry:
    """One key of the front matter, with the lines written for it."""

    key: str
    lines: Tuple[str, ...]

    @property
    def value(self) -> Union[str, List[str]]:
        """The value as text, or as a list of texts for a list."""
        first = _KEY.match(self.lines[0])
        written = (first.group(2) or "") if first else ""
        if written.startswith("[") and written.endswith("]"):
            return [_unquote(part) for part in _split_inline(written[1:-1]) if part.strip()]
        items = [line.strip()[1:].strip() for line in self.lines[1:]
                 if line.strip().startswith("-")]
        if not written and items:
            return [_unquote(item) for item in items]
        return _unquote(written)


def split(text: str) -> Tuple[Optional[List[Entry]], str]:
    """``(entries, body)`` of ``text``: its front matter and what follows
    it, or ``(None, text)`` when it has none."""
    found = _BLOCK.match(text or "")
    if found is None:
        return None, text or ""
    entries: List[Entry] = []
    for line in found.group(1).splitlines():
        key = _KEY.match(line)
        if key is not None and not line[:1].isspace():
            entries.append(Entry(key.group(1), (line,)))
        elif entries:
            entries[-1] = Entry(entries[-1].key, entries[-1].lines + (line,))
        elif line.strip():
            # Lines before any key (a comment): kept with an empty key.
            entries.append(Entry("", (line,)))
    return entries, text[found.end():]


def join(entries: Sequence[Entry], body: str) -> str:
    """``body`` with ``entries`` as its front matter (none when empty)."""
    if not entries:
        return body
    lines = [line for entry in entries for line in entry.lines]
    return "---\n" + "\n".join(lines) + "\n---\n\n" + body.lstrip("\n")


def entry(key: str, value: Union[str, Sequence[str]]) -> Entry:
    """An entry for ``key`` written the way this app writes one: a text,
    quoted when it has to be, or a list in brackets."""
    if isinstance(value, str):
        return Entry(key, (f"{key}: {_quoted(value)}",))
    return Entry(key, (f"{key}: [" + ", ".join(_quoted(item) for item in value) + "]",))


def _quoted(value: str) -> str:
    if _PLAIN.match(value) and value.lower() not in _YAML_WORDS \
            and not _looks_numeric(value) and value == value.strip():
        return value
    return json.dumps(value, ensure_ascii=False)


def _looks_numeric(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


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
