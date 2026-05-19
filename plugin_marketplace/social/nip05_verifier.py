"""NIP-05 verification worker.

Per NIP-05, a user's profile metadata (kind:0) may contain a
``nip05`` claim of the shape ``localpart@domain``. To verify the
claim we GET ``https://<domain>/.well-known/nostr.json?name=<localpart>``
and check the response maps ``localpart`` to the user's pubkey.

This module performs the check off the UI thread and persists the
result into the engagement cache with a 24h TTL. The aggregator
reads ``verified_pubkeys()`` from the cache to apply the trust
filter; the UI reads per-pubkey status to render the verified
badge.

Spec: https://github.com/nostr-protocol/nips/blob/master/05.md
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.request
from typing import Dict, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from .cache import EngagementCache
from .models import Nip05Verification


# NIP-05 explicitly limits the local part to ``[a-z0-9-_.]+``. We
# enforce the same charset on parse so a hostile claim can't sneak
# odd Unicode into our URL.
_LOCAL_RE = re.compile(r"^[a-z0-9._-]+$", re.IGNORECASE)
# Same idea on the domain side: minimal RFC-1035-ish charset.
_DOMAIN_RE = re.compile(r"^[a-z0-9._-]+$", re.IGNORECASE)

# Hard cap on the .well-known response. NIP-05 servers serve a tiny
# JSON document; anything larger than 64 KiB is either a misconfigured
# domain or hostile, and we don't want to be DOSed by either.
_MAX_BODY_BYTES: int = 64 * 1024

_CONNECT_TIMEOUT_S: float = 6.0


# ──────────────────────────────────────────────────────────────────────
# Verifier
# ──────────────────────────────────────────────────────────────────────

class Nip05Verifier(QObject):
    """Schedules NIP-05 verifications on a thread-pool worker.

    Concurrent calls for the same pubkey/identifier are coalesced so
    a list of fifty comments by the same author triggers one fetch,
    not fifty.
    """

    verification_changed = Signal(str)   # pubkey

    def __init__(
        self,
        cache: EngagementCache,
        *,
        thread_pool: Optional[QThreadPool] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._cache = cache
        self._pool = thread_pool or QThreadPool.globalInstance()
        # Track in-flight verifications so we don't fire the same fetch
        # twice; key is ``f"{pubkey}|{identifier}"``.
        self._inflight: Dict[str, None] = {}

    # ----------------------------------------------------------------------
    def request(self, pubkey: str, identifier: str) -> Optional[Nip05Verification]:
        """Ensure ``(pubkey, identifier)`` is verified, fetch if stale.

        Returns the cached verification if any. Schedules a background
        refresh when the cache row is missing or older than the TTL.
        """
        existing = self._cache.get_nip05(pubkey, identifier)
        if existing is not None and self._cache.nip05_is_fresh(existing):
            return existing
        if not _looks_like_identifier(identifier):
            # Refuse to schedule a fetch for an obviously malformed
            # claim. Record the negative so we don't retry it.
            negative = Nip05Verification(
                pubkey=pubkey, identifier=identifier,
                verified=False, checked_at=int(time.time()),
            )
            self._cache.store_nip05(negative)
            return negative

        key = f"{pubkey}|{identifier}"
        if key in self._inflight:
            return existing
        self._inflight[key] = None

        worker = _VerificationWorker(pubkey=pubkey, identifier=identifier)
        worker.signals.done.connect(self._on_worker_done)
        worker.signals.done.connect(lambda *_a, k=key: self._inflight.pop(k, None))
        self._pool.start(worker)
        return existing

    # ----------------------------------------------------------------------
    def _on_worker_done(self, pubkey: str, identifier: str, verified: bool) -> None:
        self._cache.store_nip05(Nip05Verification(
            pubkey=pubkey, identifier=identifier,
            verified=verified, checked_at=int(time.time()),
        ))
        self.verification_changed.emit(pubkey)


# ──────────────────────────────────────────────────────────────────────
# Worker
# ──────────────────────────────────────────────────────────────────────

class _VerificationSignals(QObject):
    done = Signal(str, str, bool)   # pubkey, identifier, verified


class _VerificationWorker(QRunnable):
    """One HTTPS fetch + verification check, off the UI thread."""

    def __init__(self, *, pubkey: str, identifier: str) -> None:
        super().__init__()
        self._pubkey = pubkey
        self._identifier = identifier
        self.signals = _VerificationSignals()

    def run(self) -> None:  # noqa: D401
        verified = False
        try:
            verified = _check_nip05(self._pubkey, self._identifier)
        except Exception:  # noqa: BLE001 — failures = unverified, not a crash
            verified = False
        self.signals.done.emit(self._pubkey, self._identifier, verified)


# ──────────────────────────────────────────────────────────────────────
# Low-level check
# ──────────────────────────────────────────────────────────────────────

def _check_nip05(pubkey: str, identifier: str) -> bool:
    """Return True if ``identifier`` resolves to ``pubkey`` via NIP-05.

    The response shape per spec is:

        {"names": {"<localpart>": "<pubkey-hex>", ...},
         "relays": {"<pubkey-hex>": ["wss://...", ...], ...}}

    We only care about ``names``. ``pubkey`` may appear as either
    lowercase or mixed-case hex; NIP-05 specifies hex but doesn't
    pin case, so we normalize on both sides before comparing.
    """
    local, _, domain = identifier.partition("@")
    if not local or not domain:
        return False
    if not _LOCAL_RE.match(local) or not _DOMAIN_RE.match(domain):
        return False

    url = f"https://{domain}/.well-known/nostr.json?name={local}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "my-editor-plugin-marketplace/1.0",
            "Accept": "application/json",
        },
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(
            request, timeout=_CONNECT_TIMEOUT_S, context=ctx,
        ) as resp:
            if resp.status != 200:
                return False
            body = resp.read(_MAX_BODY_BYTES + 1)
            if len(body) > _MAX_BODY_BYTES:
                return False
    except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError):
        return False

    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    names = data.get("names")
    if not isinstance(names, dict):
        return False
    claimed = names.get(local)
    if not isinstance(claimed, str):
        return False
    return claimed.lower() == pubkey.lower()


def _looks_like_identifier(identifier: str) -> bool:
    """Cheap pre-check so we don't fire HTTPS for impossible claims."""
    if not identifier or "@" not in identifier:
        return False
    local, _, domain = identifier.partition("@")
    return bool(local and domain and "." in domain)
