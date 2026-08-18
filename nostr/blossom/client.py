# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP layer for Blossom: upload, list, delete, mirror.

Built on ``QNetworkAccessManager`` so all I/O stays on the Qt event
loop without manual threading, the same pattern as ``AvatarLoader`` and
``RelayPool``. Every operation is callback-driven: caller hands in
``on_success`` and ``on_failure`` slots, and exactly one of them fires
per request.

No CORS proxy is involved (this is a desktop app, not a SPA), so every
endpoint is hit directly.

Auth events (kind 24242) are built here as *unsigned* dicts. Signing is
the caller's responsibility. The typical wire-up has the caller hand
the unsigned event to ``BunkerClient.sign_event`` and then pass the
signed result back into the matching ``*_with_auth`` method.

Every request carries a signed authorization event, and on PUT it
carries the file itself, so the response is treated as hostile:

- redirects are refused, never followed. Qt's default policy re-sends
  the caller's raw headers to whatever host ``Location`` names, which
  would hand the signed event and the upload body to a stranger.
- the auth event's ``server`` tag must name the host being contacted,
  checked before the request is issued.
- response bodies are capped mid-transfer, not after buffering.
- every URL a server hands back is validated, and rewritten to the
  canonical ``<origin>/<sha256>`` form when it is not acceptable.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QByteArray, QObject, QUrl, Signal
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)

import url_safety

from .auth import to_auth_header
from .errors import ERROR_CODES


# Upload timeout is generous because the user could be pushing a 100 MiB
# video over a slow link. Set per-request via Qt's transfer-timeout,
# which resets each time bytes move, so a slow-but-progressing transfer
# isn't killed.
_UPLOAD_TIMEOUT_MS = 5 * 60 * 1000     # five minutes of *idle* time
_LIST_TIMEOUT_MS = 30 * 1000
_DELETE_TIMEOUT_MS = 30 * 1000
_MIRROR_TIMEOUT_MS = 60 * 1000

_USER_AGENT = b"my-editor-blossom/1"

# Response caps, enforced mid-transfer. A blob descriptor is a few
# hundred bytes; a full blob list for a heavy user is still small JSON.
_MAX_RESPONSE_BYTES = 1024 * 1024        # 1 MiB: upload, mirror, delete
_MAX_LIST_BYTES = 8 * 1024 * 1024        # 8 MiB: /list


# A signed event dict, typed as ``dict`` for documentation only.
SignedEvent = dict


# Result shapes returned via callbacks ---------------------------------------

class BlossomError(Exception):
    """Raised when a Blossom request fails. ``status`` is the HTTP status
    when known (0 for transport failures), ``body`` is the response body
    (truncated to keep logs sane), ``code`` is a stable
    ``nostr.blossom.errors`` code when one applies, so the UI never has
    to string-match a transport message."""

    def __init__(
        self,
        reason: str,
        *,
        status: int = 0,
        body: str = "",
        code: str = "",
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.body = body[:500]
        self.code = code


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

_HEX_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def server_origin(server_url: str) -> str:
    """Return ``scheme://host[:port]`` for ``server_url`` (no path, no
    trailing slash). Used to scope the ``server`` tag on auth events to
    a consistent value across upload / list / delete on the same host.

    Delegates the parse so an IPv6 host keeps its brackets: reassembling
    a bare ``::1`` yields ``http://::1:3000``, which nothing can parse
    back."""
    origin = url_safety.origin_of(server_url)
    if origin is None:
        raise ValueError(f"not a usable server URL: {server_url!r}")
    return origin


def extract_server_from_blob_url(blob_url: str) -> Optional[str]:
    """Best-effort: given a blob URL like ``https://blossom.band/<hash>``,
    return ``https://blossom.band``. Returns None if the URL is malformed.
    Used so delete requests target the same server the blob actually
    lives on, not the configured primary."""
    try:
        return server_origin(blob_url)
    except ValueError:
        return None


def looks_like_sha256(value: str) -> bool:
    return isinstance(value, str) and bool(_HEX_SHA256_RE.fullmatch(value.lower()))


def safe_blob_url(candidate, server: str, sha256: str) -> str:
    """Return a blob URL that is safe to cache, store and publish.

    The server-supplied ``candidate`` is kept only when it is a media
    URL on the server's own origin. Anything else, a ``file://`` path, a
    ``data:`` URI, or another host, is replaced by the canonical
    ``<origin>/<sha256>`` form rather than dropped: BUD-01 guarantees
    ``GET /<sha256>`` on the same origin serves the blob, so the
    descriptor keeps working while the boundary closes.
    """
    origin = url_safety.origin_of(server) or str(server).rstrip("/")
    canonical = f"{origin}/{sha256}"
    if not candidate:
        return canonical
    text = str(candidate)
    if url_safety.is_safe_media_url(text, allowed_origin=server):
        return text
    return canonical


# ---------------------------------------------------------------------------
# Result data classes
# ---------------------------------------------------------------------------

class UploadResult(dict):
    """Server response from ``PUT /upload`` (or ``/mirror``).

    Kept as a ``dict`` subclass so callers can treat it like the parsed
    JSON it came from, with named accessors for the fields the rest of
    the app cares about. The Blossom spec calls this a Blob Descriptor.

    Required fields:
        hash, url, size, mime_type, server
    """

    @classmethod
    def from_json(cls, data: dict, server: str) -> "UploadResult":
        sha = (data.get("sha256") or "").lower()
        if not looks_like_sha256(sha):
            raise BlossomError(
                f"server response missing or malformed sha256: {data!r}"
            )
        result = cls(
            hash=sha,
            url=safe_blob_url(data.get("url"), server, sha),
            size=int(data.get("size") or 0),
            mime_type=str(data.get("type") or "application/octet-stream"),
            server=server,
        )
        return result


# ---------------------------------------------------------------------------
# Response guards
# ---------------------------------------------------------------------------

def _auth_server_host(auth_event: SignedEvent) -> Optional[str]:
    """Hostname named by the auth event's ``server`` tag, when present.

    Tolerates both spellings: today's full origin, and the bare
    lowercase domain BUD-11 mandates.
    """
    for tag in (auth_event or {}).get("tags") or []:
        if not isinstance(tag, (list, tuple)) or len(tag) < 2:
            continue
        if tag[0] != "server":
            continue
        value = str(tag[1]).strip()
        if "://" in value:
            return url_safety.host_of(value)
        return value.rstrip("/").lower() or None
    return None


def _require_matching_host(url: str, auth_event: SignedEvent) -> None:
    """Refuse to send an auth event to a host it does not name.

    The signed event is a bearer credential. A request that carries one
    to a different origin than the user authorised is the exact leak the
    redirect policy also guards against, arriving by another route.
    """
    tag_host = _auth_server_host(auth_event)
    if tag_host is None:
        return
    if tag_host != (url_safety.host_of(url) or ""):
        raise BlossomError(
            "auth event does not match the request host",
            code=ERROR_CODES.HOST_MISMATCH,
        )


def _sanitize_list_urls(payload: List[dict], server: str) -> List[dict]:
    """Rewrite unacceptable blob URLs in a ``/list`` response.

    Runs at parse time so no consumer downstream, the library, the
    cache, the document, ever sees a ``file://`` or cross-origin URL.
    Entries without a usable sha256 pass through untouched: the store
    already skips them, and rewriting one would invent a URL.
    """
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        sha = str(entry.get("sha256") or "").lower()
        if not looks_like_sha256(sha):
            continue
        entry["url"] = safe_blob_url(entry.get("url"), server, sha)
    return payload


def _redirect_error(reply, status: int) -> Optional[BlossomError]:
    """A refused 3xx, or None when the response is not a redirect.

    Both tests are needed: with ``ManualRedirectPolicy`` Qt leaves
    ``error()`` at ``NoError`` on a 3xx, and some replies carry the
    redirection target attribute without a status this layer can see.
    """
    target = reply.attribute(QNetworkRequest.Attribute.RedirectionTargetAttribute)
    if target is None and not (300 <= status < 400):
        return None
    return BlossomError(
        "server redirected the request; it was not resent",
        status=status,
        code=ERROR_CODES.REDIRECT_REFUSED,
    )


def _oversize_error(status: int) -> BlossomError:
    return BlossomError(
        "server response exceeded the size limit",
        status=status,
        code=ERROR_CODES.TOO_LARGE,
    )


def _guard_response_size(reply, max_bytes: int) -> dict:
    """Abort ``reply`` the moment the body passes ``max_bytes``.

    Returns the flag dict the finished handler reads, so an abort is
    reported as a size rejection rather than a generic network failure.
    The announced total is honoured too: a server that declares a huge
    Content-Length is stopped before it sends the first chunk.
    """
    oversize = {"hit": False}

    def _on_progress(received: int, total: int, r=reply) -> None:
        if oversize["hit"]:
            return
        if received > max_bytes or (total > 0 and total > max_bytes):
            oversize["hit"] = True
            r.abort()

    reply.downloadProgress.connect(_on_progress)
    return oversize


# ---------------------------------------------------------------------------
# BlossomClient
# ---------------------------------------------------------------------------

class _InflightUpload(QObject):
    """One in-flight ``PUT /upload``. Wraps the reply so we can route
    progress + finished into ``BlossomClient`` callbacks without lambda
    spaghetti."""

    progress = Signal(int, int)   # bytes_sent, bytes_total

    def __init__(
        self,
        reply: QNetworkReply,
        server: str,
        on_success: Callable[[UploadResult], None],
        on_failure: Callable[[BlossomError], None],
        on_progress: Optional[Callable[[int, int], None]],
        parent: Optional[QObject] = None,
        max_bytes: int = _MAX_RESPONSE_BYTES,
    ) -> None:
        super().__init__(parent)
        self._reply = reply
        self._server = server
        self._on_success = on_success
        self._on_failure = on_failure
        self._on_progress = on_progress
        self._oversize = _guard_response_size(reply, max_bytes)

        reply.uploadProgress.connect(self._emit_progress)
        reply.finished.connect(self._on_finished)

    def _emit_progress(self, sent: int, total: int) -> None:
        if self._on_progress is not None:
            self._on_progress(int(sent), int(total))

    def _on_finished(self) -> None:
        reply = self._reply
        try:
            err = reply.error()
            status = int(
                reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) or 0
            )
            redirect = _redirect_error(reply, status)
            if redirect is not None:
                self._on_failure(redirect)
                return
            if self._oversize["hit"]:
                self._on_failure(_oversize_error(status))
                return
            raw_body = bytes(reply.readAll())
            body_text = raw_body.decode("utf-8", errors="replace")
            if err != QNetworkReply.NoError or not (200 <= status < 300):
                self._on_failure(
                    BlossomError(
                        reply.errorString() or f"HTTP {status}",
                        status=status,
                        body=body_text,
                    )
                )
                return
            try:
                payload = json.loads(body_text) if body_text else {}
            except json.JSONDecodeError:
                self._on_failure(
                    BlossomError(
                        "server returned non-JSON upload response",
                        status=status,
                        body=body_text,
                    )
                )
                return
            if not isinstance(payload, dict):
                self._on_failure(
                    BlossomError(
                        "server returned non-object upload response",
                        status=status,
                        body=body_text,
                    )
                )
                return
            try:
                result = UploadResult.from_json(payload, self._server)
            except BlossomError as exc:
                self._on_failure(exc)
                return
            self._on_success(result)
        finally:
            reply.deleteLater()


class BlossomClient(QObject):
    """HTTP-level Blossom client. Stateless apart from the shared QNAM.

    All methods take the *signed* auth event (a dict with ``id``,
    ``pubkey``, ``sig`` etc.) and the target server origin. The signing
    handshake with the bunker happens one level up in ``MediaStore``.
    """

    def __init__(self, parent: Optional[QObject] = None, *, nam=None) -> None:
        super().__init__(parent)
        # ``nam`` is a seam for tests: a fake transport keeps the request
        # attributes assertable without a network.
        self._nam = nam or QNetworkAccessManager(self)
        # Strong refs to in-flight wrappers so they live until ``finished``.
        self._inflight: Dict[int, QObject] = {}

    # -- upload ------------------------------------------------------------

    def upload(
        self,
        server: str,
        body: bytes,
        mime_type: str,
        auth_event: SignedEvent,
        on_success: Callable[[UploadResult], None],
        on_failure: Callable[[BlossomError], None],
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Upload ``body`` (raw bytes) to ``server``'s ``/upload``.

        Blossom convention is ``PUT /upload`` with ``Authorization: Nostr
        <base64>``. The server computes its own sha256 and rejects if it
        doesn't match the ``x`` tag signed into the auth event.
        """
        try:
            request = self._prepare(
                f"{server.rstrip('/')}/upload",
                auth_event=auth_event,
                content_type=mime_type or "application/octet-stream",
                timeout_ms=_UPLOAD_TIMEOUT_MS,
            )
        except BlossomError as exc:
            on_failure(exc)
            return
        reply = self._nam.put(request, QByteArray(body))
        # Track the wrapper by reply id so we don't leak.
        wrapper = _InflightUpload(
            reply,
            server,
            on_success=lambda r, key=id(reply): self._finish(key, lambda: on_success(r)),
            on_failure=lambda e, key=id(reply): self._finish(key, lambda: on_failure(e)),
            on_progress=on_progress,
            parent=self,
        )
        self._inflight[id(reply)] = wrapper

    # -- mirror ------------------------------------------------------------

    def mirror(
        self,
        server: str,
        source_url: str,
        auth_event: SignedEvent,
        on_success: Callable[[UploadResult], None],
        on_failure: Callable[[BlossomError], None],
    ) -> None:
        """Ask ``server`` to fetch a blob from ``source_url`` and host it
        too. BUD-04: ``PUT /mirror`` with JSON ``{"url": source_url}``.

        The auth event for /mirror uses ``t=upload`` per BUD-04: the
        server treats /mirror as an upload-by-URL.
        """
        try:
            request = self._prepare(
                f"{server.rstrip('/')}/mirror",
                auth_event=auth_event,
                content_type="application/json",
                timeout_ms=_MIRROR_TIMEOUT_MS,
            )
        except BlossomError as exc:
            on_failure(exc)
            return
        body = json.dumps({"url": source_url}, separators=(",", ":")).encode("utf-8")
        reply = self._nam.put(request, QByteArray(body))
        wrapper = _InflightUpload(
            reply,
            server,
            on_success=lambda r, key=id(reply): self._finish(key, lambda: on_success(r)),
            on_failure=lambda e, key=id(reply): self._finish(key, lambda: on_failure(e)),
            on_progress=None,
            parent=self,
        )
        self._inflight[id(reply)] = wrapper

    # -- list --------------------------------------------------------------

    def list_for_pubkey(
        self,
        server: str,
        pubkey_hex: str,
        auth_event: Optional[SignedEvent],
        on_success: Callable[[List[dict]], None],
        on_failure: Callable[[BlossomError], None],
    ) -> None:
        """``GET /list/<pubkey>`` returns the user's blob descriptors.

        ``auth_event`` is optional: some servers serve the list publicly
        (BUD-02 says auth MAY be required). It is always sent when
        available; the store retries without auth on 401/403 to match
        STANDUP's fallback behaviour.
        """
        url = f"{server.rstrip('/')}/list/{pubkey_hex.lower()}"
        try:
            request = self._prepare(
                url,
                auth_event=auth_event,
                accept=b"application/json",
                timeout_ms=_LIST_TIMEOUT_MS,
            )
        except BlossomError as exc:
            on_failure(exc)
            return

        reply = self._nam.get(request)
        key = id(reply)
        oversize = _guard_response_size(reply, _MAX_LIST_BYTES)

        def _finished() -> None:
            try:
                err = reply.error()
                status = int(
                    reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) or 0
                )
                redirect = _redirect_error(reply, status)
                if redirect is not None:
                    self._finish(key, lambda: on_failure(redirect))
                    return
                if oversize["hit"]:
                    self._finish(key, lambda: on_failure(_oversize_error(status)))
                    return
                raw_body = bytes(reply.readAll())
                body_text = raw_body.decode("utf-8", errors="replace")
                if err != QNetworkReply.NoError or not (200 <= status < 300):
                    self._finish(
                        key,
                        lambda: on_failure(
                            BlossomError(
                                reply.errorString() or f"HTTP {status}",
                                status=status,
                                body=body_text,
                            )
                        ),
                    )
                    return
                try:
                    payload = json.loads(body_text) if body_text else []
                except json.JSONDecodeError:
                    self._finish(
                        key,
                        lambda: on_failure(
                            BlossomError(
                                "list returned non-JSON",
                                status=status,
                                body=body_text,
                            )
                        ),
                    )
                    return
                if not isinstance(payload, list):
                    self._finish(
                        key,
                        lambda: on_failure(
                            BlossomError(
                                "list returned non-array payload",
                                status=status,
                                body=body_text,
                            )
                        ),
                    )
                    return
                sanitized = _sanitize_list_urls(payload, server)
                self._finish(key, lambda: on_success(sanitized))
            finally:
                reply.deleteLater()

        reply.finished.connect(_finished)
        # Keep a strong ref via the inflight map.
        self._inflight[key] = reply

    # -- delete ------------------------------------------------------------

    def delete(
        self,
        server: str,
        file_hash: str,
        auth_event: SignedEvent,
        on_success: Callable[[], None],
        on_failure: Callable[[BlossomError], None],
    ) -> None:
        """``DELETE /<sha256>`` with auth."""
        url = f"{server.rstrip('/')}/{file_hash.lower()}"
        try:
            request = self._prepare(
                url,
                auth_event=auth_event,
                timeout_ms=_DELETE_TIMEOUT_MS,
            )
        except BlossomError as exc:
            on_failure(exc)
            return

        reply = self._nam.deleteResource(request)
        key = id(reply)
        oversize = _guard_response_size(reply, _MAX_RESPONSE_BYTES)

        def _finished() -> None:
            try:
                err = reply.error()
                status = int(
                    reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) or 0
                )
                redirect = _redirect_error(reply, status)
                if redirect is not None:
                    self._finish(key, lambda: on_failure(redirect))
                    return
                if oversize["hit"]:
                    self._finish(key, lambda: on_failure(_oversize_error(status)))
                    return
                raw_body = bytes(reply.readAll())
                if err == QNetworkReply.NoError and 200 <= status < 300:
                    self._finish(key, on_success)
                    return
                body_text = raw_body.decode("utf-8", errors="replace")
                self._finish(
                    key,
                    lambda: on_failure(
                        BlossomError(
                            reply.errorString() or f"HTTP {status}",
                            status=status,
                            body=body_text,
                        )
                    ),
                )
            finally:
                reply.deleteLater()

        reply.finished.connect(_finished)
        self._inflight[key] = reply

    # -- internals ---------------------------------------------------------

    def _prepare(
        self,
        url: str,
        *,
        auth_event: Optional[SignedEvent] = None,
        content_type: Optional[str] = None,
        accept: Optional[bytes] = None,
        timeout_ms: int,
    ) -> QNetworkRequest:
        """Build the request for every Blossom verb.

        One builder on purpose: a second one is a place for the redirect
        policy to be forgotten. Raises :class:`BlossomError` when the
        auth event does not match the host, before anything is sent.
        """
        if auth_event is not None:
            _require_matching_host(url, auth_event)

        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", _USER_AGENT)
        request.setTransferTimeout(timeout_ms)
        # Qt's default follows up to 50 hops and re-sends the raw headers,
        # which would copy the signed auth event and, on PUT, the whole
        # body to whatever host ``Location`` names. Manual policy stops
        # at the 3xx and lets the finished handler refuse it.
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        request.setMaximumRedirectsAllowed(0)
        if content_type:
            request.setHeader(QNetworkRequest.ContentTypeHeader, content_type)
        if accept:
            request.setRawHeader(b"Accept", accept)
        if auth_event is not None:
            request.setRawHeader(
                b"Authorization", to_auth_header(auth_event).encode("ascii")
            )
        return request

    def _finish(self, key: int, callback: Callable[[], None]) -> None:
        """Pop the inflight wrapper before invoking the user callback.

        Order matters: the callback may re-enter (e.g. uploading the
        next file in a queue), so we must release the slot first."""
        self._inflight.pop(key, None)
        try:
            callback()
        except Exception:  # noqa: BLE001, never let one callback break another
            # Surface unexpected callback errors via stderr but keep the
            # event loop healthy. We don't have a logger plumbed in here
            # yet; the rest of the codebase prints to stderr similarly.
            import traceback
            traceback.print_exc()
