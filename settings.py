#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later

import os

from atomic_file import read_json, write_json

_SETTINGS_FILE = os.path.expanduser("~/.config/my_editor/settings.json")


def load_settings() -> dict:
    """Return the stored settings dict (tolerates a missing, unreadable
    or corrupt file)."""
    return read_json(_SETTINGS_FILE, dict)


def save_setting(key: str, value) -> None:
    """Set a single setting, preserving the rest of the stored settings."""
    settings = load_settings()
    settings[key] = value
    save_settings(settings)


def save_settings(settings: dict) -> bool:
    """Overwrite the settings file with the given dict, in one step.
    False when it could not be written; the old settings are kept."""
    return write_json(_SETTINGS_FILE, settings, indent=2)
