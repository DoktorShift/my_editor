# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Edit > Start Dictation…: the system's own dictation, into the text with
the focus.

- macOS: Dictation, which Fn pressed twice starts too. Started as the Edit
  menu's Start Dictation… of every Mac app starts it, with the
  ``startDictation:`` action. Qt turns off the item macOS would put in the
  Edit menu by itself, so the app offers its own.
- Windows 10 and 11: Voice typing, which Windows+H opens. Windows has no
  call to open it, so the app presses those keys, as the person would.
- Linux: GNOME and KDE have no dictation of their own; it is not offered.

The words come in as typed text through the system's input method, which
the editor takes like any other (HtmlEditor.inputMethodEvent). Nothing is
recorded or sent anywhere by the app itself.
"""

from __future__ import annotations

import sys

# The systems that have dictation of their own.
SYSTEMS = ("darwin", "win32")


def available(platform: str = sys.platform) -> bool:
    """Whether this system has dictation the app can start."""
    return platform in SYSTEMS


def start(platform: str = sys.platform) -> bool:
    """Start the system's dictation; False when it could not be."""
    if platform == "darwin":
        return _start_mac()
    if platform == "win32":
        return _start_windows()
    return False


def _start_mac() -> bool:
    try:
        from rubicon.objc import SEL, ObjCClass
        from rubicon.objc.runtime import load_library
    except ImportError:
        return False
    load_library("AppKit")
    app = ObjCClass("NSApplication").sharedApplication
    # To the first responder, as a menu item with no target sends it: the
    # text with the focus.
    return bool(app.sendAction_to_from_(SEL("startDictation:"), None, None))


def _start_windows() -> bool:
    import ctypes
    from ctypes import wintypes

    keyboard_input, key_up = 1, 0x0002
    windows_key, h_key = 0x5B, 0x48

    class KeyboardInput(ctypes.Structure):
        _fields_ = (("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t))

    class MouseInput(ctypes.Structure):
        # Only for the size of the union: INPUT is as large as its largest.
        _fields_ = (("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t))

    class Event(ctypes.Union):
        _fields_ = (("ki", KeyboardInput), ("mi", MouseInput))

    class Input(ctypes.Structure):
        _fields_ = (("type", wintypes.DWORD), ("event", Event))

    def key(code: int, flags: int = 0) -> Input:
        return Input(keyboard_input, Event(ki=KeyboardInput(code, 0, flags, 0, 0)))

    presses = (key(windows_key), key(h_key), key(h_key, key_up), key(windows_key, key_up))
    events = (Input * len(presses))(*presses)
    sent = ctypes.windll.user32.SendInput(len(presses), events, ctypes.sizeof(Input))
    return sent == len(presses)
