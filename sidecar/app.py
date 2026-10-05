# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MyEditor membership sidecar: holds the EINUNDZWANZIG association's API key.

The association names each application that may sign people up by a
client key (``X-Api-Key``). A key inside a desktop app can be extracted
by anyone, so it lives here instead, on a small service that runs next to
the app's release channel (rinbal's server for official builds; your own
if you build and publish MyEditor yourself). MyEditor sends its
membership requests here; this service checks them, adds the key, and
forwards them to the association. It holds nothing else: no accounts, no
database, no user data.

What it forwards, and only that: the association's membership API under
``/api/v1/membership`` (config, me, applications, invoice, refresh,
payments, export, erase). Everything else is 404.

What it checks before lending its key:

- Every call but ``config`` carries a NIP-98 signature (kind 27235) whose
  ``u`` tag is the association's own URL for exactly this request, whose
  ``method`` tag matches, whose ``payload`` tag is the SHA-256 of exactly
  the bytes sent (and absent without a body), which is less than a minute
  old, validly signed, and not seen before. The association checks the
  same again; checking here first means the key is only ever spent on
  requests the association would accept.
- Requests per client address per minute, and invoices per Nostr account
  per day, are limited, because the key's quota is shared by everyone.
- Bodies and answers are size-capped; the answer passes through unchanged
  except that the key, should the association ever echo it, is removed.

What it answers itself:

    GET /status   {"service": "myeditor-sidecar", "version": ..., "membership": bool}
                  MyEditor asks this before offering to join in the app;
                  false (no key configured) means the app offers the
                  association's website instead.
    GET /healthz  {"ok": true}, for uptime monitors.

Configuration is environment variables (see .env.example and README.md).
The key is never logged and never part of any answer.

Run: ``uvicorn sidecar.app:app`` from the repository root (the app's own
``nostr`` package provides the signature checks).
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import zlib
from collections import OrderedDict, defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from nostr import nip98

VERSION = "1"
API_PREFIX = "/api/v1/membership"
MAX_BODY_BYTES = 32 * 1024
MAX_ANSWER_BYTES = 1024 * 1024
REPLAY_SECONDS = 150
CONFIG_CACHE_SECONDS = 300
UPSTREAM_TIMEOUT_SECONDS = 15.0

log = logging.getLogger("myeditor-sidecar")

# (method, path pattern) -> signed?
_ROUTES = (
    ("GET", re.compile(r"\A/config\Z"), False),
    ("GET", re.compile(r"\A/me\Z"), True),
    ("DELETE", re.compile(r"\A/me\Z"), True),
    ("POST", re.compile(r"\A/applications\Z"), True),
    ("POST", re.compile(r"\A/payments/\d{4}/invoice\Z"), True),
    ("POST", re.compile(r"\A/payments/\d{4}/refresh\Z"), True),
    ("GET", re.compile(r"\A/payments\Z"), True),
    ("GET", re.compile(r"\A/export\Z"), True),
)
_INVOICE = re.compile(r"\A/payments/\d{4}/invoice\Z")


@dataclass(frozen=True)
class Settings:
    """The sidecar's configuration, read from the environment."""

    api_key: str = ""
    upstream: str = "https://verein.einundzwanzig.space"
    rate_per_minute: int = 60
    invoices_per_day: int = 10

    @classmethod
    def from_env(cls) -> "Settings":
        def number(name: str, default: int) -> int:
            try:
                return max(1, int(os.environ.get(name, default)))
            except ValueError:
                return default
        upstream = os.environ.get("E21_UPSTREAM", cls.upstream).strip().rstrip("/")
        return cls(
            api_key=os.environ.get("E21_API_KEY", "").strip(),
            upstream=upstream or cls.upstream,
            rate_per_minute=number("SIDECAR_RATE_PER_MINUTE", cls.rate_per_minute),
            invoices_per_day=number("SIDECAR_INVOICES_PER_DAY", cls.invoices_per_day),
        )


@dataclass
class _Limits:
    """In-memory limits. A restart resets them, which only ever loosens them
    for a moment; nothing here is worth a database."""

    clock: Callable[[], float]
    per_minute: int
    per_day: int
    _hits: Dict[str, deque] = field(default_factory=lambda: defaultdict(deque))
    _invoices: Dict[Tuple[str, int], int] = field(default_factory=dict)
    _seen: "OrderedDict[str, float]" = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow_request(self, client: str) -> Optional[int]:
        """None when allowed, else seconds to wait."""
        now = self.clock()
        with self._lock:
            hits = self._hits[client]
            while hits and now - hits[0] >= 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return max(1, int(60 - (now - hits[0])) + 1)
            hits.append(now)
            return None

    def allow_invoice(self, pubkey: str) -> Optional[int]:
        now = self.clock()
        day = int(now // 86400)
        with self._lock:
            for key in [k for k in self._invoices if k[1] != day]:
                del self._invoices[key]
            count = self._invoices.get((pubkey, day), 0)
            if count >= self.per_day:
                return int((day + 1) * 86400 - now) + 1
            self._invoices[(pubkey, day)] = count + 1
            return None

    def first_use(self, event_id: str) -> bool:
        now = self.clock()
        with self._lock:
            while self._seen:
                oldest_id, seen_at = next(iter(self._seen.items()))
                if now - seen_at < REPLAY_SECONDS:
                    break
                self._seen.pop(oldest_id)
            if event_id in self._seen:
                return False
            self._seen[event_id] = now
            return True


def _route(method: str, path: str) -> Optional[bool]:
    """Whether (method, path) is forwarded, and whether it must be signed."""
    for verb, pattern, signed in _ROUTES:
        if verb == method and pattern.match(path):
            return signed
    return None


_DIGITS = re.compile(r"\A[0-9]{1,18}\Z")


def _declares_more(content_length: Optional[str], cap: int) -> bool:
    """True when a Content-Length value announces more than ``cap`` bytes.
    A value that is not a plain number counts as more."""
    if content_length is None:
        return False
    value = content_length.strip()
    return not _DIGITS.match(value) or int(value) > cap


class _TooLarge(Exception):
    """A body or an answer over its cap. Reading stopped there."""


class _Unreadable(Exception):
    """An answer in an encoding the sidecar did not ask for and cannot read."""


async def _read_body(request: Request) -> bytes:
    """The request body, read only up to MAX_BODY_BYTES.

    A declared length over the cap is refused before anything is read;
    an undeclared (chunked) body is read in pieces and refused as soon as
    it passes the cap, so no client can make the sidecar hold more."""
    if _declares_more(request.headers.get("content-length"), MAX_BODY_BYTES):
        raise _TooLarge()
    parts, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise _TooLarge()
        parts.append(chunk)
    return b"".join(parts)


def _decoder(answer: httpx.Response):
    """A zlib decompressor for the answer's Content-Encoding, or None for
    an answer sent as is. The sidecar asks for ``identity``; this is for
    an upstream that compresses anyway."""
    encoding = answer.headers.get("content-encoding", "").strip().lower()
    if encoding in ("", "identity"):
        return None
    if encoding in ("gzip", "x-gzip"):
        return zlib.decompressobj(16 + zlib.MAX_WBITS)
    if encoding == "deflate":
        return zlib.decompressobj()
    raise _Unreadable()


async def _read_answer(answer: httpx.Response) -> bytes:
    """The answer's content, decoded, read only up to MAX_ANSWER_BYTES.

    The cap applies to the decoded bytes, and decompression itself is
    bounded, so a small compressed answer cannot unpack into a large one
    in memory."""
    if _declares_more(answer.headers.get("content-length"), MAX_ANSWER_BYTES):
        raise _TooLarge()
    decoder = _decoder(answer)
    parts, size, received = [], 0, 0
    try:
        async for raw in answer.aiter_raw():
            received += len(raw)
            if received > MAX_ANSWER_BYTES:
                raise _TooLarge()
            if decoder is None:
                data = raw
            else:
                data = decoder.decompress(raw, MAX_ANSWER_BYTES + 1 - size)
                if decoder.unconsumed_tail:
                    raise _TooLarge()
            size += len(data)
            if size > MAX_ANSWER_BYTES:
                raise _TooLarge()
            parts.append(data)
        if decoder is not None:
            tail = decoder.flush()
            size += len(tail)
            if size > MAX_ANSWER_BYTES:
                raise _TooLarge()
            parts.append(tail)
    except zlib.error:
        raise _Unreadable() from None
    return b"".join(parts)


def _json(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"message": message, **extra}, status_code=status)


def create_app(settings: Optional[Settings] = None, *,
               transport: Optional[httpx.AsyncBaseTransport] = None,
               clock: Callable[[], float] = time.time) -> FastAPI:
    """The sidecar. ``transport`` and ``clock`` are seams for tests."""
    settings = settings or Settings.from_env()
    limits = _Limits(clock=clock, per_minute=settings.rate_per_minute,
                     per_day=settings.invoices_per_day)
    config_cache: Dict[str, object] = {}
    key_bytes = settings.api_key.encode("utf-8")
    client = httpx.AsyncClient(transport=transport, timeout=UPSTREAM_TIMEOUT_SECONDS,
                               follow_redirects=False,
                               headers={"User-Agent": f"myeditor-sidecar/{VERSION}"})

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await client.aclose()

    app = FastAPI(title="MyEditor membership sidecar", version=VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/status")
    async def status() -> dict:
        return {"service": "myeditor-sidecar", "version": VERSION,
                "membership": bool(settings.api_key)}

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.api_route(API_PREFIX + "/{path:path}", methods=["GET", "POST", "DELETE"])
    async def membership(path: str, request: Request) -> Response:
        started = time.monotonic()
        path = "/" + path
        method = request.method
        client_host = request.client.host if request.client else "unknown"
        outcome = {"pubkey": "-", "status": 0}

        def finish(response: Response) -> Response:
            outcome["status"] = response.status_code
            log.info("%s %s %s %s %.0fms", method, path, response.status_code,
                     outcome["pubkey"][:8], (time.monotonic() - started) * 1000)
            return response

        if not settings.api_key:
            return finish(_json(503, "Joining through this server is not available.",
                                code="not_configured"))
        signed = _route(method, path)
        if signed is None or request.url.query:
            return finish(_json(404, "Not Found"))
        wait = limits.allow_request(client_host)
        if wait is not None:
            response = _json(429, "Too many requests.")
            response.headers["Retry-After"] = str(wait)
            return finish(response)

        try:
            body = await _read_body(request)
        except _TooLarge:
            return finish(_json(413, "Request too large."))
        if body and request.headers.get("content-type") != "application/json":
            return finish(_json(415, "Unsupported Media Type"))
        if not body and method == "GET" and request.headers.get("content-type"):
            return finish(_json(415, "Unsupported Media Type"))

        upstream_url = f"{settings.upstream}{API_PREFIX}{path}"
        authorization = request.headers.get("authorization", "")
        if signed:
            try:
                event = nip98.check_auth_header(authorization, url=upstream_url,
                                                method=method, body=body, now=clock())
            except nip98.AuthRefused as refusal:
                log.info("refused %s %s: %s", method, path, refusal.reason)
                return finish(_json(401, "Unauthenticated."))
            outcome["pubkey"] = event["pubkey"]
            if not limits.first_use(event["id"]):
                log.info("refused %s %s: replay", method, path)
                return finish(_json(401, "Unauthenticated."))
            if method == "POST" and _INVOICE.match(path):
                wait = limits.allow_invoice(event["pubkey"])
                if wait is not None:
                    response = _json(429, "Too many invoices today.")
                    response.headers["Retry-After"] = str(wait)
                    return finish(response)
        elif path == "/config":
            cached = config_cache.get("answer")
            if cached is not None and clock() - config_cache["at"] < CONFIG_CACHE_SECONDS:
                status_code, content, headers = cached
                return finish(Response(content, status_code=status_code, headers=headers))

        headers = {"Accept": "application/json", "Accept-Encoding": "identity",
                   "X-Api-Key": settings.api_key}
        if signed:
            headers["Authorization"] = authorization
        if body:
            headers["Content-Type"] = "application/json"
        try:
            async with client.stream(method, upstream_url, content=body or None,
                                     headers=headers) as answer:
                status_code = answer.status_code
                if 300 <= status_code < 400:
                    return finish(_json(502, "EINUNDZWANZIG answered with a redirect."))
                content = await _read_answer(answer)
                passed = {"Content-Type": answer.headers.get("content-type",
                                                             "application/json")}
                if "retry-after" in answer.headers:
                    passed["Retry-After"] = answer.headers["retry-after"]
        except _TooLarge:
            log.warning("upstream answer too large for %s %s", method, path)
            return finish(_json(502, "EINUNDZWANZIG sent an answer that was too large."))
        except _Unreadable:
            log.warning("upstream answer unreadable for %s %s", method, path)
            return finish(_json(502, "EINUNDZWANZIG sent an answer that could not be read."))
        except httpx.HTTPError as exc:
            log.warning("upstream unreachable for %s %s: %s", method, path,
                        type(exc).__name__)
            return finish(_json(502, "EINUNDZWANZIG is not reachable right now."))

        if key_bytes and key_bytes in content:
            content = content.replace(key_bytes, b"[redacted]")
        if path == "/config" and status_code == 200:
            config_cache["answer"] = (status_code, content, passed)
            config_cache["at"] = clock()
        return finish(Response(content, status_code=status_code, headers=passed))

    return app


logging.basicConfig(level=os.environ.get("SIDECAR_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(message)s")
app = create_app() if os.environ.get("SIDECAR_NO_AUTOSTART") != "1" else None
