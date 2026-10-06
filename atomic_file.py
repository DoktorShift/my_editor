# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Writing the app's own files so that a crash can never break them.

Every file the app keeps for itself (settings, the session, crash
backups, keys, caches, ledgers) is written the same way: into a new file
beside it first, flushed to the disk, and then put in place in one step.
A crash, a full disk or a power cut in the middle leaves the previous
version untouched, never half a file.

The new file is readable by this account only, as fits files that hold
private text, keys and lists of what the person looked at. It is written
as bytes, so line endings are the same on every system.

Reading never raises either: ``read_json`` gives the expected empty value
for a file that is missing, unreadable, not JSON or not the expected
shape, because a damaged settings file must not keep the app from
starting.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from typing import Any, Type, TypeVar, Union

PathLike = Union[str, "os.PathLike[str]"]
T = TypeVar("T", dict, list)

_log = logging.getLogger(__name__)

# On Windows a file that was just written can be held open for a moment by
# a virus scanner or the search indexer, and replacing it then fails with
# PermissionError. Trying again shortly is the documented way out; the
# waits add up to under half a second.
_WINDOWS_REPLACE_WAITS_S = (0.02, 0.05, 0.1, 0.25)


def write_bytes(path: PathLike, data: bytes) -> None:
    """Put ``data`` at ``path`` in one step, creating the folder if needed.

    Raises OSError when it cannot be written; the old file is then kept
    as it was and no temporary file is left behind."""
    path = os.fspath(path)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    # mkstemp gives a fresh name (two writers never share one) and makes
    # the file readable by this account only.
    fd, temporary = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".",
                                     suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _replace(temporary, path)
    except BaseException:
        _remove_quietly(temporary)
        raise
    _sync_folder(folder)


def write_text(path: PathLike, text: str) -> None:
    """``write_bytes`` for text, as UTF-8."""
    write_bytes(path, text.encode("utf-8"))


def write_json(path: PathLike, data: Any, **dump_options) -> bool:
    """Write ``data`` as JSON in one step. False when it could not be
    written (a full or read-only disk, say), in which case the old file is
    kept and the reason is logged. ``dump_options`` go to ``json.dumps``."""
    try:
        text = json.dumps(data, **dump_options)
    except (TypeError, ValueError) as exc:
        _log.error("%s was not written: the data is not JSON (%s)", path, exc)
        return False
    try:
        write_text(path, text)
    except OSError as exc:
        _log.warning("%s could not be written: %s", path, exc)
        return False
    return True


def read_json(path: PathLike, kind: Type[T]) -> T:
    """The file's content when it is a ``kind`` (dict or list), else an
    empty one. A byte order mark, as some Windows editors write, is fine."""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except FileNotFoundError:
        return kind()
    except (OSError, ValueError) as exc:     # ValueError: bad JSON or bad UTF-8
        _log.warning("%s could not be read, starting empty: %s", path, exc)
        return kind()
    return data if isinstance(data, kind) else kind()


# -- helpers -------------------------------------------------------------------

def _replace(source: str, target: str) -> None:
    if sys.platform != "win32":
        os.replace(source, target)
        return
    for wait in _WINDOWS_REPLACE_WAITS_S:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            time.sleep(wait)
    os.replace(source, target)


def _sync_folder(folder: str) -> None:
    """Make the rename itself survive a power cut where the system allows
    it (Linux and macOS). Windows cannot open a folder this way, and it
    does not need to."""
    if sys.platform == "win32":
        return
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass        # some file systems do not sync folders; the data is safe
    finally:
        os.close(fd)


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
