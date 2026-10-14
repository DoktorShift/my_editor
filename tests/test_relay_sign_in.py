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
- A sign-in refused for good (the person said no, the relay said no) is
  not asked again on that connection; one that only went unanswered is.
- While a signer is still asked, nothing asks it again, however often
  the relay drops the connection: the person sees one question.
- The account in use is checked again when the signer is asked, and
  when it answers.
- A publish waits for a sign-in the person still has to approve, then
  gets a full wait for the answer.
- A challenge too long to be one gets no sign-in.
- The pool speaks only of its own signer's sign-ins, and says "didn't
  accept" only when the relay answered so.
- When the account changes, connections signed in as another one close.
"""

import os
import sys
import time

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
    """Signs with KEY where it may; counts what it was asked, and can fail
    (``final``: for good) or, ``waits``, not answer until ``answer()``."""

    def __init__(self, *, may=True, fail=None, final=True, waits=False, key=KEY):
        super().__init__(key)
        self.may, self.fail, self.final, self.waits = may, fail, final, waits
        self.asked, self.waiting = [], []

    def may_sign_in(self, url):
        return self.may

    def is_final(self, reason):
        return self.final

    def sign(self, url, challenge, on_signed, on_failed):
        self.asked.append((url, challenge))
        if self.waits:
            self.waiting.append((url, challenge, on_signed, on_failed))
        elif self.fail:
            on_failed(self.fail)
        else:
            super().sign(url, challenge, on_signed, on_failed)

    def answer(self, *, fail=None):
        """The person answers the oldest question still open."""
        url, challenge, on_signed, on_failed = self.waiting.pop(0)
        if fail:
            on_failed(fail)
        else:
            KeySignIn.sign(self, url, challenge, on_signed, on_failed)


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


# -- not asking the person again --------------------------------------------------

def test_a_sign_in_refused_for_good_is_not_asked_again_on_that_connection(pool):
    pool.sign_in = signer = Signer(fail="user rejected")
    failed = failures(pool)
    sub, relay, seen = refused_read(pool, MINE)
    for kinds in ([1], [7]):
        again = pool.subscribe([MINE], [{"kinds": kinds}])
        relay.says("CLOSED", again.sub_id, "auth-required: sign in")
    event = signed(31234, [["d", "x"]])
    pool.publish([MINE], event)
    relay.says("OK", event["id"], False, "auth-required: sign in to write")
    settle()
    assert len(signer.asked) == 1 and len(failed) == 1
    relay.drop()
    relay.open()                                 # a new connection may ask once more
    relay.says("AUTH", "fresh")
    relay.says("CLOSED", sub.sub_id, "auth-required: sign in")
    assert len(signer.asked) == 2


def test_a_relay_that_refused_the_sign_in_is_not_asked_again_on_that_connection(pool):
    pool.sign_in = signer = Signer()
    sub, relay, seen = refused_read(pool, MINE)
    accept_sign_in(relay, ok=False, message="restricted: not a member")
    again = pool.subscribe([MINE], [{"kinds": [1]}])
    relay.says("CLOSED", again.sub_id, "auth-required: sign in")
    assert len(signer.asked) == 1 and len(relay.sent_of("AUTH")) == 1


def test_a_sign_in_that_only_went_unanswered_is_asked_again(pool):
    pool.sign_in = signer = Signer(fail="timed out waiting for signer", final=False)
    sub, relay, seen = refused_read(pool, MINE)
    again = pool.subscribe([MINE], [{"kinds": [1]}])
    relay.says("CLOSED", again.sub_id, "auth-required: sign in")
    assert len(signer.asked) == 2


def test_a_relay_that_drops_the_connection_asks_the_person_once(pool):
    pool.sign_in = signer = Signer(waits=True)
    started = []
    pool.signing_in.connect(started.append)
    failed = failures(pool)
    sub, relay, seen = refused_read(pool, MINE)
    for n in range(5):                           # dropped and opened again, five times
        relay.drop()
        relay.open()
        relay.says("AUTH", f"challenge-{n}")
        relay.says("CLOSED", sub.sub_id, "auth-required: sign in")
    assert len(signer.asked) == 1 and len(started) == 1 and failed == []
    signer.answer()                              # yes, for a connection since lost
    assert len(signer.asked) == 2 and signer.asked[1][1] == "challenge-4"
    signer.answer()                              # the app signs the newer one
    accept_sign_in(relay)
    assert relay.sent_of("REQ")[-1][1] == sub.sub_id


def test_a_late_no_for_a_lost_connection_counts_on_the_new_one(pool):
    pool.sign_in = signer = Signer(waits=True)
    sub, relay, seen = refused_read(pool, MINE)
    relay.drop()
    relay.open()
    relay.says("AUTH", "fresh")
    relay.says("CLOSED", sub.sub_id, "auth-required: sign in")
    signer.answer(fail="user rejected")
    again = pool.subscribe([MINE], [{"kinds": [1]}])
    closed = []
    again.relay_closed.connect(lambda url, why: closed.append(why))
    relay.says("CLOSED", again.sub_id, "auth-required: sign in")
    assert len(signer.asked) == 1 and closed == ["auth-required: sign in"]


def test_a_late_yes_nobody_waits_for_any_more_is_let_go(pool):
    pool.sign_in = signer = Signer(waits=True)
    sub, relay, seen = refused_read(pool, MINE)
    relay.drop()
    signer.answer()
    assert relay.sent_of("AUTH") == [] and relay._sign_ins == {}


# -- the account in use -----------------------------------------------------------

def test_the_account_is_checked_again_when_the_signer_is_asked(pool):
    pool.sign_in = signer = Signer()
    sub, relay, seen = refused_read(pool, MINE, challenge=None)
    signer.may = False                           # another account is in use by now
    relay.says("AUTH", CHALLENGE)
    assert signer.asked == [] and relay.sent_of("AUTH") == []
    assert seen["closed"]


def test_the_account_is_checked_again_when_the_signer_answers(pool):
    pool.sign_in = signer = Signer(waits=True)
    sub, relay, seen = refused_read(pool, MINE)
    signer.pubkey = "f" * 64                     # the account changed meanwhile
    signer.answer()
    assert relay.sent_of("AUTH") == [] and seen["closed"]


def test_connections_signed_in_as_another_account_close(pool):
    pool.sign_in = Signer()
    sub, relay, seen = refused_read(pool, MINE)
    accept_sign_in(relay)
    other = pool.get_or_create("wss://other.example")
    closed = []
    relay.disconnected.connect(lambda: closed.append(MINE))
    other.disconnected.connect(lambda: closed.append("other"))
    pool.end_sign_ins(keep=pool.sign_in.pubkey)
    assert closed == []                          # still the same account
    pool.end_sign_ins(keep="")                   # signed out
    assert closed == [MINE] and relay.signed_in_with(pool.sign_in) == set()


def test_a_channel_sign_in_stays_when_the_account_changes(pool):
    pool.sign_in = Signer()
    channel = Signer(key=bytes.fromhex("5d" * 32))
    sub = pool.subscribe([MINE], [{"kinds": [24133]}], sign_in=channel)
    relay = pool.get_or_create(MINE)
    relay.says("AUTH", CHALLENGE)
    relay.says("CLOSED", sub.sub_id, "auth-required: messages are for their owner")
    accept_sign_in(relay)
    closed = []
    relay.disconnected.connect(lambda: closed.append(MINE))
    pool.end_sign_ins(keep="")
    assert closed == []


# -- the rest ---------------------------------------------------------------------

def test_a_publish_sent_again_after_a_slow_sign_in_gets_its_full_wait(pool):
    pool.sign_in = signer = Signer(waits=True)
    event = signed(31234, [["d", "x"]])
    job = pool.publish([MINE, "wss://quick.example"], event, timeout_ms=1000)
    relay = pool.get_or_create(MINE)
    relay.says("AUTH", CHALLENGE)
    relay.says("OK", event["id"], False, "auth-required: sign in to write")
    time.sleep(0.4)                              # the person takes a while
    signer.answer()
    accept_sign_in(relay)
    assert job._timer.remainingTime() > 900


def test_a_challenge_too_long_to_be_one_gets_no_sign_in(pool):
    pool.sign_in = signer = Signer()
    sub, relay, seen = refused_read(pool, MINE, challenge="x" * 1025)
    assert signer.asked == [] and relay.sent_of("AUTH") == [] and seen["closed"]


def test_the_pool_speaks_only_of_its_own_signers_sign_ins(pool):
    pool.sign_in = Signer(may=False)
    started, failed = [], failures(pool)
    pool.signing_in.connect(started.append)
    channel = Signer(key=bytes.fromhex("5d" * 32))
    sub = pool.subscribe([MINE], [{"kinds": [24133]}], sign_in=channel)
    relay = pool.get_or_create(MINE)
    relay.says("AUTH", CHALLENGE)
    relay.says("CLOSED", sub.sub_id, "auth-required: messages are for their owner")
    accept_sign_in(relay, ok=False, message="restricted")
    assert channel.asked and started == [] and failed == []


def test_a_relay_that_never_answers_the_sign_in_did_not_refuse_it(pool):
    pool.sign_in = signer = Signer()
    failed = failures(pool)
    sub, relay, seen = refused_read(pool, MINE)
    relay._sign_ins[signer.pubkey].timer.timeout.emit()
    assert failed == [(MINE, "the relay did not answer the sign-in", False)]


def test_the_person_asking_again_may_be_asked_again(pool):
    pool.sign_in = signer = Signer(fail="user rejected")
    sub, relay, seen = refused_read(pool, MINE)
    pool.forget_refusals(["wss://Mine.Example/"])        # Try Again, Save, Refresh
    again = pool.subscribe([MINE], [{"kinds": [1]}])
    relay.says("CLOSED", again.sub_id, "auth-required: sign in")
    assert len(signer.asked) == 2


def test_a_sign_in_taken_for_an_account_no_longer_in_use_closes_the_connection(pool):
    pool.sign_in = signer = Signer()
    sub, relay, seen = refused_read(pool, MINE)
    closed = []
    relay.disconnected.connect(lambda: closed.append(MINE))
    answer = relay.sent_of("AUTH")[-1][1]
    signer.pubkey = "f" * 64                     # the person switched before the relay's OK
    relay.says("OK", answer["id"], True, "")
    assert closed == [MINE] and relay.signed_in_with(signer) == set()
