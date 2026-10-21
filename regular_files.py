# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Files from the disk read only when they are regular files.

Every file in a library folder came from somewhere: a download, a zip,
a git checkout, a shared drive. Before the app reads one it must be a
regular file: a named pipe would block the reader forever, a device
such as ``/dev/zero`` never ends, and a symlink may lead to either. So
``open_regular_file`` looks at what the path really is (``realpath``,
then ``stat``) before opening it, opens it without waiting (a pipe put
there in between does not block), and checks again what it opened.
``read_regular_file`` then reads at most a given number of bytes,
counting what it reads rather than trusting the size the file system
states.

Nothing here needs Qt, so any module may use it.
"""

from __future__ import annotations

import errno
import os
import stat
from typing import Optional


def is_regular_file(path: str) -> bool:
    """Whether ``path`` is, once every symlink is followed, a regular file
    (not a folder, pipe, device or socket)."""
    try:
        return stat.S_ISREG(os.stat(os.path.realpath(path)).st_mode)
    except (OSError, ValueError):
        return False


def _not_regular(path: str) -> OSError:
    return OSError(errno.EINVAL, "Not a regular file", path)


def open_regular_file(path: str):
    """``path`` opened for reading in binary, when it is a regular file;
    raises OSError otherwise. Nothing here waits: the file is opened
    without blocking, so a pipe put in its place after the check cannot
    hold the thread, and what was opened is checked again."""
    real = os.path.realpath(path)
    if not stat.S_ISREG(os.stat(real).st_mode):
        raise _not_regular(path)
    nonblocking = getattr(os, "O_NONBLOCK", 0)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0) | nonblocking
    fd = os.open(real, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise _not_regular(path)
        if nonblocking:
            os.set_blocking(fd, True)
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def read_regular_file(path: str, limit: int) -> Optional[bytes]:
    """The bytes of the regular file ``path``, or None when it is no
    regular file, cannot be read, or holds more than ``limit`` bytes
    (counted while reading: a stated size can be wrong)."""
    try:
        with open_regular_file(path) as stream:
            data = stream.read(limit + 1)
    except (OSError, ValueError):
        return None
    return None if len(data) > limit else data


def read_regular_prefix(path: str, count: int) -> Optional[bytes]:
    """The first ``count`` bytes of the regular file ``path`` (fewer when
    it is shorter), or None when it is no regular file or unreadable."""
    try:
        with open_regular_file(path) as stream:
            return stream.read(count)
    except (OSError, ValueError):
        return None


# How much of a file of no known kind is read to tell text from binary.
TEXT_SNIFF_BYTES = 8192


def reads_as_text(path: str, limit: int = TEXT_SNIFF_BYTES) -> bool:
    """Whether the file at ``path`` is text the editor can show (a script,
    a configuration, a log): a regular file with no NUL byte in its first
    ``limit`` bytes, and those decode as UTF-8 (a character cut at the end
    aside). A pipe or a device is no text."""
    head = read_regular_prefix(path, limit)
    if head is None:
        return False
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as error:
        # A character cut by the end of what was read is still text.
        return error.start >= len(head) - 3
    return True


def file_stamp(path: str) -> Optional[tuple]:
    """The size and modification time of the file at ``path``, which a
    write to it changes; None when there is no file to read them of."""
    try:
        status = os.stat(path)
    except (OSError, ValueError):
        return None
    return status.st_size, status.st_mtime_ns
