# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Runs a script that builds the whole window in a child process, with a
home folder of its own.

The app keeps its settings, session, crash backups and logs in the home
folder, and a test must never read or touch the real ones, nor find what
another test left there. Python finds the home folder in HOME on macOS
and Linux but in USERPROFILE on Windows, so both point at the test's
folder.

The child draws offscreen, where a window is active as soon as it asks
to be, so keys and shortcuts reach it the same way on every system.
Qt's offscreen platform reads its fonts from a folder instead of asking
the system; on Windows that folder is the system's own, or the child
would have no font at all.

The script gets the repository folder and ``args`` as its arguments and
prints its result as one line: ``RESULT`` and the JSON.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def child_env(home) -> dict:
    """The environment of a child whose home folder is ``home``."""
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), QT_QPA_PLATFORM="offscreen")
    if sys.platform == "win32":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        env.setdefault("QT_QPA_FONTDIR", os.path.join(system_root, "Fonts"))
    return env


def run_window_script(script: str, home, *args: str, timeout: float = 120) -> dict:
    """Run ``script`` with ``home`` as its home folder and return what it
    printed on its RESULT line."""
    proc = subprocess.run([sys.executable, "-c", script, REPO, *args], env=child_env(home),
                          capture_output=True, text=True, timeout=timeout)
    result = next((out for out in proc.stdout.splitlines() if out.startswith("RESULT ")), None)
    assert result, f"child failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(result[len("RESULT "):])
