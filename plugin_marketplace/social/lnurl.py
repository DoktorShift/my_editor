"""Lightning Address (LUD-16) + LNURL-pay helpers used by the zap flow.

NIP-57 zaps reuse the standard LNURL-pay protocol with one twist:
the wallet adds a ``nostr=<event>`` query parameter carrying the
signed kind:9734 zap request. The LNURL service mints a bolt11
invoice for the requested amount and (after payment) publishes a
kind:9735 zap receipt that references the original zap request.

This module is the wire-side half:

  - ``lightning_address_to_lnurl_endpoint`` — LUD-16 transformation
  - ``fetch_lnurl_pay_data``                — GET LNURL JSON
  - ``LnurlPayData``                        — typed response
  - ``fetch_invoice``                       — POST + parse bolt11

All functions are blocking ``urllib`` calls intended to run on a
worker thread. The UI never calls them directly; the worker pattern
matches the one used by the registry fetcher.

Specs:
  - LUD-16: https://github.com/lnurl/luds/blob/luds/16.md
  - LUD-06: https://github.com/lnurl/luds/blob/luds/06.md
  - LUD-21: https://github.com/lnurl/luds/blob/luds/21.md
  - NIP-57: https://github.com/nostr-protocol/nips/blob/master/57.md
"""

from __future__ import annotations

import hashlib
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import List, Optional


# Generous timeouts so a slow LNURL provider doesn't strand the UI.
_HTTP_TIMEOUT_S: float = 10.0
_MAX_BODY_BYTES: int = 64 * 1024


# ──────────────────────────────────────────────────────────────────────
# Errors
# ──────────────────────────────────────────────────────────────────────

class LnurlError(RuntimeError):
    """LNURL workflow failed. ``message`` is end-user-friendly."""

    KIND_INVALID_ADDRESS = "invalid_address"
    KIND_NETWORK = "network"
    KIND_BAD_RESPONSE = "bad_response"
    KIND_NOT_ZAPPABLE = "not_zappable"
    KIND_AMOUNT = "amount"
    KIND_REJECTED = "rejected"

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


# ──────────────────────────────────────────────────────────────────────
# Lightning Address transformation (LUD-16)
# ──────────────────────────────────────────────────────────────────────

# Same conservative charset as the NIP-05 verifier; LUD-16 is silent
# on charset but every wallet I've inspected restricts to ASCII.
_LOCAL_RE = re.compile(r"^[a-z0-9._-]+$", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"^[a-z0-9._-]+$", re.IGNORECASE)


def lightning_address_to_lnurl_endpoint(address: str) -> str:
    """Convert ``alice@example.com`` into the LNURL-pay endpoint URL.

    Per LUD-16 the transformation is:
        alice@example.com -> https://example.com/.well-known/lnurlp/alice

    Raises ``LnurlError`` for anything that isn't a well-formed
    lightning address, before any network call goes out.
    """
    if not address or "@" not in address:
        raise LnurlError(
            f"{address!r} is not a lightning address.",
            kind=LnurlError.KIND_INVALID_ADDRESS,
        )
    local, _, domain = address.partition("@")
    if not _LOCAL_RE.match(local) or not _DOMAIN_RE.match(domain) or "." not in domain:
        raise LnurlError(
            f"{address!r} is not a valid lightning address.",
            kind=LnurlError.KIND_INVALID_ADDRESS,
        )
    return f"https://{domain.lower()}/.well-known/lnurlp/{local}"


# ──────────────────────────────────────────────────────────────────────
# LNURL-pay response (LUD-06)
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LnurlPayData:
    """Parsed LNURL-pay response.

    Per LUD-06 the response carries:
      - ``callback``    URL to POST/GET for the actual invoice
      - ``minSendable`` / ``maxSendable`` in millisats
      - ``metadata``    JSON-string of LNURL-pay metadata
      - ``tag`` == ``"payRequest"``

    NIP-57 adds two fields:
      - ``allowsNostr`` boolean
      - ``nostrPubkey`` hex pubkey of the LNURL service's Nostr signer
    """

    callback: str
    min_sendable_msat: int
    max_sendable_msat: int
    metadata_raw: str
    allows_nostr: bool
    nostr_pubkey: str   # empty when allows_nostr is False


def fetch_lnurl_pay_data(endpoint_url: str) -> LnurlPayData:
    """GET ``endpoint_url`` and parse the LNURL-pay response."""
    body = _http_get(endpoint_url)
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LnurlError(
            "LNURL endpoint returned invalid JSON.",
            kind=LnurlError.KIND_BAD_RESPONSE,
        ) from exc
    if not isinstance(data, dict) or data.get("tag") != "payRequest":
        raise LnurlError(
            "LNURL endpoint is not a pay request.",
            kind=LnurlError.KIND_BAD_RESPONSE,
        )

    callback = data.get("callback")
    metadata_raw = data.get("metadata")
    min_msat = data.get("minSendable")
    max_msat = data.get("maxSendable")
    if not (
        isinstance(callback, str) and callback.lower().startswith("https://")
        and isinstance(metadata_raw, str)
        and isinstance(min_msat, int) and isinstance(max_msat, int)
    ):
        raise LnurlError(
            "LNURL response is missing required fields.",
            kind=LnurlError.KIND_BAD_RESPONSE,
        )
    if min_msat < 0 or max_msat < min_msat:
        raise LnurlError(
            "LNURL response has invalid amount bounds.",
            kind=LnurlError.KIND_BAD_RESPONSE,
        )

    allows_nostr = bool(data.get("allowsNostr"))
    nostr_pubkey = data.get("nostrPubkey", "") if allows_nostr else ""
    if allows_nostr and (not isinstance(nostr_pubkey, str) or len(nostr_pubkey) != 64):
        # Service claims Nostr support but didn't advertise a pubkey;
        # treat as non-zappable rather than guess.
        allows_nostr = False
        nostr_pubkey = ""

    return LnurlPayData(
        callback=callback,
        min_sendable_msat=min_msat,
        max_sendable_msat=max_msat,
        metadata_raw=metadata_raw,
        allows_nostr=allows_nostr,
        nostr_pubkey=nostr_pubkey.lower() if nostr_pubkey else "",
    )


# ──────────────────────────────────────────────────────────────────────
# Invoice fetch
# ──────────────────────────────────────────────────────────────────────

def fetch_invoice(
    *,
    pay_data: LnurlPayData,
    amount_msat: int,
    zap_request_json: str,
    lnurl_string: str = "",
) -> str:
    """POST to the LNURL callback for a bolt11 invoice.

    Adds the NIP-57 ``nostr=<zap-request>`` query parameter alongside
    the standard LUD-06 ``amount=<msat>``. Returns the bolt11 string.

    LUD-21 (verify URL) is intentionally not requested here; the
    marketplace watches the relays for the zap receipt instead, which
    is the canonical confirmation per NIP-57.
    """
    if amount_msat < pay_data.min_sendable_msat:
        raise LnurlError(
            f"Amount too small. Minimum is {pay_data.min_sendable_msat // 1000} sats.",
            kind=LnurlError.KIND_AMOUNT,
        )
    if amount_msat > pay_data.max_sendable_msat:
        raise LnurlError(
            f"Amount too large. Maximum is {pay_data.max_sendable_msat // 1000} sats.",
            kind=LnurlError.KIND_AMOUNT,
        )

    params = {
        "amount": str(amount_msat),
        "nostr": zap_request_json,
    }
    if lnurl_string:
        params["lnurl"] = lnurl_string

    sep = "&" if "?" in pay_data.callback else "?"
    url = pay_data.callback + sep + urllib.parse.urlencode(params)
    body = _http_get(url)
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LnurlError(
            "Callback response was not valid JSON.",
            kind=LnurlError.KIND_BAD_RESPONSE,
        ) from exc
    if not isinstance(data, dict):
        raise LnurlError(
            "Callback response was not a JSON object.",
            kind=LnurlError.KIND_BAD_RESPONSE,
        )
    if data.get("status") == "ERROR":
        reason = data.get("reason", "wallet rejected the request")
        raise LnurlError(str(reason), kind=LnurlError.KIND_REJECTED)
    bolt11 = data.get("pr")
    if not isinstance(bolt11, str) or not bolt11:
        raise LnurlError(
            "Callback response is missing a bolt11 invoice.",
            kind=LnurlError.KIND_BAD_RESPONSE,
        )
    return bolt11


# ──────────────────────────────────────────────────────────────────────
# Low-level HTTP
# ──────────────────────────────────────────────────────────────────────

def _http_get(url: str) -> bytes:
    """Tight GET with size and timeout caps.

    Mirrors the safety bounds the registry fetcher uses so LNURL
    responses can't blow up the worker thread's memory.
    """
    if not url.lower().startswith("https://"):
        raise LnurlError(
            "LNURL endpoints must be HTTPS.",
            kind=LnurlError.KIND_BAD_RESPONSE,
        )
    ctx = ssl.create_default_context()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "my-editor-plugin-marketplace/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S, context=ctx) as resp:
            if resp.status != 200:
                raise LnurlError(
                    f"LNURL service returned HTTP {resp.status}.",
                    kind=LnurlError.KIND_NETWORK,
                )
            body = resp.read(_MAX_BODY_BYTES + 1)
            if len(body) > _MAX_BODY_BYTES:
                raise LnurlError(
                    "LNURL response exceeded size cap.",
                    kind=LnurlError.KIND_BAD_RESPONSE,
                )
            return body
    except urllib.error.HTTPError as exc:
        raise LnurlError(
            f"LNURL service returned HTTP {exc.code} {exc.reason}.",
            kind=LnurlError.KIND_NETWORK,
        ) from exc
    except urllib.error.URLError as exc:
        raise LnurlError(
            "Couldn't reach the LNURL service. Check your connection.",
            kind=LnurlError.KIND_NETWORK,
        ) from exc
    except (TimeoutError, OSError, ssl.SSLError) as exc:
        raise LnurlError(
            "LNURL service didn't respond in time.",
            kind=LnurlError.KIND_NETWORK,
        ) from exc


# ──────────────────────────────────────────────────────────────────────
# NIP-57 zap request builder
# ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class InvoiceWithVerify:
    """A bolt11 invoice paired with an optional LUD-21 verify URL.

    Returned by ``fetch_paid_invoice`` so callers that aren't running
    a NIP-57 zap flow (e.g. paid plugin installs) can poll the LNURL
    service for settlement without waiting for a Nostr zap receipt.
    """

    bolt11: str
    verify_url: Optional[str]


def fetch_paid_invoice(
    *,
    pay_data: LnurlPayData,
    amount_msat: int,
    comment: str = "",
) -> InvoiceWithVerify:
    """LNURL-pay callback for the paid-install flow.

    Distinct from ``fetch_invoice`` (which is zap-shaped, requiring a
    NIP-57 ``nostr`` request param) — this is the plain LUD-06 flow
    used for non-zap purchases. We capture the ``verify`` URL when the
    service advertises it (LUD-21) so the marketplace can confirm
    settlement before unlocking the download.
    """
    if amount_msat < pay_data.min_sendable_msat:
        raise LnurlError(
            f"Amount too small. Minimum is {pay_data.min_sendable_msat // 1000} sats.",
            kind=LnurlError.KIND_AMOUNT,
        )
    if amount_msat > pay_data.max_sendable_msat:
        raise LnurlError(
            f"Amount too large. Maximum is {pay_data.max_sendable_msat // 1000} sats.",
            kind=LnurlError.KIND_AMOUNT,
        )
    params = {"amount": str(amount_msat)}
    if comment:
        # LUD-12 (commentAllowed) is best-effort; the service may
        # ignore the param, which is fine. We trim to a conservative
        # 144 chars so we don't bomb services with strict caps.
        params["comment"] = comment[:144]
    sep = "&" if "?" in pay_data.callback else "?"
    url = pay_data.callback + sep + urllib.parse.urlencode(params)
    body = _http_get(url)
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LnurlError(
            "Callback response was not valid JSON.",
            kind=LnurlError.KIND_BAD_RESPONSE,
        ) from exc
    if not isinstance(data, dict):
        raise LnurlError(
            "Callback response was not a JSON object.",
            kind=LnurlError.KIND_BAD_RESPONSE,
        )
    if data.get("status") == "ERROR":
        reason = data.get("reason", "wallet rejected the request")
        raise LnurlError(str(reason), kind=LnurlError.KIND_REJECTED)
    bolt11 = data.get("pr")
    if not isinstance(bolt11, str) or not bolt11:
        raise LnurlError(
            "Callback response is missing a bolt11 invoice.",
            kind=LnurlError.KIND_BAD_RESPONSE,
        )
    verify_raw = data.get("verify")
    verify_url: Optional[str] = None
    if isinstance(verify_raw, str) and verify_raw.strip().lower().startswith("https://"):
        verify_url = verify_raw.strip()
    return InvoiceWithVerify(bolt11=bolt11, verify_url=verify_url)


@dataclass(frozen=True)
class VerifyResult:
    """Settlement state from a LUD-21 verify call."""
    settled: bool
    preimage: str   # empty when not settled
    bolt11: str     # the invoice the service confirms


def check_invoice_settled(verify_url: str) -> VerifyResult:
    """Poll a LUD-21 verify URL.

    Returns ``settled=False`` while the invoice is still open;
    ``settled=True`` once the payment lands. Raises ``LnurlError`` for
    transport failures so the UI can render a sensible retry prompt.
    """
    body = _http_get(verify_url)
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LnurlError(
            "Verify response was not valid JSON.",
            kind=LnurlError.KIND_BAD_RESPONSE,
        ) from exc
    if not isinstance(data, dict):
        raise LnurlError(
            "Verify response was not a JSON object.",
            kind=LnurlError.KIND_BAD_RESPONSE,
        )
    if data.get("status") == "ERROR":
        raise LnurlError(
            str(data.get("reason") or "verify request failed"),
            kind=LnurlError.KIND_NETWORK,
        )
    settled = bool(data.get("settled"))
    preimage_raw = data.get("preimage") or ""
    preimage = preimage_raw if isinstance(preimage_raw, str) else ""
    bolt11_raw = data.get("pr") or ""
    bolt11 = bolt11_raw if isinstance(bolt11_raw, str) else ""
    return VerifyResult(settled=settled, preimage=preimage, bolt11=bolt11)


def build_zap_request_tags(
    *,
    recipient_pubkey: str,
    amount_msat: int,
    relays: List[str],
    plugin_anchor: Optional[str] = None,
) -> List[List[str]]:
    """Return the tag set for a kind:9734 zap request.

    NIP-57 requires:
      - ``relays``  one tag listing every relay the receipt should
        be published to. Single tag, multiple values.
      - ``amount``  millisats as a decimal string.
      - ``p``       recipient pubkey.

    The plugin anchor is added as an ``a`` tag so the marketplace's
    engagement fetcher (filter ``#a``) picks up the receipt for the
    right plugin.
    """
    if amount_msat <= 0:
        raise LnurlError(
            "Zap amount must be positive.",
            kind=LnurlError.KIND_AMOUNT,
        )
    if len(recipient_pubkey) != 64:
        raise LnurlError(
            "Recipient pubkey must be 64-char hex.",
            kind=LnurlError.KIND_BAD_RESPONSE,
        )
    # Deduplicate relays while preserving order.
    seen: dict[str, None] = {}
    for url in relays:
        if url:
            seen.setdefault(url, None)
    tags: List[List[str]] = [
        ["relays"] + list(seen.keys()),
        ["amount", str(amount_msat)],
        ["p", recipient_pubkey.lower()],
    ]
    if plugin_anchor:
        tags.append(["a", plugin_anchor])
    return tags
