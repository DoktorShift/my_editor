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
    KIND_PRIVATE_RELAYS, PrivateDraftRelays, draft_relay_address, parse_private_relays,
    private_relays_plaintext,
)
from nostr.outbox import writer as outbox_writer  # noqa: E402
from nostr.outbox.directory import RelayDirectory  # noqa: E402
from tests.outbox_fakes import (  # noqa: E402
    ABSENT, NOW, PK, UNKNOWN, FakeClient, FakePool, FakeQuery, FakeSessionPool, Profile,
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


@pytest.mark.parametrize("typed, address", [
    ("wss://Relay.Example/", "wss://relay.example"),
    ("  wss://relay.example:443  ", "wss://relay.example"),
    ("wss://nas.local:4848", "wss://nas.local:4848"),      # the person's own network
    ("wss://10.0.0.5", "wss://10.0.0.5"),
    ("ws://localhost:7777", "ws://localhost:7777"),        # a relay on this computer
    ("ws://127.0.0.1:4869", "ws://127.0.0.1:4869"),
    ("ws://[::1]:4869", "ws://[::1]:4869"),
    ("ws://relay.example", None),                          # readable on the way
    ("ws://192.168.1.20:7777", None),
    ("https://relay.example", None),
    ("relay.example", None),
    ("wss://", None),
    ("wss://user@relay.example", None),
    ("", None),
])
def test_an_address_typed_for_drafts(typed, address):
    assert draft_relay_address(typed) == address


def test_going_back_to_the_usual_relays_publishes_an_empty_list():
    existing = signed(KIND_PRIVATE_RELAYS, [], 'sealed:[["relay","wss://mine.example"]]')
    drafts, client, pool = lists(found(existing))
    outcomes = []
    drafts.clear(outcomes.append)
    settle(10)
    assert outcomes[0].status == outbox_writer.WRITTEN
    assert client.requests[0]["content"] == "sealed:[]" and client.requests[0]["tags"] == []
    assert parse_private_relays("[]") == []                 # read back: none, the usual relays


def test_an_account_without_a_list_has_nothing_to_stop():
    drafts, client, pool = lists(ABSENT)
    outcomes = []
    drafts.clear(outcomes.append)
    settle(10)
    assert outcomes[0].status == outbox_writer.REFUSED
    assert client.requests == [] and pool.published == []


def test_saving_publishes_the_list_and_then_drafts_follow_it():
    drafts, _client, pool = lists(ABSENT)
    directory = drafts._directory
    outcomes = []
    drafts.save(["wss://Mine.example/", "wss://two.example"], outcomes.append)
    settle(10)
    assert outcomes[0].status == outbox_writer.WRITTEN and pool.published
    assert directory.draft_relays_of(PK) == ["wss://mine.example", "wss://two.example"]


def test_saving_none_goes_back_to_the_usual_relays():
    existing = signed(KIND_PRIVATE_RELAYS, [], 'sealed:[["relay","wss://mine.example"]]')
    drafts, client, _pool = lists(found(existing))
    directory = drafts._directory
    directory.set_draft_relays(PK, ["wss://mine.example"])
    outcomes = []
    drafts.save([], outcomes.append)
    settle(10)
    assert outcomes[0].ok and client.requests[0]["content"] == "sealed:[]"
    assert directory.draft_relays_of(PK) == []


def test_saving_none_without_a_list_is_already_done():
    drafts, client, pool = lists(ABSENT)
    outcomes = []
    drafts.save([], outcomes.append)
    settle(10)
    assert outcomes[0].status == outbox_writer.UNCHANGED and outcomes[0].ok
    assert pool.published == [] and drafts._directory.draft_relays_of(PK) == []


def test_a_save_that_did_not_go_out_leaves_the_drafts_where_they_were():
    drafts, _client, pool = lists(UNKNOWN)
    directory = drafts._directory
    outcomes = []
    drafts.save(["wss://mine.example"], outcomes.append)
    settle(10)
    assert outcomes[0].status == outbox_writer.UNKNOWN_BASE and pool.published == []
    assert directory.draft_relays_of(PK) is None


def test_saving_only_unusable_addresses_is_refused_before_anything_is_asked():
    drafts, client, _pool = lists(ABSENT)
    with pytest.raises(ValueError):
        drafts.save(["not a relay"], lambda _outcome: None)
    assert client.requests == []
