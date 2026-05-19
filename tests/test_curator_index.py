"""Tests for the kind:30750 curator-index reader."""

from __future__ import annotations

import pytest

from plugin_marketplace.curator_index import (
    CURATOR_INDEX_KIND,
    CuratorIndex,
    decode_curator_naddr,
    parse_curator_index,
    parse_listing_event,
)
from plugin_marketplace.social.models import PluginAnchor
from plugin_marketplace.social.parser import PLUGIN_LISTING_KIND


CURATOR = "a" * 64
AUTHOR = "b" * 64


def _sign(event_template: dict, *, sk_bytes: bytes) -> dict:
    from nostr.events import sign_event
    return sign_event(event_template, sk_bytes)


def _curator_event(
    *,
    identifier: str = "official-list",
    anchors=(),
    title: str = "Official curated list",
    description: str = "Plugins we trust.",
    created_at: int = 1_700_000_000,
) -> dict:
    from nostr import crypto
    from nostr.events import build_event
    sk = bytes(range(1, 33))
    pubkey = crypto.get_public_key(sk).hex()
    tags = [
        ["d", identifier],
        ["title", title],
        ["description", description],
    ]
    for anchor in anchors:
        tags.append(["a", anchor.coord, ""])
    unsigned = build_event(
        pubkey_hex=pubkey,
        kind=CURATOR_INDEX_KIND,
        content="",
        tags=tags,
        created_at=created_at,
    )
    return _sign(unsigned, sk_bytes=sk)


def _listing_event(
    *,
    plugin_id: str = "hello",
    name: str = "Hello",
    version: str = "1.0.0",
    download_url: str = "https://example.com/plugin.zip",
    sha256: str = "ab" * 32,
    description: str = "",
    license: str = "",
    lightning_address: str = "",
    price_sats: int = 0,
) -> tuple[dict, PluginAnchor]:
    from nostr import crypto
    from nostr.events import build_event
    sk = bytes(range(2, 34))
    author_pubkey = crypto.get_public_key(sk).hex()
    anchor = PluginAnchor(PLUGIN_LISTING_KIND, author_pubkey, plugin_id)
    tags = [
        ["d", plugin_id],
        ["name", name],
        ["version", version],
        ["download", download_url, sha256],
    ]
    if description:
        tags.append(["description", description])
    if license:
        tags.append(["license", license])
    if lightning_address:
        tags.append(["zap", lightning_address])
    if price_sats:
        tags.append(["price", str(price_sats)])
    unsigned = build_event(
        pubkey_hex=author_pubkey, kind=PLUGIN_LISTING_KIND,
        content="", tags=tags, created_at=1_700_000_000,
    )
    return _sign(unsigned, sk_bytes=sk), anchor


# ──────────────────────────────────────────────────────────────────────
# parse_curator_index

def test_parse_curator_index_collects_anchors():
    anchor = PluginAnchor(PLUGIN_LISTING_KIND, AUTHOR, "hello")
    event = _curator_event(anchors=[anchor])
    parsed = parse_curator_index(event)
    assert parsed is not None
    assert parsed.identifier == "official-list"
    assert parsed.anchors == (anchor,)


def test_parse_curator_index_rejects_wrong_kind():
    event = _curator_event(anchors=[PluginAnchor(PLUGIN_LISTING_KIND, AUTHOR, "x")])
    event["kind"] = 1
    assert parse_curator_index(event) is None


def test_parse_curator_index_returns_none_for_empty_list():
    event = _curator_event()
    assert parse_curator_index(event) is None


def test_parse_curator_index_filters_non_plugin_anchors():
    listing = PluginAnchor(PLUGIN_LISTING_KIND, AUTHOR, "hello")
    junk = PluginAnchor(30023, AUTHOR, "blog-post")
    event = _curator_event(anchors=[listing, junk])
    parsed = parse_curator_index(event)
    assert parsed is not None
    assert parsed.anchors == (listing,)


def test_parse_curator_index_rejects_unsigned_event():
    listing = PluginAnchor(PLUGIN_LISTING_KIND, AUTHOR, "hello")
    event = _curator_event(anchors=[listing])
    event["sig"] = "0" * 128
    assert parse_curator_index(event) is None


# ──────────────────────────────────────────────────────────────────────
# parse_listing_event

def test_parse_listing_event_round_trips_basic_listing():
    event, anchor = _listing_event(
        plugin_id="hello", name="Hello", description="A test plugin",
    )
    listing = parse_listing_event(event, anchor=anchor, source_name="curator")
    assert listing is not None
    assert listing.plugin_id == "hello"
    assert listing.name == "Hello"
    assert listing.description == "A test plugin"
    assert listing.download_url == "https://example.com/plugin.zip"


def test_parse_listing_event_rejects_d_tag_mismatch():
    event, anchor = _listing_event(plugin_id="hello")
    # Build a foreign anchor that points at a different d-tag.
    foreign = PluginAnchor(PLUGIN_LISTING_KIND, anchor.author_pubkey, "other")
    assert parse_listing_event(event, anchor=foreign, source_name="curator") is None


def test_parse_listing_event_rejects_missing_name():
    event, anchor = _listing_event(plugin_id="hello", name="")
    event["tags"] = [t for t in event["tags"] if t[0] != "name"]
    assert parse_listing_event(event, anchor=anchor, source_name="curator") is None


def test_parse_listing_event_extracts_optional_fields():
    event, anchor = _listing_event(
        plugin_id="paid", name="Paid Plugin",
        lightning_address="alice@example.com", price_sats=1000,
    )
    listing = parse_listing_event(event, anchor=anchor, source_name="curator")
    assert listing is not None
    assert listing.lightning_address == "alice@example.com"
    assert listing.price_sats == 1000


def test_parse_listing_event_rejects_unsigned_event():
    event, anchor = _listing_event()
    event["sig"] = "0" * 128
    assert parse_listing_event(event, anchor=anchor, source_name="curator") is None


# ──────────────────────────────────────────────────────────────────────
# decode_curator_naddr

def test_decode_curator_naddr_round_trip():
    # Construct a naddr from a known TLV payload via the public encoder.
    from plugin_marketplace.curator_index import _encode_naddr_listing
    anchor = PluginAnchor(CURATOR_INDEX_KIND, CURATOR, "official-list")
    naddr = _encode_naddr_listing(anchor, relay_hint="wss://relay.example")
    decoded = decode_curator_naddr(naddr)
    assert decoded.author_pubkey == CURATOR
    assert decoded.kind == CURATOR_INDEX_KIND
    assert decoded.identifier == "official-list"
    assert decoded.relay_hints == ("wss://relay.example",)


def test_decode_curator_naddr_rejects_wrong_hrp():
    with pytest.raises(Exception):
        decode_curator_naddr("npub1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
