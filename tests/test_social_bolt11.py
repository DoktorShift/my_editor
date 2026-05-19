"""Tests for the minimal BOLT-11 decoder used by NIP-57 validation."""

from __future__ import annotations

import pytest

from plugin_marketplace.social.bolt11 import (
    Bolt11Error,
    amounts_match,
    decode_bolt11,
)
from tests._bolt11_builder import build_test_bolt11, description_hash_for


_DESC_HASH = description_hash_for("hello world")


# ──────────────────────────────────────────────────────────────────────
# Amount decoding

def test_decode_extracts_amount_for_each_multiplier():
    for msat, suffix in [(1_000, "u"), (1_000_000, "m"), (100, "n")]:
        invoice = build_test_bolt11(
            amount_msat=msat, description_hash_hex=_DESC_HASH,
        )
        # encoder picks the most compact form, so we only assert the
        # decoded amount, not the chosen suffix.
        del suffix
        summary = decode_bolt11(invoice)
        assert summary.amount_msat == msat


def test_decode_returns_none_for_amount_less_invoice():
    invoice = build_test_bolt11(
        amount_msat=None, description_hash_hex=_DESC_HASH,
    )
    summary = decode_bolt11(invoice)
    assert summary.amount_msat is None


def test_decode_rejects_garbage():
    with pytest.raises(Bolt11Error):
        decode_bolt11("not-a-bolt11")


def test_decode_rejects_empty():
    with pytest.raises(Bolt11Error):
        decode_bolt11("")


def test_decode_rejects_non_bech32():
    with pytest.raises(Bolt11Error):
        decode_bolt11("lnbc100u" + "*" * 50)


# ──────────────────────────────────────────────────────────────────────
# Description hash extraction

def test_decode_extracts_description_hash():
    invoice = build_test_bolt11(
        amount_msat=21_000, description_hash_hex=_DESC_HASH,
    )
    summary = decode_bolt11(invoice)
    assert summary.hex_description_hash == _DESC_HASH


def test_decode_returns_none_when_no_h_field():
    invoice = build_test_bolt11(
        amount_msat=21_000, description_hash_hex=None,
    )
    summary = decode_bolt11(invoice)
    assert summary.hex_description_hash is None


# ──────────────────────────────────────────────────────────────────────
# amounts_match

def test_amounts_match_for_equal_values():
    assert amounts_match(21_000, 21_000)


def test_amounts_match_treats_none_as_any():
    assert amounts_match(None, 100) is True
    assert amounts_match(None, 0) is True


def test_amounts_match_rejects_mismatch():
    assert amounts_match(21_000, 42_000) is False
