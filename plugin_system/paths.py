"""Cross-platform plugin directory layout.

Two locations, both scanned at startup by the loader:

  - ``bundled_plugins/`` lives next to the editor's source and ships
    with the install. The repository owns it. Things like the future
    "Hello World" reference plugin and the bundled NWC plugin live
    here.
  - ``user_plugins_dir()`` lives under the user's per-platform config
    root. The marketplace installer writes new plugins here. The
    editor must not write to ``bundled_plugins/`` at runtime.

Where the user directory lives, per platform:

  - macOS   : ``~/Library/Application Support/my_editor/plugins/``
  - Windows : ``%APPDATA%/my_editor/plugins/``
  - Linux   : ``$XDG_CONFIG_HOME/my_editor/plugins/`` (falling back to
               ``~/.config/my_editor/plugins/``)

The choice on Linux deliberately matches the existing
``nostr/profiles.py`` convention (``~/.config/my_editor/...``) so
both halves of the editor share a single per-user config tree.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


# Single source of truth for the config-folder name used by both halves
# of the editor (nostr profile store + plugin system). Bumping this is a
# breaking change for existing installs.
APP_FOLDER: str = "my_editor"


def bundled_plugins_dir() -> Path:
    """Return the read-only ``bundled_plugins`` folder shipped with the editor.

    The folder may not exist (e.g. an install with no bundled plugins);
    callers should treat that as "no bundled plugins to load".
    """
    return Path(__file__).resolve().parent.parent / "bundled_plugins"


def user_config_root() -> Path:
    """Return the editor's per-platform user-config root.

    The directory is created on first call so callers can drop a file
    in immediately without re-running ``mkdir`` themselves.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_FOLDER
    elif sys.platform == "win32":
        # ``APPDATA`` is normally set on Windows but we defend against
        # bare-bones environments (CI, embedded shells) by falling back
        # to the documented path.
        appdata = os.environ.get("APPDATA") or str(
            Path.home() / "AppData" / "Roaming"
        )
        base = Path(appdata) / APP_FOLDER
    else:
        # XDG-conforming on Linux + any other Unix. The fallback matches
        # the existing ``nostr/profiles.py`` convention exactly.
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg:
            base = Path(xdg) / APP_FOLDER
        else:
            base = Path.home() / ".config" / APP_FOLDER
    base.mkdir(parents=True, exist_ok=True)
    return base


def user_plugins_dir() -> Path:
    """Return the user-writable plugin install root, creating it if needed."""
    p = user_config_root() / "plugins"
    p.mkdir(parents=True, exist_ok=True)
    return p


def plugin_settings_path() -> Path:
    """Return the path to the shared plugin-settings JSON file.

    All plugins' settings are stored in one file under per-plugin
    namespacing (handled by ``PluginSettingsStore``). One file is
    simpler to back up and atomically write than per-plugin files.
    """
    return user_config_root() / "plugin_settings.json"


def plugin_log_path() -> Path:
    """Return the path to the plugin diagnostic log.

    Plugin load failures, register() exceptions, and runtime errors
    in plugin-supplied callbacks are appended here. The marketplace
    UI's "View log" button reads this file. Bundled GUI users
    (.app on macOS, .exe on Windows) have no stderr they can read,
    so a real file is the only way to surface plugin diagnostics.
    """
    return user_config_root() / "plugin.log"
