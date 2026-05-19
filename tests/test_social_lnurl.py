"""Tests for the LUD-16 + LNURL-pay + NIP-57 helpers.

These cover the pure-Python pieces of the zap pipeline: address
normalization, response parsing, request-builder tags. The HTTP
shim is exercised via a monkey-patched ``urllib.request.urlopen``.
"""

from __future__ import annotations

import io
import json

import pytest

from plugin_marketplace.social import (
    LnurlError,
    LnurlPayData,
    build_zap_request_tags,
    fetch_invoice,
    fetch_lnurl_pay_data,
    lightning_address_to_lnurl_endpoint,
)


RECIPIENT_PUBKEY = "a" * 64


# ──────────────────────────────────────────────────────────────────────
# lightning_address_to_lnurl_endpoint

def test_endpoint_is_well_known_lnurlp():
    url = lightning_address_to_lnurl_endpoint("alice@example.com")
    assert url == "https://example.com/.well-known/lnurlp/alice"


def test_endpoint_lowercases_domain():
    url = lightning_address_to_lnurl_endpoint("Alice@Example.COM")
    assert url == "https://example.com/.well-known/lnurlp/Alice"


@pytest.mark.parametrize("address", [
    "",
    "no-at-sign",
    "@example.com",
    "alice@",
    "alice@no-tld",
    "alice space@example.com",
    "alice@bad host.com",
])
def test_endpoint_rejects_malformed_address(address):
    with pytest.raises(LnurlError) as excinfo:
        lightning_address_to_lnurl_endpoint(address)
    assert excinfo.value.kind == LnurlError.KIND_INVALID_ADDRESS


# ──────────────────────────────────────────────────────────────────────
# fetch_lnurl_pay_data

class _FakeResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._buf = io.BytesIO(payload)
        self.status = status

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _good_payload(**overrides) -> bytes:
    body = {
        "tag": "payRequest",
        "callback": "https://example.com/callback",
        "minSendable": 1000,
        "maxSendable": 1000000,
        "metadata": "[]",
        "allowsNostr": True,
        "nostrPubkey": "b" * 64,
    }
    body.update(overrides)
    return json.dumps(body).encode("utf-8")


def _patch_urlopen(monkeypatch, payload: bytes, *, status: int = 200):
    captured: dict = {}

    def fake_urlopen(request, *, timeout, context):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return _FakeResponse(payload, status=status)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return captured


def test_fetch_lnurl_pay_data_parses_minimum_fields(monkeypatch):
    _patch_urlopen(monkeypatch, _good_payload())
    data = fetch_lnurl_pay_data("https://example.com/.well-known/lnurlp/alice")
    assert isinstance(data, LnurlPayData)
    assert data.callback == "https://example.com/callback"
    assert data.min_sendable_msat == 1000
    assert data.max_sendable_msat == 1000000
    assert data.allows_nostr is True
    assert data.nostr_pubkey == "b" * 64


def test_fetch_lnurl_pay_data_rejects_http_endpoint(monkeypatch):
    _patch_urlopen(monkeypatch, _good_payload())
    with pytest.raises(LnurlError) as excinfo:
        fetch_lnurl_pay_data("http://example.com/.well-known/lnurlp/alice")
    assert excinfo.value.kind == LnurlError.KIND_BAD_RESPONSE


def test_fetch_lnurl_pay_data_rejects_wrong_tag(monkeypatch):
    _patch_urlopen(monkeypatch, _good_payload(tag="withdrawRequest"))
    with pytest.raises(LnurlError) as excinfo:
        fetch_lnurl_pay_data("https://example.com/.well-known/lnurlp/alice")
    assert excinfo.value.kind == LnurlError.KIND_BAD_RESPONSE


def test_fetch_lnurl_pay_data_rejects_invalid_json(monkeypatch):
    _patch_urlopen(monkeypatch, b"not-json")
    with pytest.raises(LnurlError) as excinfo:
        fetch_lnurl_pay_data("https://example.com/.well-known/lnurlp/alice")
    assert excinfo.value.kind == LnurlError.KIND_BAD_RESPONSE


def test_fetch_lnurl_pay_data_rejects_inverted_amounts(monkeypatch):
    _patch_urlopen(monkeypatch, _good_payload(minSendable=2000, maxSendable=1000))
    with pytest.raises(LnurlError) as excinfo:
        fetch_lnurl_pay_data("https://example.com/.well-known/lnurlp/alice")
    assert excinfo.value.kind == LnurlError.KIND_BAD_RESPONSE


def test_fetch_lnurl_pay_data_rejects_non_https_callback(monkeypatch):
    _patch_urlopen(monkeypatch, _good_payload(callback="http://example.com/cb"))
    with pytest.raises(LnurlError) as excinfo:
        fetch_lnurl_pay_data("https://example.com/.well-known/lnurlp/alice")
    assert excinfo.value.kind == LnurlError.KIND_BAD_RESPONSE


def test_fetch_lnurl_pay_data_treats_missing_nostr_pubkey_as_non_zappable(monkeypatch):
    _patch_urlopen(monkeypatch, _good_payload(allowsNostr=True, nostrPubkey=""))
    data = fetch_lnurl_pay_data("https://example.com/.well-known/lnurlp/alice")
    assert data.allows_nostr is False
    assert data.nostr_pubkey == ""


def test_fetch_lnurl_pay_data_handles_no_nostr_support(monkeypatch):
    _patch_urlopen(monkeypatch, _good_payload(allowsNostr=False))
    data = fetch_lnurl_pay_data("https://example.com/.well-known/lnurlp/alice")
    assert data.allows_nostr is False
    assert data.nostr_pubkey == ""


# ──────────────────────────────────────────────────────────────────────
# fetch_invoice

def _pay_data(**overrides) -> LnurlPayData:
    fields = dict(
        callback="https://example.com/callback",
        min_sendable_msat=1000,
        max_sendable_msat=1000000,
        metadata_raw="[]",
        allows_nostr=True,
        nostr_pubkey="b" * 64,
    )
    fields.update(overrides)
    return LnurlPayData(**fields)


def test_fetch_invoice_returns_bolt11(monkeypatch):
    captured = _patch_urlopen(monkeypatch, json.dumps({"pr": "lnbc100..."}).encode("utf-8"))
    bolt11 = fetch_invoice(
        pay_data=_pay_data(),
        amount_msat=5000,
        zap_request_json='{"kind":9734}',
    )
    assert bolt11 == "lnbc100..."
    assert "amount=5000" in captured["url"]
    assert "nostr=" in captured["url"]


def test_fetch_invoice_appends_to_existing_query(monkeypatch):
    captured = _patch_urlopen(
        monkeypatch,
        json.dumps({"pr": "lnbc1..."}).encode("utf-8"),
    )
    fetch_invoice(
        pay_data=_pay_data(callback="https://example.com/cb?token=abc"),
        amount_msat=5000,
        zap_request_json="{}",
    )
    assert "token=abc&" in captured["url"]


def test_fetch_invoice_rejects_amount_below_min():
    with pytest.raises(LnurlError) as excinfo:
        fetch_invoice(
            pay_data=_pay_data(min_sendable_msat=1000),
            amount_msat=500,
            zap_request_json="{}",
        )
    assert excinfo.value.kind == LnurlError.KIND_AMOUNT


def test_fetch_invoice_rejects_amount_above_max():
    with pytest.raises(LnurlError) as excinfo:
        fetch_invoice(
            pay_data=_pay_data(max_sendable_msat=1000),
            amount_msat=5000,
            zap_request_json="{}",
        )
    assert excinfo.value.kind == LnurlError.KIND_AMOUNT


def test_fetch_invoice_propagates_service_error(monkeypatch):
    _patch_urlopen(
        monkeypatch,
        json.dumps({"status": "ERROR", "reason": "user has fled the country"}).encode("utf-8"),
    )
    with pytest.raises(LnurlError) as excinfo:
        fetch_invoice(
            pay_data=_pay_data(),
            amount_msat=5000,
            zap_request_json="{}",
        )
    assert excinfo.value.kind == LnurlError.KIND_REJECTED
    assert "fled" in str(excinfo.value)


def test_fetch_invoice_rejects_missing_pr_field(monkeypatch):
    _patch_urlopen(monkeypatch, json.dumps({"status": "OK"}).encode("utf-8"))
    with pytest.raises(LnurlError) as excinfo:
        fetch_invoice(
            pay_data=_pay_data(),
            amount_msat=5000,
            zap_request_json="{}",
        )
    assert excinfo.value.kind == LnurlError.KIND_BAD_RESPONSE


# ──────────────────────────────────────────────────────────────────────
# build_zap_request_tags

def test_zap_request_tags_carry_required_fields():
    tags = build_zap_request_tags(
        recipient_pubkey=RECIPIENT_PUBKEY,
        amount_msat=21000,
        relays=["wss://r1", "wss://r2"],
    )
    assert ["relays", "wss://r1", "wss://r2"] in tags
    assert ["amount", "21000"] in tags
    assert ["p", RECIPIENT_PUBKEY] in tags


def test_zap_request_tags_lowercases_pubkey():
    tags = build_zap_request_tags(
        recipient_pubkey="A" * 64,
        amount_msat=21000,
        relays=["wss://r1"],
    )
    p_tag = next(t for t in tags if t and t[0] == "p")
    assert p_tag[1] == "a" * 64


def test_zap_request_tags_dedupes_relays_preserving_order():
    tags = build_zap_request_tags(
        recipient_pubkey=RECIPIENT_PUBKEY,
        amount_msat=21000,
        relays=["wss://r1", "wss://r2", "wss://r1"],
    )
    relays_tag = next(t for t in tags if t and t[0] == "relays")
    assert relays_tag == ["relays", "wss://r1", "wss://r2"]


def test_zap_request_tags_drops_empty_relay_urls():
    tags = build_zap_request_tags(
        recipient_pubkey=RECIPIENT_PUBKEY,
        amount_msat=21000,
        relays=["", "wss://r1"],
    )
    relays_tag = next(t for t in tags if t and t[0] == "relays")
    assert relays_tag == ["relays", "wss://r1"]


def test_zap_request_tags_appends_plugin_anchor():
    tags = build_zap_request_tags(
        recipient_pubkey=RECIPIENT_PUBKEY,
        amount_msat=21000,
        relays=["wss://r1"],
        plugin_anchor=f"30700:{RECIPIENT_PUBKEY}:my-plugin",
    )
    a_tags = [t for t in tags if t and t[0] == "a"]
    assert a_tags == [["a", f"30700:{RECIPIENT_PUBKEY}:my-plugin"]]


def test_zap_request_tags_omits_anchor_when_absent():
    tags = build_zap_request_tags(
        recipient_pubkey=RECIPIENT_PUBKEY,
        amount_msat=21000,
        relays=["wss://r1"],
    )
    assert all(t[0] != "a" for t in tags if t)


def test_zap_request_tags_rejects_non_positive_amount():
    with pytest.raises(LnurlError) as excinfo:
        build_zap_request_tags(
            recipient_pubkey=RECIPIENT_PUBKEY,
            amount_msat=0,
            relays=["wss://r1"],
        )
    assert excinfo.value.kind == LnurlError.KIND_AMOUNT


def test_zap_request_tags_rejects_short_pubkey():
    with pytest.raises(LnurlError) as excinfo:
        build_zap_request_tags(
            recipient_pubkey="abc",
            amount_msat=1000,
            relays=["wss://r1"],
        )
    assert excinfo.value.kind == LnurlError.KIND_BAD_RESPONSE
