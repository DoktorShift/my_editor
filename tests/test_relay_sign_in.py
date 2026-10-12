# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins how the relay pool signs in to a relay that asks (NIP-42).

What must hold:
- A relay that refuses a request with "auth-required:" is signed in to,
  with the challenge it gave the connection, and asked again, once.
  Only if that does not work does its refusal stand.
- Nothing signs in unasked: a challenge alone is no reason to.
- A relay the signer may not sign in to is told nothing.
- One sign-in per key and connection, however many requests asked; a
  new connection asks again.
- A publish waits for a sign-in the person still has to approve.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr import events  # noqa: E402
from nostr.relay import AUTH_KIND, NOT_CHOSEN, KeySignIn  # noqa: E402
from tests.outbox_fakes import settle, signed  # noqa: E402
from tests.relay_fakes import (  # noqa: E402
    CHALLENGE, accept_sign_in, fake_relay_pool, refused_read,
)

MINE = "wss://mine.example"
KEY = bytes.fromhex("4c" * 32)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pool(monkeypatch):
    return fake_relay_pool(monkeypatch)


class Signer(KeySignIn):
    """Signs with KEY where it may; counts what it was asked, and can fail."""

    def __init__(self, *, may=True, fail=None):
        super().__init__(KEY)
        self.may, self.fail, self.asked = may, fail, []

    def may_sign_in(self, url):
        return self.may

    def sign(self, url, challenge, on_signed, on_failed):
        self.asked.append((url, challenge))
        if self.fail:
            on_failed(self.fail)
        else:
            super().sign(url, challenge, on_signed, on_failed)


def failures(pool):
    seen = []
    pool.sign_in_failed.connect(lambda *args: seen.append(args))
    return seen


def test_a_refused_read_is_signed_in_to_and_asked_again(pool):
    pool.sign_in = Signer()
    sub, relay, seen = refused_read(pool, MINE)
    settle()
    answer = accept_sign_in(relay)
    assert answer["kind"] == AUTH_KIND and events.verify_event(answer)
    assert ["relay", MINE] in answer["tags"] and ["challenge", CHALLENGE] in answer["tags"]
    reqs = relay.sent_of("REQ")
    assert len(reqs) == 2 and reqs[1][1] == sub.sub_id      # asked again, same question
    draft = signed(31234, [["d", "x"]])
    relay.says("EVENT", sub.sub_id, draft)
    assert seen["events"] == [draft] and seen["closed"] == []


def test_nothing_signs_in_unasked(pool):
    pool.sign_in = signer = Signer()
    sub = pool.subscribe([MINE], [{"kinds": [1]}])
    relay = pool.get_or_create(MINE)
    relay.says("AUTH", CHALLENGE)                # a challenge alone asks for nothing
    relay.says("EOSE", sub.sub_id)
    settle()
    assert relay.sent_of("AUTH") == [] and signer.asked == []


def test_without_a_signer_the_refusal_stands(pool):
    sub, relay, seen = refused_read(pool, MINE)
    assert relay.sent_of("AUTH") == []
    assert seen["closed"] == ["auth-required: sign in to read your drafts"]


def test_a_relay_the_signer_may_not_sign_in_to_is_told_nothing(pool):
    pool.sign_in = signer = Signer(may=False)
    failed = failures(pool)
    sub, relay, seen = refused_read(pool, MINE)
    settle()
    assert relay.sent_of("AUTH") == [] and signer.asked == []
    assert seen["closed"] == ["auth-required: sign in to read your drafts"]
    assert failed == []                          # nothing to say: nothing was tried
    done = []
    relay.sign_in(signer, lambda ok, why: done.append((ok, why)))
    assert done == [(False, NOT_CHOSEN)]


def test_a_second_refusal_after_signing_in_stands(pool):
    pool.sign_in = Signer()
    sub, relay, seen = refused_read(pool, MINE)
    settle()
    accept_sign_in(relay)
    relay.says("CLOSED", sub.sub_id, "auth-required: still not yours")
    settle()
    assert len(relay.sent_of("AUTH")) == 1
    assert seen["closed"] == ["auth-required: still not yours"]


def test_a_sign_in_the_relay_refuses_leaves_its_refusal(pool):
    pool.sign_in = Signer()
    failed = failures(pool)
    sub, relay, seen = refused_read(pool, MINE)
    settle()
    accept_sign_in(relay, ok=False, message="restricted: not a member")
    assert seen["closed"] == ["auth-required: sign in to read your drafts"]
    assert failed == [(MINE, "restricted: not a member", True)]


def test_a_signer_that_does_not_sign_leaves_the_refusal(pool):
    pool.sign_in = Signer(fail="user rejected")
    failed = failures(pool)
    sub, relay, seen = refused_read(pool, MINE)
    settle()
    assert relay.sent_of("AUTH") == []
    assert failed == [(MINE, "user rejected", False)]
    assert seen["closed"] == ["auth-required: sign in to read your drafts"]


def test_the_challenge_may_come_after_the_refusal(pool):
    pool.sign_in = Signer()
    sub, relay, seen = refused_read(pool, MINE, challenge=None)
    settle()
    assert relay.sent_of("AUTH") == []           # nothing to sign yet
    relay.says("AUTH", "late")
    settle()
    answer = accept_sign_in(relay)
    assert ["challenge", "late"] in answer["tags"]
    assert len(relay.sent_of("REQ")) == 2


def test_a_relay_that_never_sends_a_challenge_is_given_up(pool):
    pool.sign_in = signer = Signer()
    failed = failures(pool)
    sub, relay, seen = refused_read(pool, MINE, challenge=None)
    relay._sign_ins[signer.pubkey].timer.timeout.emit()     # the wait for it ran out
    assert seen["closed"] and failed and failed[0][2] is False


def test_many_refusals_are_one_sign_in(pool):
    pool.sign_in = signer = Signer()
    first, relay, _ = refused_read(pool, MINE)
    second = pool.subscribe([MINE], [{"kinds": [1]}])
    relay.says("CLOSED", second.sub_id, "auth-required: you too")
    settle()
    assert len(signer.asked) == 1 and len(relay.sent_of("AUTH")) == 1
    accept_sign_in(relay)
    asked_again = {m[1] for m in relay.sent_of("REQ")}
    assert {first.sub_id, second.sub_id} <= asked_again
    third = pool.subscribe([MINE], [{"kinds": [7]}])
    relay.says("CLOSED", third.sub_id, "auth-required: and you")
    settle()
    assert len(relay.sent_of("AUTH")) == 1       # signed in already on this connection


def test_a_new_connection_signs_in_again(pool):
    pool.sign_in = signer = Signer()
    sub, relay, seen = refused_read(pool, MINE)
    settle()
    accept_sign_in(relay)
    relay.drop()
    relay.open()                                 # as the reconnect does
    relay.says("AUTH", "fresh")
    relay.says("CLOSED", sub.sub_id, "auth-required: who are you again")
    settle()
    answer = accept_sign_in(relay)
    assert ["challenge", "fresh"] in answer["tags"]
    assert len(signer.asked) == 2 and seen["closed"] == []


def test_a_refused_event_is_signed_in_for_and_sent_again(pool):
    pool.sign_in = Signer()
    event = signed(31234, [["d", "x"]])
    job = pool.publish([MINE], event)
    relay = pool.get_or_create(MINE)
    results = []
    job.all_done.connect(results.extend)
    relay.says("AUTH", CHALLENGE)
    relay.says("OK", event["id"], False, "auth-required: sign in to write")
    settle()
    accept_sign_in(relay)
    assert relay.sent_of("EVENT") == [["EVENT", event], ["EVENT", event]]
    relay.says("OK", event["id"], True, "")
    assert results == [(MINE, True, "")]


def test_a_publish_waits_while_the_person_approves_the_sign_in(pool):
    pool.sign_in = Signer()
    event = signed(31234, [["d", "x"]])
    job = pool.publish([MINE], event)
    relay = pool.get_or_create(MINE)
    results = []
    job.all_done.connect(results.extend)
    relay.says("AUTH", CHALLENGE)
    relay.says("OK", event["id"], False, "auth-required: sign in to write")
    settle()
    job._timer.timeout.emit()                    # the usual wait ran out meanwhile
    assert results == []
    accept_sign_in(relay)
    assert job._timer.isActive()                 # a full wait for the answer
    relay.says("OK", event["id"], True, "")
    assert results == [(MINE, True, "")]


def test_a_request_may_name_who_signs_in(pool):
    pool.sign_in = Signer(may=False)
    own = Signer()
    sub = pool.subscribe([MINE], [{"kinds": [24133]}], sign_in=own)
    relay = pool.get_or_create(MINE)
    relay.says("AUTH", CHALLENGE)
    relay.says("CLOSED", sub.sub_id, "auth-required: messages are for their owner")
    accept_sign_in(relay)
    assert own.asked and len(relay.sent_of("REQ")) == 2


def test_a_connection_lost_mid_sign_in_is_a_lost_relay_not_a_refusal(pool):
    pool.sign_in = Signer()
    sub, relay, seen = refused_read(pool, MINE, challenge=None)
    lost = []
    sub.relay_failed.connect(lambda url, why: lost.append(url))
    relay.drop()
    assert relay._sign_ins == {}
    assert lost == [MINE] and seen["closed"] == []
