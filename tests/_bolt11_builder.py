"""BOLT-11 test fixture builder.

Tests need bolt11 invoices that the real decoder will accept. Real
bolt11s are bech32-encoded, signed by a routing node, and embed a
SHA-256 description hash. We don't need a *valid* signature for parser
tests — just the right *shape*: a parseable HRP with our amount, a
tagged ``h`` field with our description hash, and a 65-byte placeholder
signature. The amount and description-hash extractors don't verify
signatures or routing hops, so a synthetic invoice round-trips fine.

Used only from tests. Production code never builds bolt11s.
"""

from __future__ import annotations

import hashlib
from typing import List, Optional

from nostr.bech32 import bech32_encode


_BTC_MSAT = 100_000_000_000   # 1 BTC == 1e11 msat
_MULTIPLIERS = (
    # (suffix, msat-per-unit). Order picks the most compact form for a
    # given amount. ``""`` is the no-suffix case (whole BTC).
    ("p", None),  # special-cased
    ("n", 100),
    ("u", 100_000),
    ("m", 100_000_000),
    ("", _BTC_MSAT),
)


def _encode_amount(msat: int) -> str:
    """Pick a compact HRP amount slot for ``msat``.

    BOLT-11 allows any of the four multipliers (m/u/n/p) or none.
    Picks the largest unit that still expresses ``msat`` exactly.
    """
    if msat <= 0:
        raise ValueError("msat must be positive")
    # Pico path first: ``p`` units are 0.1 msat, so they always exist
    # but must be multiples of 10.
    if msat % _BTC_MSAT == 0:
        return f"{msat // _BTC_MSAT}"
    for suffix, per_unit in _MULTIPLIERS[1:]:   # skip ``p`` special-case
        if per_unit is None:
            continue
        if msat % per_unit == 0:
            return f"{msat // per_unit}{suffix}"
    # Fallback to ``p`` (must be * 10 of msat).
    return f"{msat * 10}p"


def _bits_8_to_5(data: bytes) -> List[int]:
    """Repack 8-bit bytes into 5-bit groups for bech32 encoding."""
    acc = 0
    bits = 0
    out: List[int] = []
    for byte in data:
        acc = (acc << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            out.append((acc >> bits) & 0x1F)
    if bits > 0:
        out.append((acc << (5 - bits)) & 0x1F)
    return out


def build_test_bolt11(
    *,
    description_hash_hex: Optional[str],
    amount_msat: Optional[int] = 21_000,
    chain_prefix: str = "bc",
) -> str:
    """Return a bech32-encoded fixture bolt11 invoice.

    The invoice is well-formed as far as the marketplace's decoder
    cares: HRP carries the amount + chain prefix; the data carries a
    7-group timestamp, an ``h`` (type 23) description hash field, and
    a 104-group placeholder signature.

    Pass ``description_hash_hex=None`` to omit the description-hash
    tag (the parser should then reject the receipt).
    Pass ``amount_msat=None`` to mint an "any amount" invoice.
    """
    hrp = "ln" + chain_prefix
    if amount_msat is not None:
        hrp += _encode_amount(amount_msat)

    # Data section: 7 groups of timestamp + tagged fields + 104 signature groups.
    data: List[int] = []
    # Timestamp: zero is fine for parser tests.
    data.extend([0] * 7)
    if description_hash_hex is not None:
        raw = bytes.fromhex(description_hash_hex)
        if len(raw) != 32:
            raise ValueError("description hash must be 32 bytes")
        groups = _bits_8_to_5(raw)
        # 256 bits -> 52 groups (the last group has 4 bits of padding).
        if len(groups) != 52:
            raise ValueError("re-packing yielded unexpected number of groups")
        # Tag: type 23 (description hash), length = 52 == 1*32 + 20.
        data.append(23)
        data.append(1)    # length high
        data.append(20)   # length low
        data.extend(groups)
    # 104 signature groups (placeholder zeros).
    data.extend([0] * 104)
    return bech32_encode(hrp, data)


def description_hash_for(description: str) -> str:
    """Convenience: sha256(description) as lowercase hex."""
    return hashlib.sha256(description.encode("utf-8")).hexdigest()
