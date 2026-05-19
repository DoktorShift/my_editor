"""PluginSettingsStore: round-trip, scoping, atomic writes."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from plugin_system.settings import PluginSettingsScope, PluginSettingsStore


@pytest.fixture
def store(tmp_path) -> PluginSettingsStore:
    return PluginSettingsStore(path=tmp_path / "settings.json")


def test_empty_store_returns_default(store):
    assert store.get("nonexistent", "key", default="fallback") == "fallback"


def test_set_then_get_round_trips(store):
    store.set("plugin_a", "greeting", "Hello")
    assert store.get("plugin_a", "greeting") == "Hello"


def test_two_plugins_dont_collide(store):
    """Two plugins writing the same key see only their own value."""
    store.set("plugin_a", "shared", "from-a")
    store.set("plugin_b", "shared", "from-b")
    assert store.get("plugin_a", "shared") == "from-a"
    assert store.get("plugin_b", "shared") == "from-b"


def test_all_for_returns_namespace_snapshot(store):
    store.set("plugin_a", "x", 1)
    store.set("plugin_a", "y", 2)
    store.set("plugin_b", "x", "other")
    assert store.all_for("plugin_a") == {"x": 1, "y": 2}


def test_delete_removes_key(store):
    store.set("plugin_a", "k", "v")
    assert store.delete("plugin_a", "k") is True
    assert store.get("plugin_a", "k") is None


def test_delete_unknown_is_noop(store):
    assert store.delete("plugin_a", "missing") is False


def test_delete_cleans_up_empty_section(store, tmp_path):
    store.set("plugin_a", "x", 1)
    store.delete("plugin_a", "x")
    data = json.loads((tmp_path / "settings.json").read_text())
    assert "plugin_a" not in data  # empty section pruned


def test_clear_plugin_drops_section(store):
    store.set("plugin_a", "x", 1)
    store.set("plugin_a", "y", 2)
    store.set("plugin_b", "z", 3)
    store.clear_plugin("plugin_a")
    assert store.all_for("plugin_a") == {}
    assert store.all_for("plugin_b") == {"z": 3}


def test_file_is_chmod_600(store, tmp_path):
    """Settings can hold tokens / wallet URIs; the file must not be
    world-readable on POSIX. Windows ignores chmod bits — skipped there."""
    if os.name != "posix":
        pytest.skip("POSIX file modes don't apply on Windows")
    store.set("plugin_a", "secret", "shhh")
    mode = (tmp_path / "settings.json").stat().st_mode & 0o777
    assert mode == 0o600, f"settings file should be chmod 600, got {oct(mode)}"


def test_atomic_write_does_not_leave_tmpfiles(store, tmp_path):
    """Atomic write uses a temp file in the same dir; after success
    no .json.tmp leftovers should remain."""
    store.set("plugin_a", "k", "v")
    leftovers = list(tmp_path.glob(".plugin_settings_*.json.tmp"))
    assert leftovers == [], f"leftover temp files: {leftovers}"


def test_corrupt_file_is_treated_as_empty(store, tmp_path):
    """A malformed JSON file shouldn't crash the editor. The next
    write fixes the file."""
    (tmp_path / "settings.json").write_text("{not valid json")
    assert store.get("plugin_a", "anything") is None
    store.set("plugin_a", "key", "value")
    # The next read succeeds and reflects the new write.
    assert store.get("plugin_a", "key") == "value"


def test_non_object_root_is_treated_as_empty(store, tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps(["not", "an", "object"]))
    assert store.get("plugin_a", "key") is None


def test_scope_facade_isolates_plugins(store):
    a_scope = store.scope("plugin_a")
    b_scope = store.scope("plugin_b")
    a_scope.set("shared", "from-a")
    b_scope.set("shared", "from-b")
    assert a_scope.get("shared") == "from-a"
    assert b_scope.get("shared") == "from-b"


def test_scope_delete_and_all(store):
    s = store.scope("plugin_a")
    s.set("x", 1)
    s.set("y", 2)
    assert s.all() == {"x": 1, "y": 2}
    s.delete("x")
    assert s.all() == {"y": 2}


def test_complex_value_types_round_trip(store):
    """Nested lists/dicts and unicode strings survive the JSON
    round trip."""
    payload = {
        "nested": {"a": [1, 2, 3], "b": "café"},
        "bools": [True, False, None],
    }
    store.set("plugin_a", "complex", payload)
    assert store.get("plugin_a", "complex") == payload


def test_set_rejects_non_json_serializable_with_clear_error(store, tmp_path):
    """A plugin that stuffs a Path or bytes into settings should get a
    clear error pointing at the offending key, not a deep json.dumps
    error from inside our internals."""
    with pytest.raises(TypeError) as excinfo:
        store.set("plugin_a", "bad", tmp_path)  # Path object isn't JSON
    msg = str(excinfo.value)
    assert "plugin_a" in msg
    assert "bad" in msg
