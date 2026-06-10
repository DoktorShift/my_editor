"""Per-bot cache of Telegram file_ids for uploaded media.

After ``sendPhoto`` / ``sendDocument`` succeeds, Telegram returns a
``file_id`` that the same bot can reuse on subsequent sends with zero
upload bandwidth. The cache key is ``(local sha256, bot id)`` because
file_ids are bot-scoped per Telegram's contract: they cannot be
shared between bots.

Cache invalidation is automatic - the API wrapper evicts the entry on
``StaleFileId`` and re-uploads. The local store is purely additive;
the worst-case cost of corruption is one extra upload.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Dict, Optional

from ._atomic import SETTINGS_DIR, load_json, save_json


FILE_IDS_FILE: Path = SETTINGS_DIR / "telegram_file_ids.json"

SCHEMA_VERSION: int = 1

# A single file is bounded by Telegram's 50 MB document limit, but we
# stream the hash in chunks so memory usage stays flat regardless.
_HASH_CHUNK: int = 64 * 1024


def sha256_file(path: Path) -> str:
    """Return the hex sha256 of ``path``, streamed in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class FileIdCache:
    """``(sha256, bot_id, kind) -> file_id`` map with atomic JSON save."""

    def __init__(self, path: Path = FILE_IDS_FILE) -> None:
        self._path = path
        # Keys are flat ``"{sha}|{bot_id}|{kind}"`` strings for cheap
        # JSON round-tripping.
        self._entries: Dict[str, dict] = {}
        self._load()

    def get(self, sha256: str, bot_id: str, kind: str) -> Optional[str]:
        """Return a cached file_id or ``None`` for a cache miss."""
        entry = self._entries.get(_key(sha256, bot_id, kind))
        return entry["file_id"] if entry else None

    def put(self, sha256: str, bot_id: str, kind: str, file_id: str) -> None:
        self._entries[_key(sha256, bot_id, kind)] = {
            "file_id": file_id,
            "stored_at": int(time.time()),
        }
        self._save()

    def evict(self, sha256: str, bot_id: str, kind: str) -> None:
        if self._entries.pop(_key(sha256, bot_id, kind), None) is not None:
            self._save()

    def evict_bot(self, bot_id: str) -> None:
        """Remove every cached id belonging to ``bot_id``."""
        marker = f"|{bot_id}|"
        before = len(self._entries)
        self._entries = {k: v for k, v in self._entries.items() if marker not in k}
        if len(self._entries) != before:
            self._save()

    # -- internals -- #

    def _load(self) -> None:
        data = load_json(self._path)
        if not isinstance(data, dict):
            return
        if data.get("version") != SCHEMA_VERSION:
            return
        entries = data.get("entries")
        if isinstance(entries, dict):
            for k, v in entries.items():
                if isinstance(v, dict) and isinstance(v.get("file_id"), str):
                    self._entries[k] = {
                        "file_id": v["file_id"],
                        "stored_at": int(v.get("stored_at") or 0),
                    }

    def _save(self) -> None:
        save_json(
            self._path,
            {"version": SCHEMA_VERSION, "entries": self._entries},
        )


def _key(sha256: str, bot_id: str, kind: str) -> str:
    return f"{sha256}|{bot_id}|{kind}"
