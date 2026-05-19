"""Cross-platform plugin path resolution."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from plugin_system import paths as plugin_paths
from plugin_system.paths import (
    APP_FOLDER,
    bundled_plugins_dir,
    plugin_settings_path,
    user_config_root,
    user_plugins_dir,
)


def test_bundled_dir_is_repo_local():
    """The bundled folder ships with the editor source — it should
    live next to the editor's other modules, not in the user's home."""
    d = bundled_plugins_dir()
    assert d.name == "bundled_plugins"
    # The hello-world reference plugin lives here.
    assert (d / "hello_world" / "manifest.json").is_file()


def test_user_root_is_created(tmp_path, monkeypatch):
    """First call should create the per-platform config root."""
    # Pretend we're on Linux with a custom XDG home; the simplest path.
    monkeypatch.setattr(plugin_paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    root = user_config_root()
    assert root == tmp_path / APP_FOLDER
    assert root.is_dir()


def test_user_plugins_dir_under_user_root(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    p = user_plugins_dir()
    assert p == tmp_path / APP_FOLDER / "plugins"
    assert p.is_dir()


def test_plugin_settings_path_under_user_root(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    p = plugin_settings_path()
    assert p.parent == tmp_path / APP_FOLDER
    assert p.name == "plugin_settings.json"


def test_macos_uses_application_support(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_paths.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = user_config_root()
    assert root == tmp_path / "Library" / "Application Support" / APP_FOLDER


def test_windows_uses_appdata(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    root = user_config_root()
    assert root == tmp_path / APP_FOLDER


def test_windows_appdata_fallback(tmp_path, monkeypatch):
    """If APPDATA is unset, we fall back to ``~/AppData/Roaming``."""
    monkeypatch.setattr(plugin_paths.sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = user_config_root()
    assert root == tmp_path / "AppData" / "Roaming" / APP_FOLDER


def test_linux_default_falls_back_to_dot_config(tmp_path, monkeypatch):
    """No XDG_CONFIG_HOME: use ``~/.config`` to match the existing
    profile-store convention."""
    monkeypatch.setattr(plugin_paths.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = user_config_root()
    assert root == tmp_path / ".config" / APP_FOLDER
