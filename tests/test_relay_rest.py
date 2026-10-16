# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins resting a relay that turns this client away for asking too often.

What must hold:
- A relay that refuses an event with "rate-limited:" or "banned:" is
  not published to for REST_MINUTES: a publish meanwhile counts it at
  once as refusing, with the reason it gave, and waits for nothing; it
  does not even connect to it. After the rest it is asked as usual.
- A refusal that can be about one event ("blocked:", say) rests nothing.
- The other relays of a publish do not rest with it.
- A rest is logged once, when it starts.
"""

import itertools
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.relay import REST_MINUTES  # noqa: E402
from tests.outbox_fakes import settle, signed  # noqa: E402
from tests.relay_fakes import fake_relay_pool  # noqa: E402

BUSY = "wss://busy.example"
OTHER = "wss://other.example"
SLOW_DOWN = "rate-limited: slow down"

_notes = itertools.count()


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


class Clock:
    """The time, in seconds, as the test sets it."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def pool(monkeypatch, clock):
    return fake_relay_pool(monkeypatch, clock=clock)


class Publish:
    """A fresh event published to ``urls``, and what its job tells: the
    relay that took it first, and every relay's answer once all did."""

    def __init__(self, pool, *urls):
        self.event = signed(1, content=f"note {next(_notes)}")
        self.job = pool.publish(list(urls), self.event)
        self.first, self.done = [], []
        self.job.first_accept.connect(self.first.append)
        self.job.all_done.connect(self.done.append)


def refused(pool, url, reason):
    """``url`` refuses a publish with ``reason``; the relay."""
    sent = Publish(pool, url)
    relay = pool.get_or_create(url)
    relay.says("OK", sent.event["id"], False, reason)
    assert sent.done == [[(url, False, reason)]]
    return relay


@pytest.mark.parametrize("reason", [
    SLOW_DOWN,
    "banned: too many rate-limit violations, try again later",    # relay.damus.io
])
def test_a_relay_that_turns_this_client_away_rests_then_is_asked_again(pool, clock, reason):
    relay = refused(pool, BUSY, reason)
    clock.now += REST_MINUTES * 60 - 1
    meanwhile = Publish(pool, BUSY)
    settle()
    assert len(relay.sent_of("EVENT")) == 1                       # not asked
    assert meanwhile.done == [[(BUSY, False, reason)]] and meanwhile.first == []
    assert not meanwhile.job._timer.isActive()                    # nothing waits
    clock.now += 1                                                # the rest is over
    after = Publish(pool, BUSY)
    assert relay.sent_of("EVENT")[-1] == ["EVENT", after.event]
    relay.says("OK", after.event["id"], True, "")
    assert after.first == [BUSY] and after.done == [[(BUSY, True, "")]]


def test_a_resting_relay_is_not_even_connected_to(pool):
    relay = refused(pool, BUSY, SLOW_DOWN)
    relay.drop()
    Publish(pool, BUSY)
    settle()
    assert not relay.is_connected


@pytest.mark.parametrize("reason", [
    "blocked: not on the list",
    "invalid: event creation date is too far off from the current time",
])
def test_a_refusal_that_can_be_about_one_event_rests_nothing(pool, reason):
    relay = refused(pool, BUSY, reason)
    second = Publish(pool, BUSY)
    assert relay.sent_of("EVENT")[-1] == ["EVENT", second.event]


def test_the_other_relays_of_a_publish_do_not_rest(pool):
    busy, other = refused(pool, BUSY, SLOW_DOWN), pool.get_or_create(OTHER)
    both = Publish(pool, BUSY, OTHER)
    settle()
    assert len(busy.sent_of("EVENT")) == 1
    assert other.sent_of("EVENT") == [["EVENT", both.event]]
    other.says("OK", both.event["id"], True, "")
    assert both.first == [OTHER]
    assert both.done == [[(BUSY, False, SLOW_DOWN), (OTHER, True, "")]]


def test_a_rest_is_logged_once_when_it_starts(pool, caplog):
    caplog.set_level(logging.WARNING, logger="nostr.relay")
    under_way = [Publish(pool, BUSY), Publish(pool, BUSY)]        # both sent already
    relay = pool.get_or_create(BUSY)
    for sent in under_way:
        relay.says("OK", sent.event["id"], False, SLOW_DOWN)
    logged = [r.getMessage() for r in caplog.records if r.name == "nostr.relay"]
    assert len(logged) == 1
    assert BUSY in logged[0] and SLOW_DOWN in logged[0]
    assert f"{REST_MINUTES} minutes" in logged[0]
