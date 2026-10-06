# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Feeds checked while MyEditor runs: rhythm, health, and what arrives.

The clock, the fetcher and the scheduler are faked; the inbox database
is real (in memory). Nothing here asks the network anything.
"""

from __future__ import annotations

import pytest

from nostr.imports.checker import (
    CHECK_FAILED,
    TOO_LARGE,
    FeedChecker,
    checks_automatically,
    error_text,
    interval_for,
)
from nostr.imports.errors import ERROR_CODES, SourceError
from nostr.imports.inbox_store import INBOX, InboxStore, View
from tests.imports_fakes import inline_run_blocking, rss_feed

T0 = 1_800_000_000
FEED = "https://blog.example/feed"
PAGE = "https://blog.example/"


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


def feed(*titles):
    return rss_feed([{"title": t, "guid": f"g-{t}", "link": f"https://blog.example/{t}",
                      "pubdate": "Mon, 01 Jan 2024 00:00:00 GMT" if t.startswith("old")
                      else "Fri, 15 Jan 2027 08:05:00 GMT"} for t in titles])


class FakeFetcher:
    """``answers`` maps a URL to ("body", text, meta) | ("304",) | ("err", SourceError);
    ``hold`` keeps every request waiting."""

    def __init__(self, answers=None, hold=False):
        self.answers = dict(answers or {})
        self.hold = hold
        self.requests = []
        self.waiting = []

    def fetch_feed(self, url, *, etag, last_modified, on_body, on_not_modified, on_failure):
        self.requests.append((url, etag, last_modified))
        answer = lambda: self._answer(url, on_body, on_not_modified, on_failure)  # noqa: E731
        if self.hold:
            self.waiting.append(answer)
        else:
            answer()

    def fetch(self, url, *, on_success, on_failure):
        self.requests.append((url, None, None))
        kind, *rest = self.answers.get(url, ("err", SourceError("404", ERROR_CODES.FETCH_ERROR)))
        if kind == "body":
            on_success(rest[0])
        else:
            on_failure(rest[0])

    def _answer(self, url, on_body, on_not_modified, on_failure):
        kind, *rest = self.answers.get(url, ("err", SourceError("404", ERROR_CODES.FETCH_ERROR)))
        if kind == "body":
            on_body(rest[0], rest[1] if len(rest) > 1 else {})
        elif kind == "304":
            on_not_modified()
        else:
            on_failure(rest[0])


class Harness:
    def __init__(self, answers=None, *, sources=((FEED, True),), hold=False, online=True):
        self.clock = Clock()
        self.store = InboxStore(":memory:", clock=self.clock)
        self.store.sync_sources([(url, url, "", automatic) for url, automatic in sources])
        self.fetcher = FakeFetcher(answers, hold=hold)
        self.scheduled = []
        self.is_online = online
        self.checker = FeedChecker(
            store=self.store, fetcher=self.fetcher, run_blocking=inline_run_blocking,
            online=lambda: self.is_online,
            scheduler=lambda ms, fn: self.scheduled.append((ms, fn)))
        self.checked = []
        self.checker.source_checked.connect(lambda key, new: self.checked.append((key, new)))

    def later(self, seconds):
        self.clock.now += seconds


def test_only_feeds_are_checked_automatically():
    assert checks_automatically("https://blog.example/feed")
    assert checks_automatically("http://blog.example/rss.xml")
    assert not checks_automatically("npub1sg6plzptd64u62a878hep2kev88swjh3tw00gjsfl8f237lmu63q0uf63m")
    assert not checks_automatically("https://github.com/rinbal/notes/tree/main/posts")


def test_the_interval_stretches_above_fifty_sources():
    assert interval_for(1) == 900
    assert interval_for(50) == 900
    assert interval_for(51) == 1800
    assert interval_for(120) == 2700


def test_launch_waits_then_checks_what_is_due():
    h = Harness({FEED: ("body", feed("a"), {"etag": '"v1"', "final_url": FEED})})
    h.checker.start()
    assert h.fetcher.requests == []
    (delay, launch), = h.scheduled
    assert delay == 10_000
    launch()
    assert h.fetcher.requests == [(FEED, "", "")]
    assert h.checked == [(FEED, 0)]                  # the first check is a baseline
    assert h.store.counts().older == 1
    source = h.store.source(FEED)
    assert (source.etag, source.next_check) == ('"v1"', T0 + 900)


def test_new_posts_arrive_in_the_inbox_and_the_next_check_is_conditional():
    h = Harness({FEED: ("body", feed("a"), {"etag": '"v1"', "last_modified": "Mon"})})
    h.checker.start()
    h.checker.tick()
    h.later(900)
    h.fetcher.answers[FEED] = ("body", feed("a", "b"), {"etag": '"v2"'})
    h.checker.tick()
    assert h.fetcher.requests[-1] == (FEED, '"v1"', "Mon")
    assert h.checked[-1] == (FEED, 1)
    assert [p.title for p in h.store.page(View(INBOX))] == ["b"]


def test_an_unchanged_feed_is_a_success():
    h = Harness({FEED: ("304",)})
    h.checker.start()
    h.checker.tick()
    source = h.store.source(FEED)
    assert (source.failures, source.next_check) == (0, T0 + 900)


def test_at_most_two_at_a_time():
    urls = [f"https://{n}.example/feed" for n in "abcd"]
    h = Harness(sources=[(u, True) for u in urls], hold=True)
    h.checker.start()
    h.checker.tick()
    assert len(h.fetcher.requests) == 2
    h.checker.tick()
    assert len(h.fetcher.requests) == 2
    h.fetcher.waiting.pop(0)()                        # one ends: the next starts
    assert len(h.fetcher.requests) == 3


def test_a_failure_keeps_the_posts_and_tries_later():
    h = Harness({FEED: ("body", feed("a"))})
    h.checker.start()
    h.checker.tick()
    h.later(900)
    h.fetcher.answers[FEED] = ("err", SourceError("too big", ERROR_CODES.TOO_LARGE))
    h.checker.tick()
    source = h.store.source(FEED)
    assert (source.error, source.failures, source.next_check) == (
        TOO_LARGE, 1, h.clock.now + 1800)
    assert h.store.counts().older == 1


def test_offline_skips_without_a_failure():
    h = Harness({FEED: ("body", feed("a"))}, online=False)
    h.checker.start()
    h.checker.tick()
    assert h.fetcher.requests == []
    assert h.store.source(FEED).failures == 0


def test_manual_sources_are_never_checked_on_their_own():
    h = Harness(sources=[(FEED, False)])
    h.checker.start()
    h.checker.tick()
    assert h.fetcher.requests == []


def test_check_now_once_a_minute():
    h = Harness({FEED: ("body", feed("a"))})
    h.checker.start()
    assert h.checker.check_now(FEED)
    assert not h.checker.check_now(FEED)
    h.later(60)
    assert h.checker.check_now(FEED)


def test_stopping_ends_checks_without_a_result():
    h = Harness({FEED: ("body", feed("a"))}, hold=True)
    h.checker.start()
    h.checker.tick()
    h.checker.stop()
    h.fetcher.waiting.pop()()
    assert h.checked == []
    assert h.store.counts().older == 0
    assert h.store.claim(FEED)                        # the lease was given back
    h.checker.tick()
    assert len(h.fetcher.requests) == 1               # nothing more while stopped


def test_a_page_is_searched_for_its_feed_and_the_feed_remembered():
    h = Harness({PAGE: ("body", "<!doctype html><html><head><link rel='alternate' "
                        "type='application/rss+xml' href='/feed'></head></html>"),
                 FEED: ("body", feed("a"))}, sources=[(PAGE, True)])
    h.checker.start()
    h.checker.tick()
    assert h.store.source(PAGE).final_url == FEED
    assert h.store.counts().older == 1
    h.later(900)
    h.checker.tick()
    assert h.fetcher.requests[-1][0] == FEED


@pytest.mark.parametrize("code, expected", [
    (ERROR_CODES.TOO_LARGE, TOO_LARGE),
    (ERROR_CODES.UNKNOWN, CHECK_FAILED),
])
def test_error_words(code, expected):
    assert error_text(SourceError("x", code)) == expected


def test_a_local_address_says_so():
    text = error_text(SourceError("", ERROR_CODES.LOCAL_NETWORK))
    assert "local network" in text
