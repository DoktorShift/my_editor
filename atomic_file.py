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

The person's own files (a saved document, an export, a copied picture)
are written with ``save_document``: the same one-step replacement, but
keeping what belongs to the existing file (see there).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import logging
import os
import stat
import sys
import tempfile
import time
from typing import Any, Optional, Type, TypeVar, Union

PathLike = Union[str, "os.PathLike[str]"]
T = TypeVar("T", dict, list)

_log = logging.getLogger(__name__)

# On Windows a file that was just written can be held open for a moment by
# a virus scanner or the search indexer, and replacing it then fails with
# PermissionError. Trying again shortly is the documented way out; the
# waits add up to under half a second.
_WINDOWS_REPLACE_WAITS_S = (0.02, 0.05, 0.1, 0.25)


def _new_file_mode() -> int:
    """The permissions a newly created file gets on this system (the
    umask applied to read and write for everyone). Read once at import,
    while the app still runs a single thread."""
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


_NEW_FILE_MODE = _new_file_mode()


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


def save_document(path: PathLike, data: bytes) -> None:
    """Save one of the person's own files in one step.

    A full disk or a crash during the save leaves the file as it was,
    instead of empty or cut off. What belongs to an existing file stays:
    its permissions, and its extended attributes on macOS and Linux
    (Finder tags, security labels). A link keeps pointing where it did,
    because the file it points to is the one replaced. A new file gets
    the permissions any new file gets on this system. Where the file
    cannot be replaced (a folder the person may not write in, or a file
    with several hard links, which a replacement would split), it is
    written in place, as before. Raises OSError."""
    target = os.path.realpath(os.fspath(path))
    folder = os.path.dirname(target)
    try:
        existing: Optional[os.stat_result] = os.stat(target)
    except FileNotFoundError:
        existing = None
    if existing is not None and existing.st_nlink > 1:
        _write_in_place(target, data)
        return
    try:
        fd, temporary = tempfile.mkstemp(prefix="." + os.path.basename(target) + ".",
                                         suffix=".tmp", dir=folder)
    except PermissionError:
        _write_in_place(target, data)
        return
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temporary, stat.S_IMODE(existing.st_mode) if existing is not None
                 else _NEW_FILE_MODE)
        if existing is not None:
            _copy_extended_attributes(target, temporary)
        _replace(temporary, target)
    except BaseException:
        _remove_quietly(temporary)
        raise
    _sync_folder(folder)


def save_text_document(path: PathLike, text: str, *, encoding: str = "utf-8") -> None:
    """``save_document`` for text, with this system's line endings, as
    text files have always been saved by the app."""
    save_document(path, text.replace("\n", os.linesep).encode(encoding))


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


def _write_in_place(target: str, data: bytes) -> None:
    with open(target, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _copy_extended_attributes(source: str, target: str) -> None:
    """Best effort: a file that lost its tags is still saved."""
    if sys.platform == "darwin":
        _copy_attributes_macos(source, target)
    elif hasattr(os, "listxattr"):
        try:
            names = os.listxattr(source)
        except OSError:
            return
        for name in names:
            try:
                os.setxattr(target, name, os.getxattr(source, name))
            except OSError:
                # security.* and trusted.* need privileges; the rest copied.
                _log.debug("extended attribute %s of %s not kept", name, source)


_COPYFILE_ACL = 1 << 0
_COPYFILE_XATTR = 1 << 2
_copyfile = None


def _copy_attributes_macos(source: str, target: str) -> None:
    # copyfile(3) with only these flags copies the access list and the
    # extended attributes (Finder tags among them), not the content and
    # not the times, so the saved file keeps its new modification time.
    global _copyfile
    if _copyfile is None:
        try:
            libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
            function = libc.copyfile
        except (OSError, AttributeError):
            _copyfile = False
            return
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                             ctypes.c_uint32]
        function.restype = ctypes.c_int
        _copyfile = function
    if not _copyfile:
        return
    if _copyfile(os.fsencode(source), os.fsencode(target), None,
                 _COPYFILE_ACL | _COPYFILE_XATTR) != 0:
        _log.debug("attributes of %s not kept (errno %s)", source, ctypes.get_errno())


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
