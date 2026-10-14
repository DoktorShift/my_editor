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
from typing import Any, Optional, Tuple, Type, TypeVar, Union

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

# As much of a document's name as its temporary file's name begins with:
# at four bytes a character at most, it leaves room in 255 bytes for the
# random part and the suffix.
_TEMPORARY_NAME_CHARS = 40


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
    if _hard_linked(target) or _denies_delete(target):
        _write_in_place(target, data)
        return
    try:
        fd, temporary = new_document_file(target)
    except PermissionError:
        _write_in_place(target, data)
        return
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        _remove_quietly(temporary)
        raise
    place_document(temporary, target)


def place_document(temporary: PathLike, path: PathLike) -> None:
    """Put a file written in full (from ``new_document_file`` for the same
    ``path``) at ``path`` in one step, as ``save_document`` saves: a link
    keeps pointing where it did, and the file it points to keeps its
    permissions and extended attributes. A file with several hard links,
    or one whose folder couldn't take ``temporary`` (made beside a link
    instead), gets the content in place. ``temporary`` is gone
    afterwards, whatever happens. Raises OSError."""
    temporary, target = os.fspath(temporary), os.path.realpath(os.fspath(path))
    try:
        # Written in place: a file with several names, one nobody may
        # delete (replacing it would), or one whose folder couldn't take
        # the temporary file (it was made elsewhere).
        if _hard_linked(target) or _denies_delete(target) or \
                os.path.dirname(os.path.realpath(temporary)) != os.path.dirname(target):
            with open(temporary, "rb") as source:
                _write_in_place(target, source.read())
            _remove_quietly(temporary)
            return
        try:
            existing: Optional[os.stat_result] = os.stat(target)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            _keep_owner(temporary, existing)
            os.chmod(temporary, stat.S_IMODE(existing.st_mode))
            _copy_extended_attributes(target, temporary)
            _keep_creation_date(temporary, existing)
        else:
            os.chmod(temporary, _NEW_FILE_MODE)
        _replace(temporary, target)
    except BaseException:
        _remove_quietly(temporary)
        raise
    _sync_folder(os.path.dirname(target))


def _keep_owner(temporary: str, existing: os.stat_result) -> None:
    """Best effort, as the system's editors do: the file keeps its group
    (any account may give its file one of its own groups) and its owner
    (where this account may give it). Before the permissions are set,
    which changing the owner can clear."""
    if not hasattr(os, "chown"):
        return
    for owner, group in ((existing.st_uid, existing.st_gid), (-1, existing.st_gid)):
        try:
            os.chown(temporary, owner, group)
            return
        except OSError:
            continue


class _AttrList(ctypes.Structure):
    """setattrlist(2)'s struct attrlist."""
    _fields_ = [("bitmapcount", ctypes.c_ushort), ("reserved", ctypes.c_uint16),
                ("commonattr", ctypes.c_uint32), ("volattr", ctypes.c_uint32),
                ("dirattr", ctypes.c_uint32), ("fileattr", ctypes.c_uint32),
                ("forkattr", ctypes.c_uint32)]


class _Timespec(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]


_ATTR_BIT_MAP_COUNT = 5
_ATTR_CMN_CRTIME = 0x00000200


def _keep_creation_date(temporary: str, existing: os.stat_result) -> None:
    """On macOS, best effort: the new file keeps the document's creation
    date (Finder's "Created"), as TextEdit keeps it, instead of the time
    of this save."""
    born = getattr(existing, "st_birthtime", None)
    libc = _libc() if sys.platform == "darwin" and born is not None else None
    if libc is None:
        return
    setattrlist = libc.setattrlist
    setattrlist.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                            ctypes.c_ulong]
    setattrlist.restype = ctypes.c_int
    seconds = int(born)
    when = _Timespec(seconds, int(round((born - seconds) * 1e9)))
    attributes = _AttrList(_ATTR_BIT_MAP_COUNT, 0, _ATTR_CMN_CRTIME, 0, 0, 0, 0)
    if setattrlist(os.fsencode(temporary), ctypes.byref(attributes), ctypes.byref(when),
                   ctypes.sizeof(when), 0) != 0:
        _log.debug("creation date of %s not kept (errno %s)", temporary, ctypes.get_errno())


_libc_handle = None


def _libc():
    """The C library, for what Python doesn't offer; None where it can't
    be loaded."""
    global _libc_handle
    if _libc_handle is None:
        try:
            _libc_handle = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        except OSError:
            _libc_handle = False
    return _libc_handle or None


_ACL_TYPE_EXTENDED = 0x00000100


def _denies_delete(target: str) -> bool:
    """On macOS, whether the file's access list forbids deleting it, which
    replacing it does: it is then written in place, and no temporary file
    that copied the list (and so couldn't be deleted either) is made."""
    libc = _libc() if sys.platform == "darwin" else None
    if libc is None:
        return False
    libc.acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_int]
    libc.acl_get_file.restype = ctypes.c_void_p
    libc.acl_to_text.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    libc.acl_to_text.restype = ctypes.c_void_p
    libc.acl_free.argtypes = [ctypes.c_void_p]
    acl = libc.acl_get_file(os.fsencode(target), _ACL_TYPE_EXTENDED)
    if not acl:
        return False                                 # none, or no such file
    try:
        text = libc.acl_to_text(acl, None)
        if not text:
            return False
        try:
            listed = ctypes.string_at(text).decode("utf-8", "replace")
        finally:
            libc.acl_free(text)
    finally:
        libc.acl_free(acl)
    # Lines such as "user:FFFFEEEE-…:everyone:12:deny:delete,write", or
    # "…:deny,inherited:delete" for an entry the folder passed on.
    return any(len(fields) > 1 and "deny" in fields[-2].split(",")
               and "delete" in fields[-1].split(",")
               for fields in (line.split(":") for line in listed.splitlines()))


def _hard_linked(target: str) -> bool:
    """Whether ``target`` is a file with several hard links, which a
    replacement would split."""
    try:
        return os.stat(target).st_nlink > 1
    except FileNotFoundError:
        return False


def new_document_file(path: PathLike, *, suffix: str = ".tmp",
                      follow_link: bool = True) -> Tuple[int, str]:
    """A new, empty hidden file in the folder of ``path`` (of the file it
    links to, unless ``follow_link`` is False), to write one of the
    person's files into before it takes its name: ``(fd, path)``.

    Its name begins with the document's (shortened, so a name of any
    length the folder holds still has room), so a file a crash leaves
    behind says whose it is. It is created new (never a file or a link
    already there, whoever left it) and readable by this account only
    while it is written: it takes the document's permissions when it is
    put in place (``place_document``). Raises OSError."""
    path = os.path.abspath(os.fspath(path))
    folder = (os.path.dirname(os.path.realpath(path)) if follow_link
              else os.path.realpath(os.path.dirname(path)))
    prefix = "." + os.path.basename(path)[:_TEMPORARY_NAME_CHARS] + "."
    return tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=folder)


def free_path(path: PathLike, taken=()) -> str:
    """``path``, or the first of "name 2.ext", "name 3.ext" and on (as the
    Finder numbers a copy) that is neither on the disk nor in ``taken``:
    where a new file goes without replacing one."""
    path = os.fspath(path)
    stem, ext = os.path.splitext(path)
    candidate, number = path, 2
    while candidate in taken or os.path.lexists(candidate):
        candidate, number = f"{stem} {number}{ext}", number + 1
    return candidate


def read_text_document(path: PathLike, *, encoding: str = "utf-8") -> Tuple[str, str]:
    """A text file's content with every line ending as ``"\n"``, and the
    line ending the file uses (``"\r\n"`` or ``"\n"``; this system's
    when the file has no line break), so that saving writes the file
    back the way it was. Raises OSError, and ValueError for text that is
    not in ``encoding``."""
    with open(path, "rb") as f:
        text = f.read().decode(encoding)
    return text.replace("\r\n", "\n").replace("\r", "\n"), newline_of(text)


def newline_of(text: str) -> str:
    """The line ending most lines of ``text`` use; this system's when
    there is none. A Markdown file from a Git repository keeps its "\n"
    on Windows, a file written on Windows keeps its "\r\n" elsewhere."""
    windows = text.count("\r\n")
    unix = text.count("\n") - windows
    if windows == unix == 0:
        return os.linesep
    return "\r\n" if windows > unix else "\n"


def save_text_document(path: PathLike, text: str, *, encoding: str = "utf-8",
                       newline: Optional[str] = None) -> None:
    """``save_document`` for text. Lines end in ``newline``, the ending the
    file had when it was read (see ``read_text_document``); a new file gets
    this system's."""
    text = text.replace("\r\n", "\n")
    save_document(path, text.replace("\n", newline or os.linesep).encode(encoding))


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


# A disk block, as the in-place save counts in them where the file system
# doesn't say.
_BLOCK = 4096


def _write_in_place(target: str, data: bytes) -> None:
    """Write ``data`` over the file at ``target`` itself, where it can't be
    replaced. Like a replacement, a failure leaves the file as it was.
    The disk first shows it has room for the whole new text, as a
    replacement needs it: a copy-on-write disk (btrfs, XFS, an APFS
    clone or snapshot) takes fresh room for every byte written over the
    old text too. The new text is written past the end, the old text
    untouched, and cut off again. Only then is it written over the old
    and the rest cut off, and when that fails all the same (a removed
    volume) the old text, kept in memory, is put back."""
    try:
        # Unbuffered, so nothing is left waiting to be written when a
        # write fails and the old text goes back.
        f = open(target, "r+b", buffering=0)
    except FileNotFoundError:
        with open(target, "wb") as new:              # nothing there to keep
            new.write(data)
            new.flush()
            os.fsync(new.fileno())
        return
    with f:
        old = f.read()
        try:
            _make_room(f, len(old), data)
        except OSError:
            _cut_back(f, len(old))
            raise
        try:
            _write_at(f, 0, data)
            f.truncate(len(data))
            os.fsync(f.fileno())
        except OSError:
            _put_back(f, old)
            raise


def _make_room(f, end: int, data: bytes) -> None:
    """Show the disk has room for ``data`` as it will hold it (a
    compressing disk takes little for zeros): written past the ``end``
    of ``f``, synced, then cut off, which leaves the room free. The last
    block of the old text is filled up and synced first, on its own, so
    ``data`` shares no run of blocks with it (btrfs keeps a whole run
    while any of it is in use) and no gap is left before it (a nearly
    full APFS disk refuses one)."""
    block = getattr(os.fstat(f.fileno()), "st_blksize", 0) or _BLOCK
    start = -(-end // block) * block
    if start > end:
        _write_at(f, end, bytes(start - end))
        os.fsync(f.fileno())
    _write_at(f, start, data)
    os.fsync(f.fileno())
    f.truncate(end)


def _write_at(f, start: int, data) -> None:
    """``data`` into the unbuffered ``f`` from ``start`` on."""
    f.seek(start)
    view = memoryview(data)
    while view:
        view = view[f.write(view):]


def _cut_back(f, end: int) -> None:
    """Best effort: what was written past the old text's ``end`` cut off;
    the old text itself was never touched."""
    try:
        f.truncate(end)
        os.fsync(f.fileno())
    except OSError:
        _log.error("%s could not be cut back to its old length", f.name)


def _put_back(f, old: bytes) -> None:
    """Best effort: the old text, as it was. Cut to its length first, then
    only the blocks that differ from it are written again, as each takes
    fresh room on a copy-on-write disk."""
    try:
        f.truncate(len(old))
        f.seek(0)
        now = f.read()
        for start in range(0, len(old), _BLOCK):
            block = old[start:start + _BLOCK]
            if now[start:start + _BLOCK] != block:
                _write_at(f, start, block)
        os.fsync(f.fileno())
    except OSError:
        _log.error("%s could not be put back as it was", f.name)


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
    _carry_quarantine(source, target)


_QUARANTINE = b"com.apple.quarantine"


def _carry_quarantine(source: str, target: str) -> None:
    """copyfile(3) rewrites the quarantine mark it copies (its flags, its
    time, the app that made it); where the file came from, as the system
    recorded it, is carried over unchanged instead."""
    libc = _libc()
    if libc is None:
        return
    getxattr, setxattr = libc.getxattr, libc.setxattr
    getxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t,
                         ctypes.c_uint32, ctypes.c_int]
    getxattr.restype = ctypes.c_ssize_t
    setxattr.argtypes = getxattr.argtypes
    setxattr.restype = ctypes.c_int
    value = ctypes.create_string_buffer(4096)
    size = getxattr(os.fsencode(source), _QUARANTINE, value, len(value), 0, 0)
    if size < 0:
        return                                       # none, or none readable
    if setxattr(os.fsencode(target), _QUARANTINE, value, size, 0, 0) != 0:
        _log.debug("quarantine mark of %s not kept (errno %s)", source, ctypes.get_errno())


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
