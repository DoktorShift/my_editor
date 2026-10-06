# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the private relay list for drafts (NIP-37, kind 10013).

What must hold: the relays travel encrypted in the content as
``[["relay", url], ...]`` and never as plain tags; reading tells "none"
apart from "could not read or decrypt"; a list that could not be read is
never replaced; an empty list is never published.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.draft_relays import (  # noqa: E402
    KIND_PRIVATE_RELAYS, PrivateDraftRelays, parse_private_relays, private_relays_plaintext,
)
from nostr.outbox import writer as outbox_writer  # noqa: E402
from nostr.outbox.directory import RelayDirectory  # noqa: E402
from tests.outbox_fakes import (  # noqa: E402
    ABSENT, NOW, UNKNOWN, FakeClient, FakePool, FakeQuery, FakeSessionPool, Profile,
    found, settle, signed,
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


class SealingClient(FakeClient):
    """Encrypts to self by wrapping the text, so a test can see what was sealed."""

    def __init__(self, *, refuse=False, **kw):
        super().__init__(**kw)
        self.refuse = refuse

    def nip44_encrypt_self(self, plaintext, on_success, on_failure):
        if self.refuse:
            on_failure("user rejected")
        else:
            on_success("sealed:" + plaintext)

    def nip44_decrypt_self(self, ciphertext, on_success, on_failure):
        if self.refuse or not ciphertext.startswith("sealed:"):
            on_failure("cannot decrypt")
        else:
            on_success(ciphertext[len("sealed:"):])


def lists(answer, client=None):
    client = client or SealingClient()
    pool = FakePool()
    query = FakeQuery({KIND_PRIVATE_RELAYS: answer})
    directory = RelayDirectory(pool, query=query, store_path=None)
    return PrivateDraftRelays(profile=Profile(), pool=pool,
                              session_pool=FakeSessionPool(client), directory=directory,
                              query=query, clock=lambda: NOW), client, pool


def test_the_list_is_read_and_decrypted():
    event = signed(KIND_PRIVATE_RELAYS, [],
                   'sealed:[["relay","wss://Mine.example/"],["relay","wss://two.example"]]')
    drafts, _client, _pool = lists(found(event))
    got = []
    drafts.read(got.append)
    assert got == [["wss://mine.example", "wss://two.example"]]


def test_none_and_unreadable_are_told_apart():
    for answer, expected in ((ABSENT, []), (UNKNOWN, None)):
        drafts, _client, _pool = lists(answer)
        got = []
        drafts.read(got.append)
        assert got == [expected]
    undecryptable = signed(KIND_PRIVATE_RELAYS, [], "garbage")
    drafts, _client, _pool = lists(found(undecryptable))
    got = []
    drafts.read(got.append)
    assert got == [None]


def test_publishing_encrypts_the_relays_into_the_content():
    drafts, client, pool = lists(ABSENT)
    outcomes = []
    drafts.publish(["wss://mine.example/", "wss://mine.example"], outcomes.append)
    settle(10)
    assert outcomes[0].status == outbox_writer.WRITTEN
    sent = client.requests[0]
    assert sent["kind"] == KIND_PRIVATE_RELAYS and sent["tags"] == []
    assert sent["content"] == 'sealed:[["relay","wss://mine.example"]]'
    assert pool.published


def test_a_list_that_could_not_be_read_is_never_replaced():
    drafts, client, pool = lists(UNKNOWN)
    outcomes = []
    drafts.publish(["wss://mine.example"], outcomes.append)
    settle(10)
    assert outcomes[0].status == outbox_writer.UNKNOWN_BASE
    assert client.requests == [] and pool.published == []


def test_a_signer_that_will_not_encrypt_changes_nothing():
    drafts, client, pool = lists(ABSENT, SealingClient(refuse=True))
    outcomes = []
    drafts.publish(["wss://mine.example"], outcomes.append)
    assert outcomes[0].status == outbox_writer.FAILED and pool.published == []


def test_an_empty_list_is_never_published():
    with pytest.raises(ValueError):
        private_relays_plaintext(["not a relay"])


def test_a_list_that_is_not_json_is_not_guessed_at():
    with pytest.raises(ValueError):
        parse_private_relays('{"relay": "wss://x.example"}')
    assert parse_private_relays('[["r", "wss://x.example"], ["relay"]]') == []
