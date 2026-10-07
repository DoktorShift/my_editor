# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shared list of sources never loses the other app's edits.

Review of the engine, H1 and H2 (both seen on the ndak relays): a stale
copy of the list, kept by a slow or a fallback relay, used to win the
merge and erase a source EINUNDZWANZIG STANDUP had added, or bring back
one removed here; and a read that nobody answered was taken for "there
is no list", after which this app's view was published over a newer
list. Now only a list newer than the last one seen counts, and nothing
is published, nor the old list's migration marked done (M1), without an
answer from the account's own relays.
"""

from __future__ import annotations

import json

from nostr import events
from nostr.imports.constants import FEED_LIST_DTAG, LEGACY_FEED_LIST_DTAG, SUBSCRIPTIONS_KIND
from tests.outbox_fakes import OTHER_SK, SK, settle
from tests.test_imports_subscriptions import NOW, FakeRelay, bound, publish

A = "https://a.example/feed"
B = "https://b.example/feed"
X = "https://x.example/feed"


def list_event(urls, created_at, *, sk=SK, d_tag=FEED_LIST_DTAG):
    payload = {"feeds": [{"url": url, "title": ""} for url in urls]}
    return events.sign_event({"kind": SUBSCRIPTIONS_KIND,
                              "content": "ENC[" + json.dumps(payload) + "]",
                              "tags": [["d", d_tag], ["encrypted", "nip44"]],
                              "created_at": created_at}, sk)


def urls(store):
    return sorted(feed.url for feed in store.feeds)


def published_urls(relay):
    _relays, signed = relay.published[-1]
    return sorted(row["url"] for row in json.loads(signed["content"][4:-1])["feeds"])


class Relays(FakeRelay):
    """Relays whose answers a test chooses: a list of events, and whether
    they answered at all."""

    def __init__(self):
        super().__init__()
        self.answers = {}           # d tag -> [events]
        self.silent = set()         # d tags nobody answers for

    def events(self, relays, filters, on_done):
        self.queries.append((list(relays), filters))
        d_tag = filters[0]["#d"][0]
        if d_tag in self.silent:
            on_done([], set())
            return
        answer = self.answers.get(d_tag)
        if answer is None:
            stored = self.stored.get(d_tag)
            answer = [stored] if stored else []
        on_done(list(answer), set(relays))


def known_with(tmp_path, urls_, created_at=NOW - 50):
    relay = Relays()
    newest = list_event(urls_, created_at)
    relay.stored[FEED_LIST_DTAG] = newest
    store, relay, scheduler = bound(tmp_path, relay=relay)
    assert sorted(urls(store)) == sorted(urls_)
    return store, relay, scheduler


class TestOnlyANewerListCounts:
    def test_a_stale_copy_does_not_remove_what_the_other_app_added(self, tmp_path):
        store, relay, _scheduler = known_with(tmp_path, [A, X])
        relay.answers[FEED_LIST_DTAG] = [list_event([A], NOW - 500)]
        store.refresh()
        settle()
        assert urls(store) == [A, X]

    def test_a_stale_copy_before_publishing_does_not_erase_a_source(self, tmp_path):
        store, relay, scheduler = known_with(tmp_path, [A, X])
        relay.answers[FEED_LIST_DTAG] = [list_event([A], NOW - 500)]
        store.add_feed(B)
        publish(store, scheduler)
        assert published_urls(relay) == [A, B, X]
        # Published after the list it was built on, not only after the
        # stale copy the relays answered with.
        assert relay.published[-1][1]["created_at"] > NOW - 50

    def test_a_removal_here_survives_a_stale_copy(self, tmp_path):
        store, relay, scheduler = known_with(tmp_path, [A, X])
        relay.answers[FEED_LIST_DTAG] = [list_event([A, X], NOW - 500)]
        store.remove_feed(X)
        publish(store, scheduler)
        assert published_urls(relay) == [A]
        assert urls(store) == [A]

    def test_a_newer_list_still_counts(self, tmp_path):
        store, relay, _scheduler = known_with(tmp_path, [A])
        relay.answers[FEED_LIST_DTAG] = [list_event([A, X], NOW)]
        store.refresh()
        settle()
        assert urls(store) == [A, X]

    def test_the_last_list_seen_is_remembered_across_launches(self, tmp_path):
        store, relay, _scheduler = known_with(tmp_path, [A, X])
        store.bind_profile(None)
        settle()
        relay.answers[FEED_LIST_DTAG] = [list_event([A], NOW - 500)]
        from tests.test_imports_subscriptions import PROFILE
        store.bind_profile(PROFILE)
        settle()
        assert urls(store) == [A, X]

    def test_a_forged_newer_list_does_not_hide_the_real_one(self, tmp_path):
        store, relay, _scheduler = known_with(tmp_path, [A])
        relay.answers[FEED_LIST_DTAG] = [list_event([A, X], NOW),
                                         list_event([B], NOW + 100, sk=OTHER_SK)]
        store.refresh()
        settle()
        assert urls(store) == [A, X]


class TestNoAnswerIsNotNoList:
    def test_nothing_is_published_over_a_list_nobody_answered_for(self, tmp_path):
        store, relay, scheduler = known_with(tmp_path, [A])
        statuses = []
        store.sync_status.connect(statuses.append)
        relay.silent.add(FEED_LIST_DTAG)
        sent = len(relay.published)
        store.add_feed(B)
        publish(store, scheduler)
        assert len(relay.published) == sent
        assert store._dirty                     # waits here, and in the cache
        assert any("didn't answer" in text for text in statuses)
        # Once the relays answer, the change goes out, merged with theirs.
        relay.silent.clear()
        relay.answers[FEED_LIST_DTAG] = [list_event([A, X], NOW)]
        store.flush()
        settle()
        assert published_urls(relay) == [A, B, X]

    def test_no_answer_leaves_the_list_unknown(self, tmp_path):
        relay = Relays()
        relay.silent.add(FEED_LIST_DTAG)
        store, relay, _scheduler = bound(tmp_path, relay=relay)
        assert not store.known

    def test_a_silent_old_list_is_asked_for_again(self, tmp_path):
        """Review M1: the one-time merge of the old list was marked done
        when nobody answered, and its sources never came."""
        relay = Relays()
        relay.silent.add(LEGACY_FEED_LIST_DTAG)
        relay.stored[LEGACY_FEED_LIST_DTAG] = list_event([X], NOW - 900,
                                                         d_tag=LEGACY_FEED_LIST_DTAG)
        store, relay, _scheduler = bound(tmp_path, relay=relay)
        assert not store._legacy_merged
        relay.silent.clear()
        store.refresh()
        settle()
        assert store._legacy_merged
        assert X in urls(store)
