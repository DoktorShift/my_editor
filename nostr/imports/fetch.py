# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared async source fetchers on top of ``QNetworkAccessManager``.

The one fetch path every resolver uses. Matches the rest of the editor
(avatar loader, blossom client, relay pool): callback-driven, no
threading, exactly one of ``on_success`` / ``on_failure`` fires per
call. Failures arrive as :class:`~nostr.imports.errors.SourceError`
with a stable code so callers never string-match transport errors.

Bounds:
- body size capped at 16 MiB, enforced mid-transfer (the reply is
  aborted the moment the cap is crossed, not after buffering),
- 30 s transfer timeout,
- nothing on the person's own network: every address, and every
  redirect before it is followed (five at most), passes the network
  guard (netguard.py), which also connects to the address it checked
  (``NetGuard.get``: one transfer, shared by both fetchers here).

Decoding is intentionally permissive: feeds in the wild lie about
encoding, so we fall back through ``Content-Type charset`` to the XML
declaration to UTF-8 with replacement on errors.

:class:`BlobFetcher` is the same discipline for bytes rather than text.
Rehosting an image needs the bytes themselves, and
:class:`SourceFetcher` cannot serve that: it decodes every response to a
string, which mangles anything that is not text.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from PySide6.QtCore import QObject, QUrl
from PySide6.QtNetwork import QNetworkAccessManager

import url_safety
from i18n import _
from image_safety import sniff_image_mime

from .errors import ERROR_CODES, SourceError
from .netguard import UNRESOLVED_REASON, Fetched, NetGuard


_USER_AGENT = b"my-editor-rss/1"
_MAX_BODY_BYTES = 16 * 1024 * 1024        # 16 MiB hard cap on a single body
# Shown as it is: the importer has no friendlier copy for TOO_LARGE.
_OVERSIZE_MESSAGE = _("Feed exceeds the {size} MiB size limit").format(
    size=_MAX_BODY_BYTES // (1024 * 1024))

# Ceiling for a rehosted image, before the destination server's own cap
# narrows it further. A feed body should never carry anything near this.
_MAX_BLOB_BYTES = 25 * 1024 * 1024

_BLOB_USER_AGENT = b"my-editor-rehost/1"
# Short copy: these land in the per-image row of the image review
# dialog, next to the filename.
_BLOB_UNSAFE_URL = _("URL was not allowed")
_BLOB_OVERSIZE = _("Image is too large to rehost")
_BLOB_EMPTY = _("Image was empty")
_BLOB_NOT_FOUND = _("Image address could not be found")

_CHARSET_FROM_CONTENT_TYPE = re.compile(
    r"charset\s*=\s*([A-Za-z0-9_\-.:]+)", re.IGNORECASE
)
_CHARSET_FROM_XML_DECL = re.compile(
    rb"""<\?xml[^?>]*encoding\s*=\s*["']([A-Za-z0-9_\-.:]+)["']""", re.IGNORECASE
)


class SourceFetcher(QObject):
    """Reusable one-shot HTTP(S) body fetcher.

    Call :meth:`fetch` per request. The object owns one
    ``QNetworkAccessManager`` for the life of the instance. ``guard`` and
    ``nam`` are seams for tests (a resolver that never asks the network,
    a transport that never connects).
    """

    def __init__(self, parent: Optional[QObject] = None, *,
                 guard: Optional[NetGuard] = None, nam=None) -> None:
        super().__init__(parent)
        self._nam = nam or QNetworkAccessManager(self)
        self._guard = guard or NetGuard()

    def fetch(
        self,
        url: str,
        *,
        on_success: Callable[[str], None],
        on_failure: Callable[[SourceError], None],
    ) -> None:
        """Issue a GET and decode the response body as text.

        ``on_success`` receives the decoded body. ``on_failure`` receives
        a :class:`SourceError` with a stable code and a short reason.
        """
        qurl = QUrl(url)
        if not qurl.isValid() or qurl.scheme() not in ("http", "https"):
            on_failure(SourceError(
                _("Feed URL must be http(s)"), ERROR_CODES.FETCH_ERROR))
            return
        self._get(url, on_success, on_failure)

    def fetch_feed(
        self,
        url: str,
        *,
        etag: str = "",
        last_modified: str = "",
        on_body: Callable[[str, dict], None],
        on_not_modified: Callable[[], None],
        on_failure: Callable[[SourceError], None],
    ) -> None:
        """A conditional GET for a feed check: with the validators of the
        last answer, a feed that did not change answers 304 and costs no
        download (``on_not_modified``). ``on_body`` gets the text and the
        new validators: ``{"etag", "last_modified", "final_url"}``."""
        qurl = QUrl(url)
        if not qurl.isValid() or qurl.scheme() not in ("http", "https"):
            on_failure(SourceError(
                _("Feed URL must be http(s)"), ERROR_CODES.FETCH_ERROR))
            return
        headers = []
        if etag:
            headers.append((b"If-None-Match", etag.encode("latin-1", "ignore")))
        if last_modified:
            headers.append((b"If-Modified-Since", last_modified.encode("latin-1", "ignore")))
        meta: dict = {}
        self._get(url, lambda text: on_body(text, meta), on_failure, headers=headers,
                  meta=meta, on_not_modified=on_not_modified)

    def _get(self, url: str, on_success, on_failure, *, headers=(), meta=None,
             on_not_modified=None) -> None:
        headers = list(headers) + [
            (b"User-Agent", _USER_AGENT),
            (b"Accept", b"application/rss+xml, application/atom+xml, application/feed+json, "
                        b"application/xml;q=0.9, */*;q=0.1")]
        # The answer is read by a function that does not hold this
        # fetcher: the transfer is its manager's child, and a callback
        # holding the fetcher would close a cycle through them.
        answered = SourceFetcher._answered
        self._guard.get(self._nam, url, headers=headers, max_bytes=_MAX_BODY_BYTES,
                        on_done=lambda fetched: answered(
                            fetched, on_success, on_failure, meta, on_not_modified))

    @staticmethod
    def _answered(fetched: Fetched, on_success, on_failure, meta, on_not_modified) -> None:
        if fetched.refused:
            on_failure(SourceError(fetched.refused, ERROR_CODES.LOCAL_NETWORK))
            return
        if fetched.unresolved:
            on_failure(SourceError(UNRESOLVED_REASON, ERROR_CODES.FETCH_ERROR))
            return
        if fetched.too_large:
            on_failure(SourceError(_OVERSIZE_MESSAGE, ERROR_CODES.TOO_LARGE))
            return
        if fetched.error:
            on_failure(SourceError(fetched.error or _("network error"),
                                   ERROR_CODES.FETCH_ERROR))
            return
        if on_not_modified is not None and fetched.status == 304:
            on_not_modified()
            return
        if meta is not None:
            meta.update(etag=fetched.etag, last_modified=fetched.last_modified,
                        final_url=fetched.url)
        if not fetched.data:
            on_failure(SourceError("Empty response", ERROR_CODES.EMPTY_RESPONSE))
            return
        on_success(_decode_body(fetched.data, fetched.content_type))


class BlobFetcher(QObject):
    """Reusable one-shot fetcher for raw bytes, used by image rehosting.

    Call :meth:`fetch` per request; ``on_success`` receives
    ``(bytes, mime)``. The object owns one ``QNetworkAccessManager`` for
    the life of the instance.

    ``max_bytes`` narrows the cap to what the destination Blossom server
    says it accepts, so an image too big to rehost is dropped while it
    is being downloaded rather than after a server refuses it. This
    module's own ceiling is the upper bound either way.

    The address passes the network guard before the request, every
    redirect before it is followed, and the final address is checked
    once more on the reply, because the bytes may end up coming from
    somewhere the caller never named. This request carries no
    credentials, so a redirect cannot leak one.
    """

    def __init__(
        self,
        parent: Optional[QObject] = None,
        *,
        max_bytes: int = _MAX_BLOB_BYTES,
        accept: bytes = b"image/*;q=0.9, */*;q=0.1",
        nam=None,
        guard: Optional[NetGuard] = None,
    ) -> None:
        super().__init__(parent)
        # ``nam`` and ``guard`` are seams for tests: a fake transport and
        # resolver keep the size cap and the redirect recheck assertable
        # without a network.
        self._nam = nam or QNetworkAccessManager(self)
        self._guard = guard or NetGuard()
        self._max_bytes = max(1, min(int(max_bytes), _MAX_BLOB_BYTES))
        self._accept = accept

    def fetch(
        self,
        url: str,
        *,
        on_success: Callable[[bytes, str], None],
        on_failure: Callable[[str], None],
    ) -> None:
        """GET ``url`` and hand the raw body plus its mime type back.

        ``on_failure`` receives short user-facing copy rather than a
        Qt transport string: it is rendered per image in the review
        dialog, next to the filename.
        """
        if not url_safety.is_safe_mirror_source(url):
            on_failure(_BLOB_UNSAFE_URL)
            return
        answered = BlobFetcher._answered     # not a callback holding this fetcher
        self._guard.get(self._nam, url, headers=[
            (b"User-Agent", _BLOB_USER_AGENT),
            (b"Accept", self._accept)], max_bytes=self._max_bytes,
            on_done=lambda fetched: answered(fetched, on_success, on_failure))

    @staticmethod
    def _answered(fetched: Fetched, on_success, on_failure) -> None:
        if fetched.refused:
            on_failure(_BLOB_UNSAFE_URL)
        elif fetched.unresolved:
            on_failure(_BLOB_NOT_FOUND)
        elif fetched.too_large:
            on_failure(_BLOB_OVERSIZE)
        elif fetched.error:
            on_failure(_("Could not download the image"))
        elif not fetched.data:
            on_failure(_BLOB_EMPTY)
        else:
            on_success(fetched.data, _blob_mime(fetched.content_type, fetched.data))


def _blob_mime(content_type: str, data: bytes) -> str:
    """Mime for downloaded bytes: the header, then the bytes, then generic.

    The header is only a claim, so a generic or absent one falls through
    to the magic bytes. Nothing here decodes the image; the value is
    what gets sent on to the Blossom server as the blob's type.
    """
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    if declared and declared != "application/octet-stream":
        return declared
    return sniff_image_mime(data) or "application/octet-stream"


def _decode_body(data: bytes, content_type: str) -> str:
    """Best-effort byte-to-text decode.

    Priority: ``Content-Type charset`` then the XML declaration's
    ``encoding`` attribute then UTF-8 with replacement.
    """
    charset: Optional[str] = None
    match = _CHARSET_FROM_CONTENT_TYPE.search(content_type or "")
    if match:
        charset = match.group(1)
    if not charset:
        decl = _CHARSET_FROM_XML_DECL.search(data[:512])
        if decl:
            charset = decl.group(1).decode("ascii", errors="ignore")
    if not charset:
        charset = "utf-8"
    try:
        return data.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return data.decode("utf-8", errors="replace")
