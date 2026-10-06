# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One question to several relays, and what came back from where
(nostr/queries.py ``fetch_events``).

What must hold: the query ends when every relay has ended one way or
another, and at the latest on time; it says which relays answered and
which did not, so "nothing there" is never confused with "nobody
answered"; every event comes with the relays that sent it; only validly
signed events count, and a forged copy cannot hide the real one.
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr import queries  # noqa: E402
from nostr.queries import Fetched, fetch_events  # noqa: E402
from tests.outbox_fakes import OTHER_SK, PK, HandPool, settle, signed  # noqa: E402

A = "wss://a.example"
B = "wss://b.example"
C = "wss://c.example"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication(sys.argv)


_ALIVE = []      # the queries asked, kept as a caller's parent would keep them


def ask(relays, *, accept=None, timeout_ms=60_000):
    pool = HandPool()
    out = []
    _ALIVE.append(fetch_events(pool, relays, [{"kinds": [1]}], out.append, accept=accept,
                               timeout_ms=timeout_ms))
    return pool, out


def test_events_come_back_with_the_relays_that_sent_them():
    pool, out = ask([A, B])
    sub = pool.subs[0]
    one, two = signed(1, content="one"), signed(1, content="two")
    sub.answer(A, one, two)
    assert out == []                          # B has not ended yet
    sub.answer(B, one)
    fetched = out[0]
    assert [e["id"] for e in fetched.events] == [one["id"], two["id"]]
    assert fetched.relays_of(one["id"]) == (A, B)
    assert fetched.relays_of(two["id"]) == (A,)
    assert fetched.answered == (A, B) and fetched.refused == ()
    assert sub.is_closed


def test_it_ends_when_every_relay_ended_one_way_or_another():
    pool, out = ask([A, B, C])
    sub = pool.subs[0]
    sub.fail(A)
    sub.refuse(B, "auth-required: sign in")
    assert out == []
    sub.answer(C)
    assert out[0].answered == (C,)
    assert set(out[0].refused) == {A, B}


def test_a_relay_that_never_ends_is_neither_answered_nor_refused():
    pool, out = ask([A, B], timeout_ms=20)
    pool.subs[0].answer(A, signed(1))
    deadline = time.monotonic() + 2
    while not out and time.monotonic() < deadline:
        settle()
    assert out and out[0].answered == (A,) and out[0].refused == ()


def test_only_validly_signed_events_count_and_a_forgery_cannot_hide_the_real_one():
    real = signed(1, content="mine")
    forged = dict(real, content="not what I wrote")       # same id, wrong content
    pool, out = ask([A, B])
    sub = pool.subs[0]
    sub.answer(A, forged, {"id": "x" * 64, "kind": 1})
    sub.answer(B, real)
    assert out[0].events == (real,)
    assert out[0].relays_of(real["id"]) == (B,)


def test_accept_decides_which_signed_events_count():
    mine, theirs = signed(1, content="mine"), signed(1, content="theirs", sk=OTHER_SK)
    pool, out = ask([A], accept=lambda event: event["pubkey"] == PK)
    pool.subs[0].answer(A, theirs, mine)
    assert out[0].events == (mine,)


def test_nothing_to_ask_answers_on_the_next_turn_with_nothing():
    pool, out = ask([])
    assert out == [] and pool.subs == []
    settle()
    assert out == [Fetched()]


def test_events_after_the_end_are_not_added():
    pool, out = ask([A])
    sub = pool.subs[0]
    sub.answer(A)
    sub.send(A, signed(1))
    assert out[0].events == ()


def test_a_flooding_relay_costs_a_bounded_number_of_checks():
    pool = HandPool()
    out = []
    _ALIVE.append(queries._EventsQuery(pool, [A], [{"kinds": [1]}], out.append, None,
                                       60_000, None, max_copies=2))
    sub = pool.subs[0]
    first, second, third = (signed(1, content=str(n)) for n in range(3))
    sub.answer(A, first, second, third)
    assert [e["id"] for e in out[0].events] == [first["id"], second["id"]]
