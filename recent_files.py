#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later

import os

from atomic_file import read_json, write_json

_RECENT_FILE = os.path.expanduser("~/.cache/my_editor/recent_files.json")
_MAX_ENTRIES = 10


def load_recent() -> list[str]:
    """Return the stored list of recent file paths (all entries, including missing files)."""
    return [p for p in read_json(_RECENT_FILE, list) if isinstance(p, str)]


def add_recent(path: str) -> None:
    """Add path to the top of the recent list, removing duplicates, capped at _MAX_ENTRIES."""
    entries = load_recent()
    if path in entries:
        entries.remove(path)
    entries.insert(0, path)
    _save(entries[:_MAX_ENTRIES])


def clear_recent() -> None:
    _save([])


def _save(entries: list[str]) -> None:
    write_json(_RECENT_FILE, entries, indent=2)
