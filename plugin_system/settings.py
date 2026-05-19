"""Per-plugin scoped settings, one JSON file shared by all plugins.

Each plugin sees a private key-value store. Two plugins writing the
same key never collide because the on-disk file partitions everything
by ``plugin_id``:

    {
      "hello_world":   {"greeting": "Hello", "count": 5},
      "rss_importer":  {"interval_min": 30, "last_run": 1700000000}
    }

Writes are atomic (temp file + rename in the same directory) so a
crash mid-write never corrupts the store. The file is ``chmod 600``
because a plugin may use it to remember tokens, API keys, or wallet
URIs the user wouldn't want world-readable.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .paths import plugin_settings_path


class PluginSettingsStore:
    """JSON-backed key-value store. Each plugin gets a scoped view via
    ``scope(plugin_id)``.

    The store reloads the file on every read and rewrites it on every
    write. This trades a small amount of disk I/O for a trivially
    correct concurrency story: two plugins racing to set the same
    setting can never lose each other's other keys.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or plugin_settings_path()

    # -- read --------------------------------------------------------------

    def _load_all(self) -> Dict[str, Dict[str, Any]]:
        if not self._path.is_file():
            return {}
        try:
            with self._path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            # A corrupt file shouldn't take down the editor; treat as
            # empty and let the next write fix it.
            return {}
        if not isinstance(data, dict):
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for plugin_id, section in data.items():
            if isinstance(plugin_id, str) and isinstance(section, dict):
                out[plugin_id] = section
        return out

    def get(self, plugin_id: str, key: str, default: Any = None) -> Any:
        return self._load_all().get(plugin_id, {}).get(key, default)

    def all_for(self, plugin_id: str) -> Dict[str, Any]:
        return dict(self._load_all().get(plugin_id, {}))

    # -- write -------------------------------------------------------------

    def set(self, plugin_id: str, key: str, value: Any) -> None:
        # Validate the value JSON-encodes before mutating the in-memory
        # snapshot. A plugin author who stuffs a Path or bytes into
        # settings gets a clear TypeError pointing at their key instead
        # of a generic json.dumps error from deep inside our save path.
        try:
            json.dumps(value)
        except TypeError as exc:
            raise TypeError(
                f"plugin settings value for {plugin_id!r}/{key!r} is not "
                f"JSON-serializable: {exc}"
            ) from exc
        data = self._load_all()
        section = data.setdefault(plugin_id, {})
        section[key] = value
        self._save_all(data)

    def delete(self, plugin_id: str, key: str) -> bool:
        data = self._load_all()
        section = data.get(plugin_id, {})
        if key not in section:
            return False
        del section[key]
        if not section:
            # Clean up empty sections so the file doesn't accumulate
            # noise from uninstalled plugins.
            data.pop(plugin_id, None)
        self._save_all(data)
        return True

    def clear_plugin(self, plugin_id: str) -> None:
        """Drop every setting belonging to ``plugin_id`` (used at uninstall)."""
        data = self._load_all()
        if plugin_id in data:
            data.pop(plugin_id)
            self._save_all(data)

    # -- internals ---------------------------------------------------------

    def _save_all(self, data: Dict[str, Dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: tmp file in the same dir + rename. Same dir is
        # important because rename across filesystems is not atomic.
        fd, tmp_path = tempfile.mkstemp(
            prefix=".plugin_settings_",
            suffix=".json.tmp",
            dir=str(self._path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                # Best-effort on platforms whose chmod doesn't fully
                # honour POSIX bits (Windows). Not a fatal error.
                pass
            os.replace(tmp_path, self._path)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # -- scoped facade -----------------------------------------------------

    def scope(self, plugin_id: str) -> "PluginSettingsScope":
        """Return a per-plugin view that the PluginAPI hands to a plugin."""
        return PluginSettingsScope(self, plugin_id)


class PluginSettingsScope:
    """One plugin's view of the shared store. Pure facade - no state
    of its own beyond the plugin id, so re-creating it is free."""

    def __init__(self, store: PluginSettingsStore, plugin_id: str) -> None:
        self._store = store
        self._plugin_id = plugin_id

    def get(self, key: str, default: Any = None) -> Any:
        return self._store.get(self._plugin_id, key, default)

    def set(self, key: str, value: Any) -> None:
        self._store.set(self._plugin_id, key, value)

    def delete(self, key: str) -> bool:
        return self._store.delete(self._plugin_id, key)

    def all(self) -> Dict[str, Any]:
        return self._store.all_for(self._plugin_id)
