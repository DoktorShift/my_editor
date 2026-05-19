"""Tests for the marketplace data model + filtering."""

from __future__ import annotations

import pytest

from plugin_marketplace.models import (
    PluginListing,
    REGISTRY_SCHEMA_VERSION,
    RegistryParseError,
    filter_listings,
    parse_registry_index,
)


def make_entry(plugin_id="x", **overrides) -> dict:
    base = {
        "id": plugin_id,
        "name": plugin_id.title(),
        "version": "1.0.0",
        "download_url": "https://example.com/p.zip",
        "sha256": "a" * 64,
        "author": "Tester",
        "description": "A demo plugin.",
        "category": "editor",
        "tags": ["one", "two"],
    }
    base.update(overrides)
    return base


def test_parses_a_minimal_index():
    raw = {"schema_version": 1, "plugins": [make_entry()]}
    listings = parse_registry_index(raw, source_name="default")
    assert len(listings) == 1
    listing = listings[0]
    assert listing.plugin_id == "x"
    assert listing.source_name == "default"
    assert listing.is_free


def test_rejects_non_object_root():
    with pytest.raises(RegistryParseError):
        parse_registry_index([], source_name="d")


def test_rejects_unknown_schema_version():
    with pytest.raises(RegistryParseError):
        parse_registry_index(
            {"schema_version": REGISTRY_SCHEMA_VERSION + 1, "plugins": []},
            source_name="d",
        )


def test_drops_entries_with_missing_required_fields():
    """One bad entry must not break the rest of the index."""
    raw = {
        "schema_version": 1,
        "plugins": [
            make_entry("good"),
            {"id": "bad-no-version"},
            make_entry("also_good"),
        ],
    }
    ids = [li.plugin_id for li in parse_registry_index(raw, source_name="d")]
    assert ids == ["good", "also_good"]


def test_rejects_non_https_download_url():
    raw = {"schema_version": 1, "plugins": [make_entry(download_url="http://x/p.zip")]}
    assert parse_registry_index(raw, source_name="d") == []


def test_rejects_malformed_sha256():
    raw = {"schema_version": 1, "plugins": [make_entry(sha256="not-hex")]}
    assert parse_registry_index(raw, source_name="d") == []
    raw["plugins"][0]["sha256"] = "a" * 63
    assert parse_registry_index(raw, source_name="d") == []


def test_optional_int_coerces_safely():
    raw = {"schema_version": 1, "plugins": [make_entry(price_sats="not-int")]}
    listings = parse_registry_index(raw, source_name="d")
    assert listings[0].price_sats == 0


def test_bool_does_not_sneak_in_as_int():
    """``True`` is technically an int subclass, but the registry author
    didn't mean "1 sat"."""
    raw = {"schema_version": 1, "plugins": [make_entry(price_sats=True)]}
    listings = parse_registry_index(raw, source_name="d")
    assert listings[0].price_sats == 0


# ──────────────────────────────────────────────────────────────────────
# Filtering
# ──────────────────────────────────────────────────────────────────────

def _listing(**kw):
    base = dict(
        plugin_id="x",
        name="x",
        version="1",
        download_url="https://e/p.zip",
        sha256="a" * 64,
    )
    base.update(kw)
    return PluginListing(**base)


def test_filter_by_query_matches_name_and_tags():
    a = _listing(plugin_id="a", name="Async", description="speeds things up", tags=("perf",))
    b = _listing(plugin_id="b", name="Bookmarks", tags=("nav",))
    assert filter_listings([a, b], query="perf") == [a]
    assert filter_listings([a, b], query="book") == [b]


def test_filter_by_query_is_case_insensitive():
    a = _listing(plugin_id="a", name="Async")
    assert filter_listings([a], query="ASY") == [a]


def test_filter_by_category():
    a = _listing(plugin_id="a", category="nostr")
    b = _listing(plugin_id="b", category="wallets")
    assert filter_listings([a, b], category="nostr") == [a]
    # "all" pseudo-category is a no-op
    assert filter_listings([a, b], category="all") == [a, b]


def test_filter_free_only():
    free = _listing(plugin_id="f", price_sats=0)
    paid = _listing(plugin_id="p", price_sats=1000)
    assert filter_listings([free, paid], free_only=True) == [free]


def test_filter_unknown_category_defaults_to_other():
    """A listing with a category we don't know should show under 'other'."""
    a = _listing(plugin_id="a", category="made-up")
    assert filter_listings([a], category="other") == [a]
    assert filter_listings([a], category="editor") == []
