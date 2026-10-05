# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins adding one relay to the user's published relay list.

What must hold:

  No list found means nothing is published: a list built from nothing
  would replace the person's real one.

  A list that is not this person's, or not validly signed, counts as not
  found.

  The new list keeps every relay already on it, adds the one relay, is
  newer than the old list, and goes to the relays the list names.

  A relay already listed is not added again and nothing is signed.

Relays, the signer and publishing are fakes; signatures are real.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr import crypto, events  # noqa: E402
from nostr import relay_list_addition as rla  # noqa: E402

SK = bytes.fromhex("4c" * 32)
PK = crypto.get_public_key(SK).hex()
OTHER_SK = bytes.fromhex("5d" * 32)
E21 = "wss://nostr.einundzwanzig.space"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class Profile:
    user_pubkey = PK
    bunker_relays = ("wss://bunker.example",)


class FakeClient:
    def __init__(self, fail=None):
        self.fail = fail
        self.signed = []

    def sign_event(self, unsigned, on_success, on_failure):
        if self.fail:
            on_failure(self.fail)
            return
        self.signed.append(unsigned)
        on_success(events.sign_event(dict(unsigned), SK))


class FakeSessionPool:
    def __init__(self, client):
        self.client = client

    def get(self, profile, on_ready, on_error):
        on_ready(self.client)


class FakeJob(QObject):
    all_done = Signal(list)


class FakePool:
    def __init__(self, results):
        self.results = results
        self.published = []

    def publish(self, urls, event):
        self.published.append((list(urls), event))
        job = FakeJob()
        self._job = job

        def settle():
            job.all_done.emit(self.results)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, settle)
        return job


def relay_list(tags, sk=SK, created_at=1_700_000_000):
    return events.sign_event({"kind": 10002, "content": "", "tags": tags,
                              "created_at": created_at}, sk)


def run(existing, *, client=None, results=(("wss://a.example", True, ""),), qt_app=None):
    client = client or FakeClient()
    pool = FakePool(list(results))
    outcomes = []

    def fetch(_pool, relays, *, filters, on_done, timeout_ms, parent):
        run.queried = (relays, filters)
        on_done(existing)

    job = rla.RelayListAddition(pool, FakeSessionPool(client), Profile(), E21,
                                fetch=fetch, clock=lambda: 1_800_000_000)
    job.finished.connect(outcomes.append)
    job.start()
    QApplication.processEvents()
    return outcomes, client, pool


def test_no_list_found_publishes_nothing():
    outcomes, client, pool = run(None)
    assert outcomes == [rla.NO_LIST]
    assert client.signed == [] and pool.published == []


def test_someone_elses_list_counts_as_not_found():
    outcomes, client, _ = run(relay_list([["r", "wss://a.example"]], sk=OTHER_SK))
    assert outcomes == [rla.NO_LIST] and client.signed == []


def test_a_tampered_list_counts_as_not_found():
    event = relay_list([["r", "wss://a.example"]])
    event["tags"].append(["r", "wss://evil.example"])
    outcomes, client, _ = run(event)
    assert outcomes == [rla.NO_LIST] and client.signed == []


def test_the_new_list_keeps_everything_and_adds_the_relay():
    existing = relay_list([["r", "wss://a.example"], ["r", "wss://b.example", "read"]])
    outcomes, client, pool = run(existing)
    assert outcomes == [rla.ADDED]
    tags = client.signed[0]["tags"]
    assert tags[:2] == [["r", "wss://a.example"], ["r", "wss://b.example", "read"]]
    assert tags[2] == ["r", E21, "write"]
    assert client.signed[0]["created_at"] > existing["created_at"]
    targets, published = pool.published[0]
    assert published["pubkey"] == PK
    for relay in ("wss://a.example", "wss://b.example", E21, "wss://purplepag.es"):
        assert relay in targets


def test_a_relay_already_listed_is_left_alone():
    outcomes, client, pool = run(relay_list([["r", E21 + "/"]]))
    assert outcomes == [rla.ALREADY]
    assert client.signed == [] and pool.published == []


def test_a_signer_that_says_no_changes_nothing():
    outcomes, _, pool = run(relay_list([["r", "wss://a.example"]]),
                            client=FakeClient(fail="user rejected"))
    assert outcomes == [rla.FAILED] and pool.published == []


def test_no_relay_taking_the_list_is_a_failure():
    outcomes, _, _ = run(relay_list([["r", "wss://a.example"]]),
                         results=(("wss://a.example", False, "blocked"),))
    assert outcomes == [rla.FAILED]


def test_the_lookup_asks_the_users_relays_and_the_list_specialists():
    run(None)
    relays, filters = run.queried
    assert relays[0] == "wss://bunker.example"
    assert "wss://purplepag.es" in relays
    assert filters == [{"kinds": [10002], "authors": [PK], "limit": 1}]


def test_outcome_messages_are_plain_and_have_no_em_dashes():
    for outcome in (rla.ADDED, rla.ALREADY, rla.NO_LIST, rla.FAILED):
        text = rla.outcome_message(outcome)
        assert text and "\u2014" not in text and "10002" not in text
