# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins adding one relay to the user's published relay list.

What must hold (the membership window relies on these four outcomes):

  No list found, or none could be read: nothing is published, because a
  list built from nothing would replace the person's real one (no_list).

  The new list keeps every relay already on it, adds the one relay, is
  newer than the old list, and goes to the relays the list names (added).

  A relay already listed is not added again and nothing is signed
  (already).

  A signer that says no, or relays that will not take it, change nothing
  (failed).

The safe read-modify-write underneath is pinned in test_outbox_package.py.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr import relay_list_addition as rla  # noqa: E402
from nostr.outbox import defaults, writer  # noqa: E402
from nostr.outbox.directory import RelayDirectory  # noqa: E402
from tests.outbox_fakes import (  # noqa: E402
    ABSENT, NOW, OTHER_SK, UNKNOWN, FakeClient, FakePool, FakeQuery, FakeSessionPool,
    Profile, found, settle, signed,
)

E21 = "wss://nostr.einundzwanzig.space"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(autouse=True)
def quick_read_back(monkeypatch):
    monkeypatch.setattr(writer, "READ_BACK_DELAY_MS", 0)


def run(answer, *, client=None, pool=None):
    client = client or FakeClient()
    pool = pool or FakePool()
    query = FakeQuery({10002: answer})
    directory = RelayDirectory(pool, query=query, store_path=None)
    job = rla.RelayListAddition(pool, FakeSessionPool(client), Profile(), E21,
                                directory=directory, query=query, clock=lambda: NOW)
    outcomes = []
    job.finished.connect(outcomes.append)
    job.start()
    settle(10)
    return outcomes, client, pool


def test_no_list_found_publishes_nothing():
    outcomes, client, pool = run(ABSENT)
    assert outcomes == [rla.NO_LIST]
    assert client.requests == [] and pool.published == []


def test_a_list_that_could_not_be_read_publishes_nothing():
    outcomes, client, pool = run(UNKNOWN)
    assert outcomes == [rla.NO_LIST] and client.requests == []


def test_the_new_list_keeps_everything_and_adds_the_relay():
    existing = signed(10002, [["r", "wss://a.example"], ["r", "wss://b.example", "read"]])
    outcomes, client, pool = run(found(existing))
    assert outcomes == [rla.ADDED]
    tags = client.requests[0]["tags"]
    assert tags == [["r", "wss://a.example"], ["r", "wss://b.example", "read"],
                    ["r", E21, "write"]]
    assert client.requests[0]["created_at"] > existing["created_at"]
    targets, _event = pool.published[0]
    for relay in ("wss://a.example", "wss://b.example", E21, *defaults.INDEXER_RELAYS):
        assert relay in targets


def test_a_relay_already_listed_is_left_alone():
    outcomes, client, pool = run(found(signed(10002, [["r", E21 + "/"]])))
    assert outcomes == [rla.ALREADY]
    assert client.requests == [] and pool.published == []


def test_a_signer_that_says_no_changes_nothing():
    outcomes, _client, pool = run(found(signed(10002, [["r", "wss://a.example"]])),
                                  client=FakeClient(fail="user rejected"))
    assert outcomes == [rla.FAILED] and pool.published == []


def test_relays_that_will_not_take_it_are_a_failure():
    existing = signed(10002, [["r", "wss://a.example"]])
    everyone = {"wss://a.example", E21, *defaults.INDEXER_RELAYS}
    outcomes, _client, _pool = run(found(existing), pool=FakePool(refuse=everyone))
    assert outcomes == [rla.FAILED]


def test_outcome_messages_are_plain_and_have_no_em_dashes():
    for outcome in (rla.ADDED, rla.ALREADY, rla.NO_LIST, rla.FAILED):
        text = rla.outcome_message(outcome)
        assert text and "\u2014" not in text and "10002" not in text
