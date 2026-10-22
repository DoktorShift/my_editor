# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where the files bundled with the app are, from source and when frozen.

A module of its own, with nothing to set up when it is imported: main.py
chooses the language as it is imported, so a check that only needs a
bundled file (self_test.py) must not import main for it."""

import os
import sys


def resource_path(rel_path: str) -> str:
    """Resolve a bundled resource, working both from source and when frozen.

    PyInstaller unpacks bundled data under sys._MEIPASS; from source we resolve
    relative to this file (the repo root). The spec bundles assets preserving
    their repo-relative path, so the same rel_path works in both cases."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel_path)
