"""Minimal BOLT-11 decoder for NIP-57 zap receipt validation.

The marketplace only needs two facts from a bolt11 invoice:

  - the **amount** in millisats (so we can verify it matches the zap
    request's ``amount`` tag, per NIP-57).
  - the **description hash** (the bolt11 tagged field ``h``, 5-bit
    type 23), which the LNURL service computes as
    ``sha256(<zap-request-json>)``. Equality between this hash and
    ``sha256(description_tag_value)`` is the binding that ties the
    9735 receipt to the 9734 request the marketplace originally sent.

That's it. We deliberately do NOT verify the bolt11 signature, parse
routing hints, or extract the payment hash — the relay-published zap
receipt is the source of truth for "this was paid". The numbers we
extract here are *cross-checks* against the receipt, nothing more.

References:
  - BOLT-11: https://github.com/lightning/bolts/blob/master/11-payment-encoding.md
  - NIP-57:  https://github.com/nostr-protocol/nips/blob/master/57.md
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from nostr.bech32 import bech32_decode


@dataclass(frozen=True)
class Bolt11Summary:
    """The subset of a bolt11 invoice we need for zap verification.

    ``amount_msat`` is ``None`` when the invoice was minted without a
    fixed amount (BOLT-11 lets the HRP omit the amount entirely, in
    which case any value is acceptable at pay time). We surface it as
    ``None`` rather than zero so callers can distinguish "any" from
    "zero sats".

    ``description_hash`` is 32 raw bytes, lowercased hex-encoded as
    ``hex_description_hash`` for ergonomic comparison. ``None`` when
    the invoice uses the inline description (tagged field ``d``)
    instead of the hash (``h``). NIP-57 mandates the hash form, so a
    missing hash on a zap-claiming receipt is itself a validation
    failure.
    """

    amount_msat: Optional[int]
    hex_description_hash: Optional[str]


class Bolt11Error(ValueError):
    """Raised when the invoice can't be parsed in the slice we support."""


# BOLT-11 multiplier table: msat-per-1-of-the-prefix-unit.
# The HRP encodes amount in BTC: 1 BTC = 100_000_000_000 msat.
_MULTIPLIER_TO_MSAT_PER_UNIT = {
    "m": 100_000_000,        # milli  -> 1e-3 BTC == 1e8 msat
    "u": 100_000,            # micro  -> 1e-6 BTC == 1e5 msat
    "n": 100,                # nano   -> 1e-9 BTC == 1e2 msat
    "p": None,               # pico   -> 1e-12 BTC == 0.1 msat (must be * 10)
}

# Bolt11 HRP: ``ln`` + chain prefix (``bc``/``tb``/``bcrt``/``sb``) + optional
# amount + optional multiplier. We only parse the amount slot here; the
# chain prefix is checked just enough to reject obvious garbage like
# someone pasting an LNURL or naddr into the receipt's ``bolt11`` tag.
_KNOWN_CHAIN_PREFIXES = ("bc", "tb", "bcrt", "sb")
_HRP_RE = re.compile(r"^ln([a-z]+?)(\d+)?([munp])?$")


def decode_bolt11(invoice: str) -> Bolt11Summary:
    """Parse the HRP amount and the optional ``h`` field from a bolt11.

    Returns a ``Bolt11Summary``. Raises ``Bolt11Error`` if the input
    isn't a syntactically valid bolt11 (so callers can surface a
    user-friendly "invoice malformed" error rather than ``None``).
    """
    if not isinstance(invoice, str) or not invoice:
        raise Bolt11Error("empty invoice")
    invoice = invoice.strip().lower()

    try:
        hrp, data = bech32_decode(invoice)
    except ValueError as exc:
        raise Bolt11Error(f"bolt11 not bech32: {exc}") from exc

    amount_msat = _amount_msat_from_hrp(hrp)
    description_hash_hex = _description_hash_from_data(data)
    return Bolt11Summary(
        amount_msat=amount_msat,
        hex_description_hash=description_hash_hex,
    )


def _amount_msat_from_hrp(hrp: str) -> Optional[int]:
    """Parse ``lnbc100u`` style HRPs into millisats.

    Returns ``None`` for amount-less invoices ("any amount"). Raises
    ``Bolt11Error`` for anything that doesn't match the HRP shape.
    """
    match = _HRP_RE.match(hrp)
    if not match:
        raise Bolt11Error(f"invalid bolt11 HRP: {hrp!r}")
    chain, amount_part, multiplier = match.groups()
    if chain not in _KNOWN_CHAIN_PREFIXES:
        raise Bolt11Error(f"unknown bolt11 chain prefix: {chain!r}")
    if amount_part is None:
        return None
    try:
        amount = int(amount_part)
    except ValueError as exc:
        raise Bolt11Error(f"invalid bolt11 amount: {amount_part!r}") from exc
    if multiplier is None:
        # Bare integer == BTC. 1 BTC == 1e11 msat.
        return amount * 100_000_000_000
    if multiplier == "p":
        # Pico-BTC == 1e-12 BTC == 0.1 msat. BOLT-11 requires that the
        # encoded amount with the ``p`` multiplier always be a multiple
        # of 10 so the resulting msat value is integral.
        if amount % 10 != 0:
            raise Bolt11Error(
                f"pico-BTC bolt11 amount {amount!r} must be a multiple of 10"
            )
        return amount // 10
    msat_per_unit = _MULTIPLIER_TO_MSAT_PER_UNIT[multiplier]
    return amount * msat_per_unit  # type: ignore[operator]


def _description_hash_from_data(data) -> Optional[str]:
    """Extract the tagged ``h`` field (type 23) from the 5-bit stream.

    BOLT-11 data after the HRP looks like:

        <timestamp: 7 5-bit groups>
        ( <tag: 1 5-bit group> <len: 2 5-bit groups> <value: len 5-bit groups> )*
        <signature: 104 5-bit groups>

    We walk the tagged fields, find type 23 (``h`` description hash),
    and return its 32-byte value as lowercase hex. Any other tag is
    skipped. If no ``h`` tag is present we return ``None``.
    """
    if len(data) < 7 + 104:
        # Not enough room for the mandatory timestamp + signature.
        return None
    # Skip the 7-group (35-bit) timestamp.
    cursor = 7
    end = len(data) - 104  # last 104 groups are the signature
    while cursor + 3 <= end:
        tag_type = data[cursor]
        length_hi = data[cursor + 1]
        length_lo = data[cursor + 2]
        cursor += 3
        length = length_hi * 32 + length_lo
        if cursor + length > end:
            # Malformed but recoverable: bail rather than over-read.
            return None
        if tag_type == 23 and length == 52:
            # 52 groups * 5 bits == 260 bits; the field is 256 bits of
            # data plus 4 zero-padding bits. Repack into bytes.
            sliced = data[cursor : cursor + length]
            packed = _bits_5_to_8(sliced)
            if packed is None or len(packed) < 32:
                return None
            return bytes(packed[:32]).hex()
        cursor += length
    return None


def _bits_5_to_8(data) -> Optional[list]:
    """Repack 5-bit groups into 8-bit bytes. Mirrors the BOLT-11 wire
    behaviour: ignore trailing zero-padding bits."""
    acc = 0
    bits = 0
    out: list = []
    for value in data:
        if value < 0 or value > 31:
            return None
        acc = (acc << 5) | value
        bits += 5
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return out


# --------------------------------------------------------------------------- #
# Convenience comparators                                                     #
# --------------------------------------------------------------------------- #

def amounts_match(invoice_msat: Optional[int], expected_msat: int) -> bool:
    """Strict amount comparison with one exception per BOLT-11.

    ``invoice_msat is None`` means the bolt11 was minted as "any
    amount." NIP-57 wallets pay these, so we treat ``None`` as
    matching whatever the zap request asked for.
    """
    if invoice_msat is None:
        return True
    return invoice_msat == expected_msat
