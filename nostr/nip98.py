# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""NIP-98 HTTP Auth: the credential that says who a request is made for.

Spec: https://github.com/nostr-protocol/nips/blob/master/98.md

A NIP-98 credential is a signed kind 27235 event bound to exactly one
HTTP request: its ``u`` tag names the absolute URL, its ``method`` tag
the verb, and when a body is sent its ``payload`` tag is the SHA-256 of
those exact bytes. The server recomputes all three from the request it
received, so the credential is worthless for any other request.

The EINUNDZWANZIG membership API is strict about every part of that,
and each rule below exists because the server checks it:

- ``u`` is compared byte for byte. It is passed through untouched here;
  normalising it (a trailing slash, a re-encoded query) would produce a
  credential for a different URL.
- ``payload`` is the lowercase hex SHA-256 of the raw bytes sent, so the
  caller hashes and sends the same ``bytes`` object, never a second
  encoding of the same JSON.
- A request without a body carries no ``payload`` tag and no
  Content-Type. An empty body counts as no body: it is never sent, so
  there is nothing to hash.
- ``created_at`` must be within 60 seconds of server time in either
  direction, so it is the caller's "now" exactly, not backdated the way
  Blossom tokens are. Backdating would only spend the window.
- Every event id is accepted once. A ``nonce`` tag makes two requests
  built in the same second for the same URL produce different ids. The
  server ignores tags other than ``u``, ``method`` and ``payload``, and
  its own documentation recommends exactly this nonce, so it costs
  nothing and prevents a double-click from being refused as a replay.

Pure on purpose: no Qt, no clock, no network. Signing is the caller's
job (a NIP-46 remote signer in this app).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from typing import Optional
from urllib.parse import urlsplit


# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #

NIP98_KIND: int = 27235

# The only Content-Type a body may carry. The server refuses anything else
# with 415, because a body the framework has already parsed can no longer
# be checked against the payload hash.
JSON_CONTENT_TYPE: str = "application/json"

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
_HEX128 = re.compile(r"\A[0-9a-f]{128}\Z")


# --------------------------------------------------------------------------- #
# Building the unsigned event                                                  #
# --------------------------------------------------------------------------- #

def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 of ``data``."""
    return hashlib.sha256(bytes(data)).hexdigest()


def new_nonce() -> str:
    """A fresh random nonce for one credential."""
    return secrets.token_hex(16)


def has_body(body: Optional[bytes]) -> bool:
    """True when ``body`` is something that will actually be sent."""
    return body is not None and len(body) > 0


def build_unsigned_auth_event(
    url: str,
    method: str,
    body: Optional[bytes],
    *,
    now: int,
    nonce: Optional[str] = None,
) -> dict:
    """The unsigned kind 27235 event for one request.

    ``url`` must be the absolute URL exactly as it will be requested.
    ``body`` is the exact bytes that will be sent, or None (or empty)
    for a request without one. ``now`` is the current unix time; it is a
    parameter so no caller and no test depends on the wall clock.
    ``nonce`` exists for tests; leave it out and a random one is used.

    Raises ValueError for a URL that is not absolute http(s) or an empty
    method. Both are programming errors, not runtime conditions.
    """
    if not isinstance(url, str):
        raise ValueError("url must be a string")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("url must be an absolute http(s) URL")
    verb = str(method or "").strip().upper()
    if not verb:
        raise ValueError("method must not be empty")

    tags = [
        ["u", url],
        ["method", verb],
        ["nonce", nonce if nonce is not None else new_nonce()],
    ]
    if has_body(body):
        tags.append(["payload", sha256_hex(body)])

    return {
        "kind": NIP98_KIND,
        "created_at": int(now),
        "tags": tags,
        "content": "",
    }


# --------------------------------------------------------------------------- #
# Checking what the signer handed back                                         #
# --------------------------------------------------------------------------- #

def signed_event_problem(signed: object, unsigned: dict) -> Optional[str]:
    """Why ``signed`` cannot be sent as the credential for ``unsigned``.

    None when it can. A remote signer is another program, so its answer
    is checked before it is spent on a request: the server answers every
    failed check with the same 401, and a credential that was broken on
    arrival would read as "the server refused you" rather than "the
    signer returned something unusable".

    The id, pubkey and signature must be lowercase hex because the
    server refuses uppercase outright. The Schnorr signature itself is
    not re-verified here: the NIP-46 client already does that, and the
    server is the authority on it.
    """
    if not isinstance(signed, dict):
        return "the signer returned something that is not an event"
    if signed.get("kind") != NIP98_KIND:
        return "the signer changed the event kind"
    if signed.get("tags") != unsigned.get("tags"):
        return "the signer changed the event tags"
    if signed.get("content", None) != unsigned.get("content", ""):
        return "the signer changed the event content"
    created_at = signed.get("created_at")
    if not isinstance(created_at, int) or isinstance(created_at, bool):
        return "the signer returned an event without a valid time"
    for field, pattern in (("id", _HEX64), ("pubkey", _HEX64), ("sig", _HEX128)):
        value = signed.get(field)
        if not isinstance(value, str) or not pattern.match(value):
            return f"the signer returned an event with a malformed {field}"
    return None


# --------------------------------------------------------------------------- #
# The header                                                                   #
# --------------------------------------------------------------------------- #

def authorization_header_value(signed_event: dict) -> bytes:
    """``Nostr <base64(json)>`` for the Authorization header.

    Standard base64 with padding, which is what the server decodes (its
    own example uses ``btoa``). This is NOT the base64url form Blossom
    uses, so the two helpers are deliberately separate. The JSON is the
    compact form with non-ASCII kept as UTF-8, the same bytes the event
    id was computed over.
    """
    payload = json.dumps(signed_event, separators=(",", ":"), ensure_ascii=False)
    return b"Nostr " + base64.b64encode(payload.encode("utf-8"))
