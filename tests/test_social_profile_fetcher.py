"""Tests for CommenterProfileFetcher.

The fetcher coalesces kind:0 lookups per pubkey, respects a 24h TTL,
and emits ``profile_updated`` after writing parsed metadata into the
engagement cache. We exercise those guarantees against a fake relay
pool so no network is touched.
"""

from __future__ import annotations

import json
import time

import pytest

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from plugin_marketplace.social import EngagementCache, CommenterProfileFetcher


PUBKEY_A = "a" * 64
PUBKEY_B = "b" * 64


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def cache(tmp_path):
    return EngagementCache(path=tmp_path / "engagement.sqlite")


class _FakeSubscription(QObject):
    event = Signal(dict)
    eose = Signal()
    closed = Signal(str)

    def __init__(self, *, urls, filters) -> None:
        super().__init__()
        self.urls = urls
        self.filters = filters
        self.closed_flag = False

    def close(self) -> None:
        self.closed_flag = True


class _FakeRelayPool:
    def __init__(self) -> None:
        self.subs: list[_FakeSubscription] = []

    def subscribe(self, urls, filters, sub_id=None):
        sub = _FakeSubscription(urls=urls, filters=filters)
        self.subs.append(sub)
        return sub


def _metadata_event(pubkey: str, **fields) -> dict:
    return {
        "id": "0" * 64,
        "pubkey": pubkey,
        "kind": 0,
        "created_at": int(time.time()),
        "tags": [],
        "content": json.dumps(fields),
        "sig": "0" * 128,
    }


# ──────────────────────────────────────────────────────────────────────
# request_many

def test_request_many_opens_one_subscription_for_two_pubkeys(qapp, cache):
    pool = _FakeRelayPool()
    fetcher = CommenterProfileFetcher(relay_pool=pool, cache=cache)
    fetcher.request_many([PUBKEY_A, PUBKEY_B])
    assert len(pool.subs) == 1
    authors = pool.subs[0].filters[0]["authors"]
    assert set(authors) == {PUBKEY_A, PUBKEY_B}


def test_request_many_skips_pubkeys_with_fresh_cache(qapp, cache):
    cache.store_profile(PUBKEY_A, display_name="Alice")
    pool = _FakeRelayPool()
    fetcher = CommenterProfileFetcher(relay_pool=pool, cache=cache)
    fetcher.request_many([PUBKEY_A, PUBKEY_B])
    assert len(pool.subs) == 1
    authors = pool.subs[0].filters[0]["authors"]
    assert authors == [PUBKEY_B]


def test_request_many_skips_inflight_pubkeys(qapp, cache):
    """Two back-to-back requests for the same pubkey only open one sub."""
    pool = _FakeRelayPool()
    fetcher = CommenterProfileFetcher(relay_pool=pool, cache=cache)
    fetcher.request_many([PUBKEY_A])
    fetcher.request_many([PUBKEY_A])
    assert len(pool.subs) == 1


def test_request_many_skips_empty_pubkeys(qapp, cache):
    pool = _FakeRelayPool()
    fetcher = CommenterProfileFetcher(relay_pool=pool, cache=cache)
    fetcher.request_many(["", None])  # type: ignore[list-item]
    assert pool.subs == []


def test_request_many_noop_when_all_cached(qapp, cache):
    cache.store_profile(PUBKEY_A, display_name="Alice")
    pool = _FakeRelayPool()
    fetcher = CommenterProfileFetcher(relay_pool=pool, cache=cache)
    fetcher.request_many([PUBKEY_A])
    assert pool.subs == []


# ──────────────────────────────────────────────────────────────────────
# event handling

def test_handle_event_stores_display_name_and_picture(qapp, cache):
    fetcher = CommenterProfileFetcher(relay_pool=_FakeRelayPool(), cache=cache)
    fetcher._handle_event(_metadata_event(
        PUBKEY_A,
        display_name="Alice",
        picture="https://example.com/a.png",
        nip05="alice@example.com",
    ))
    row = cache.get_profile(PUBKEY_A)
    assert row is not None
    assert row["display_name"] == "Alice"
    assert row["picture_url"] == "https://example.com/a.png"
    assert row["nip05"] == "alice@example.com"


def test_handle_event_falls_back_to_name_when_no_display_name(qapp, cache):
    fetcher = CommenterProfileFetcher(relay_pool=_FakeRelayPool(), cache=cache)
    fetcher._handle_event(_metadata_event(PUBKEY_A, name="bob"))
    row = cache.get_profile(PUBKEY_A)
    assert row["display_name"] == "bob"


def test_handle_event_strips_non_http_picture_url(qapp, cache):
    fetcher = CommenterProfileFetcher(relay_pool=_FakeRelayPool(), cache=cache)
    fetcher._handle_event(_metadata_event(
        PUBKEY_A,
        display_name="Alice",
        picture="javascript:alert(1)",
    ))
    row = cache.get_profile(PUBKEY_A)
    assert row["picture_url"] == ""


def test_handle_event_emits_profile_updated(qapp, cache):
    fetcher = CommenterProfileFetcher(relay_pool=_FakeRelayPool(), cache=cache)
    captured: list[str] = []
    fetcher.profile_updated.connect(captured.append)
    fetcher._handle_event(_metadata_event(PUBKEY_A, display_name="Alice"))
    assert captured == [PUBKEY_A]


def test_handle_event_ignores_non_kind_zero(qapp, cache):
    fetcher = CommenterProfileFetcher(relay_pool=_FakeRelayPool(), cache=cache)
    captured: list[str] = []
    fetcher.profile_updated.connect(captured.append)
    event = _metadata_event(PUBKEY_A, display_name="Alice")
    event["kind"] = 1
    fetcher._handle_event(event)
    assert captured == []
    assert cache.get_profile(PUBKEY_A) is None


def test_handle_event_ignores_malformed_content(qapp, cache):
    fetcher = CommenterProfileFetcher(relay_pool=_FakeRelayPool(), cache=cache)
    event = _metadata_event(PUBKEY_A)
    event["content"] = "not-json"
    fetcher._handle_event(event)
    # Empty fields are stored; downstream code treats them like "no profile".
    row = cache.get_profile(PUBKEY_A)
    assert row is None or row["display_name"] == ""


def test_handle_event_ignores_bad_pubkey(qapp, cache):
    fetcher = CommenterProfileFetcher(relay_pool=_FakeRelayPool(), cache=cache)
    event = _metadata_event("short", display_name="Alice")
    fetcher._handle_event(event)
    assert cache.get_profile("short") is None


# ──────────────────────────────────────────────────────────────────────
# lifecycle

def test_close_all_clears_inflight(qapp, cache):
    pool = _FakeRelayPool()
    fetcher = CommenterProfileFetcher(relay_pool=pool, cache=cache)
    fetcher.request_many([PUBKEY_A])
    fetcher.close_all()
    # In-flight gate released so a fresh call reopens the sub.
    fetcher.request_many([PUBKEY_A])
    assert len(pool.subs) == 2
