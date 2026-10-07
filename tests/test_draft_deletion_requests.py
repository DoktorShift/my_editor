# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deleting drafts says how often a signer app will ask (engine review M7).

Each draft is deleted by a separately signed replacement, and an
imported draft by a second request too (NIP-09), which tells other apps
(EINUNDZWANZIG STANDUP) the post is gone. The question before deleting
counts both, and a key kept on this computer asks nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import main_window
from main_window import MainWindow
from nostr.draft_store import DraftStore
from nostr.drafts import DraftWrapMeta
from tests.widget_lifetime import delete_new_windows


@pytest.fixture(autouse=True)
def _windows_deleted():
    """Every window and panel a test makes is deleted after it: left to
    the cycle collector, one without a parent can crash it."""
    yield from delete_new_windows()


PK = "ab" * 32


def store_with(*records):
    store = DraftStore()
    store.bind_profile(PK)
    for identifier, tags in records:
        store.upsert_skeleton(DraftWrapMeta(identifier, 30023, identifier + "-wrap", PK,
                                            100, None, "ct"))
        store.set_decrypted(identifier, inner={"kind": 30023, "content": "x",
                                               "tags": [["title", identifier], *tags]})
    return store


def host_for(store, *, local=False):
    host = SimpleNamespace(_draft_store=store,
                           _profile_store=SimpleNamespace(default=lambda: SimpleNamespace(
                               is_local=local)))
    host._imported_draft_wraps = lambda deletable: MainWindow._imported_draft_wraps(
        host, deletable)
    return host


def test_imported_drafts_are_known_by_their_name_or_their_source():
    store = store_with(("rss-1234", []), ("my-own", []),
                       ("kept-name", [["source", "https://blog.example/feed"]]))
    host = host_for(store)
    found = MainWindow._imported_draft_wraps(
        host, [("rss-1234", 30023), ("my-own", 30023), ("kept-name", 30023)])
    assert found == {"rss-1234": "rss-1234-wrap", "kept-name": "kept-name-wrap"}


@pytest.fixture
def asked(monkeypatch):
    told = []
    monkeypatch.setattr(main_window, "confirm_destructive",
                        lambda parent, **kw: told.append(kw) or False)
    return told


def test_one_imported_draft_means_two_requests(asked):
    host = host_for(store_with(("rss-1234", [])))
    MainWindow._confirm_draft_deletion(host, [("rss-1234", 30023)], [])
    message = asked[0]["message"]
    assert "expect 2 requests" in message
    assert "An imported draft takes a second request" in message


def test_drafts_and_imported_drafts_are_counted_together(asked):
    host = host_for(store_with(("rss-1", []), ("rss-2", []), ("mine", [])))
    MainWindow._confirm_draft_deletion(
        host, [("rss-1", 30023), ("rss-2", 30023), ("mine", 30023)], [])
    assert "expect 5 requests" in asked[0]["message"]


def test_one_draft_of_ones_own_asks_once_and_says_nothing(asked):
    host = host_for(store_with(("mine", [])))
    MainWindow._confirm_draft_deletion(host, [("mine", 30023)], [])
    assert "request" not in asked[0]["message"]


def test_a_key_kept_here_asks_nothing(asked):
    host = host_for(store_with(("rss-1", []), ("rss-2", [])), local=True)
    MainWindow._confirm_draft_deletion(host, [("rss-1", 30023), ("rss-2", 30023)], [])
    assert "request" not in asked[0]["message"]
