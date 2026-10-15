# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Edit > Start Dictation…: one command on every system, the way to dictate
chosen for the person.

- SYSTEM: the system's own dictation (system.py). On a Mac it is the only
  way: it works on the computer already (Apple silicon). On Windows it is
  the first way, Voice typing, which needs Microsoft's online speech
  recognition.
- ON_DEVICE: dictation on this computer (on_device.py), offline, with a
  speech model the person downloads once (models.py). The only way on
  Linux, which has no dictation of its own; on Windows the other way,
  chosen in Settings > Dictation for no internet or for privacy. Its
  engine ships with the Windows and Linux builds alone.
"""

from __future__ import annotations

import sys
from typing import Tuple

from .system import available as system_available, start as start_system

__all__ = ["SYSTEM", "ON_DEVICE", "chosen_way", "on_device_supported", "start_system",
           "system_available", "ways"]

SYSTEM, ON_DEVICE = "system", "on_device"


def on_device_supported() -> bool:
    """Whether this build has the engine for dictation on this computer."""
    from importlib.util import find_spec
    return find_spec("sherpa_onnx") is not None


def ways(platform: str = sys.platform, *, on_device: bool = None) -> Tuple[str, ...]:
    """The ways to dictate here, the default first."""
    found = (SYSTEM,) if system_available(platform) else ()
    if platform != "darwin" and (on_device_supported() if on_device is None else on_device):
        found += (ON_DEVICE,)
    return found


def chosen_way(setting: str, platform: str = sys.platform, *, on_device: bool = None) -> str:
    """The way to dictate: the one chosen when there is such a way here,
    else the default; "" when there is none."""
    offered = ways(platform, on_device=on_device)
    if setting in offered:
        return setting
    return offered[0] if offered else ""
