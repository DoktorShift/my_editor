"""Shared atomic JSON read/write helpers for Telegram persistence.

Same shape used by ``nostr/blossom/settings.py``: write to a temp file
in the same directory, fsync, then ``os.replace`` onto the target.
``os.replace`` is atomic on POSIX and on Windows (NTFS/ReFS) per the
Python docs, so a crash during write cannot leave a half-written
file at the canonical path.

The directory is created with ``0o700`` and the file with ``0o600``
so a token file isn't world-readable on multi-user systems. Windows
silently ignores the mode bits; that is acceptable for the threat
model (the dir is under the user's profile already).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


SETTINGS_DIR: Path = Path.home() / ".config" / "my_editor"


def ensure_dir() -> Path:
    """Create the settings directory if needed and return it."""
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(SETTINGS_DIR, 0o700)
    except OSError:
        pass
    return SETTINGS_DIR


def load_json(path: Path) -> Any:
    """Return the parsed JSON at ``path`` or ``None`` if absent/corrupt.

    Callers are expected to validate the shape themselves; this helper
    only protects against missing files and malformed JSON.
    """
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save_json(path: Path, payload: Any) -> None:
    """Atomically replace ``path`` with the JSON of ``payload``."""
    ensure_dir()
    fd, tmp = tempfile.mkstemp(
        prefix="." + path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
