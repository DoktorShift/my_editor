# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins publishing the account's media server list (kind 10063).

What must hold: the servers go out in the order given, as full
addresses; a list that could not be read is never replaced; any tag that
is not a server is kept; nothing is sent when the list already says
exactly this; an empty list is never published.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.blossom.server_list import parse_server_list  # noqa: E402
from nostr.media_server_list import publish_server_list, server_list_tags  # noqa: E402
from nostr.outbox import writer as outbox_writer  # noqa: E402
from nostr.outbox.directory import RelayDirectory  # noqa: E402
from tests.outbox_fakes import (  # noqa: E402
    ABSENT, NOW, UNKNOWN, FakeClient, FakePool, FakeQuery, FakeSessionPool, Profile,
    found, settle, signed,
)

A = "https://a.example"
B = "https://b.example"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def run(answer, servers):
    client = FakeClient()
    pool = FakePool()
    query = FakeQuery({10063: answer})
    directory = RelayDirectory(pool, query=query, store_path=None)
    writer = publish_server_list(servers=servers, pool=pool, directory=directory,
                                 session_pool=FakeSessionPool(client), profile=Profile(),
                                 query=query, clock=lambda: NOW)
    outcomes = []
    writer.finished.connect(outcomes.append)
    writer.start()
    settle(10)
    return outcomes[0], client, pool


def test_the_servers_go_out_in_order_as_full_addresses():
    outcome, client, pool = run(ABSENT, [B + "/", A])
    assert outcome.status == outbox_writer.WRITTEN
    assert client.requests[0]["kind"] == 10063
    assert client.requests[0]["tags"] == [["server", B], ["server", A]]
    assert parse_server_list(outcome.event) == [B, A]
    assert pool.published


def test_a_list_that_could_not_be_read_is_never_replaced():
    outcome, client, pool = run(UNKNOWN, [A])
    assert outcome.status == outbox_writer.UNKNOWN_BASE
    assert client.requests == [] and pool.published == []


def test_other_tags_are_kept_and_old_servers_replaced():
    existing = signed(10063, [["server", A], ["alt", "media servers"]])
    outcome, client, _pool = run(found(existing), [B])
    assert client.requests[0]["tags"] == [["server", B], ["alt", "media servers"]]


def test_the_same_list_is_not_sent_again():
    existing = signed(10063, [["server", A], ["server", B]])
    outcome, client, pool = run(found(existing), [A, B])
    assert outcome.status == outbox_writer.UNCHANGED
    assert client.requests == [] and pool.published == []


def test_an_empty_list_is_never_published():
    with pytest.raises(ValueError):
        publish_server_list(servers=["not a server", "http://plain.example"], pool=None,
                            directory=None, session_pool=None, profile=Profile())


def test_tags_are_normalized_like_the_reader_does():
    assert server_list_tags(["https://A.example/x", A], None) == [["server", A]]
