# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Spell checking with each system's own spell checker.

``backends`` reaches the system checker (macOS, Windows, Enchant on
Linux, or none) behind one interface that never raises. See
docs/spelling.md for how the pieces fit and how the editor uses them.
"""

from .backends import (
    AUTOMATIC,
    NullBackend,
    SpellBackend,
    TextCheck,
    best_language,
    create_backend,
    normalize_language,
)

__all__ = [
    "AUTOMATIC",
    "NullBackend",
    "SpellBackend",
    "TextCheck",
    "best_language",
    "create_backend",
    "normalize_language",
]
