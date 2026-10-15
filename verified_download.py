# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Downloading a file from its official source, and only that file.

What the app downloads for a feature the person turns on (R Markdown's
toolchain, the dictation model) comes over HTTPS from the hosts its makers
publish on, every redirect included, and is checked against the SHA-256
pinned for it when there is one. It lands in its place whole or not at all:
it is written beside it as ``.part`` and moved there once it checks out.
A cancelled download leaves nothing behind.
"""

from __future__ import annotations

import hashlib
import os
import urllib.request
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

_CHUNK = 256 * 1024


class DownloadError(Exception):
    """The file could not be had as published."""


class NotOfficial(DownloadError):
    """The address, or a redirect, is not on an allowed host."""


class ChecksumMismatch(DownloadError):
    """What arrived is not the file pinned."""


class Cancelled(Exception):
    """The person stopped the download."""


def host_allowed(url: str, hosts: Iterable[str]) -> bool:
    """Whether ``url`` is HTTPS on one of ``hosts``; an entry starting with
    a dot allows that domain's subdomains (".hf.co": "cdn-lfs.hf.co")."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host:
        return False
    return any(host == entry or (entry.startswith(".") and host.endswith(entry))
               for entry in hosts)


def download(url: str, dest: str, *, hosts: Iterable[str], sha256: Optional[str] = None,
             progress: Optional[Callable[[int, int], None]] = None, cancel=None,
             user_agent: str = "MyEditor") -> str:
    """Stream ``url`` to ``dest``. ``progress(done, total)`` as bytes come
    (total 0 when the server does not say); ``cancel.is_set()`` stops it.
    Raises NotOfficial, ChecksumMismatch, Cancelled, and OSError for the
    network or the disk."""
    hosts = tuple(hosts)
    if not host_allowed(url, hosts):
        raise NotOfficial(f"not an official download address: {url}")
    folder = os.path.dirname(dest)
    if folder:
        os.makedirs(folder, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    hasher = hashlib.sha256()
    partial = dest + ".part"
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if not host_allowed(response.geturl(), hosts):
                raise NotOfficial(f"redirected away from the official source: "
                                  f"{response.geturl()}")
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with open(partial, "wb") as f:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    chunk = response.read(_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    hasher.update(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)
        if sha256 is not None and hasher.hexdigest().lower() != sha256.lower():
            raise ChecksumMismatch(f"{os.path.basename(dest)} does not match its published "
                                   f"checksum")
    except BaseException:
        _remove(partial)
        raise
    os.replace(partial, dest)
    return dest


def _remove(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
