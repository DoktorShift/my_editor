# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins signing in to the relays of a signer app's channel (NIP-42).

What must hold: a channel relay that asks for a sign-in gets one with
the channel's own key, which says nothing about the account; and a
signer app is asked, when it pairs, for permission to sign the account's
own sign-ins.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr import bunker, crypto  # noqa: E402
from tests.outbox_fakes import PK  # noqa: E402
from tests.relay_fakes import accept_sign_in, fake_relay_pool  # noqa: E402

STRANGER = "wss://stranger.example"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def pool(monkeypatch):
    return fake_relay_pool(monkeypatch)


def test_a_signer_apps_channel_signs_in_with_its_own_key(pool):
    channel_key = bytes.fromhex("4c" * 32)
    client = bunker.BunkerClient(pool)
    client.listen_for_nostrconnect([STRANGER], "secret", lambda _pk: None,
                                   lambda _why: None, local_sk=channel_key)
    relay = pool.get_or_create(STRANGER)
    sub_id = relay.sent_of("REQ")[0][1]
    relay.says("AUTH", "channel")
    relay.says("CLOSED", sub_id, "auth-required: messages are for their owner")
    answer = accept_sign_in(relay)
    assert answer["pubkey"] == crypto.get_public_key(channel_key).hex() != PK
    assert len(relay.sent_of("REQ")) == 2
    client.close()


def test_a_signer_app_is_asked_for_permission_to_sign_in():
    assert "sign_event:22242" in bunker.DEFAULT_PERMS.split(",")
