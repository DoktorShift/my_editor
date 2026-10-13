# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A relay whose socket connects at once and records what is sent, for
the tests of nostr/relay.py's own protocol (signing in, NIP-42)."""

import json

from nostr import relay as relay_module
from nostr.relay import Relay, RelayPool

CHALLENGE = "a-challenge"


class FakeRelay(Relay):
    def __init__(self, url, parent=None):
        super().__init__(url, parent)
        self.sent = []

    def open(self):
        self._stopped = False
        if not self._connected:
            self._on_connected()

    def send(self, message):
        if not self._connected:
            return False
        self.sent.append(message)
        return True

    def says(self, *message):
        """The relay sends ``message``."""
        self._on_text_message(json.dumps(list(message)))

    def sent_of(self, verb):
        return [m for m in self.sent if m[0] == verb]

    def drop(self):
        self._on_disconnected()

    def reconnect(self):
        self.drop()                   # as the real socket reports its close


def fake_relay_pool(monkeypatch) -> RelayPool:
    """A real RelayPool whose relays are FakeRelays (``monkeypatch``, the
    pytest fixture, puts the real relay back after the test)."""
    monkeypatch.setattr(relay_module, "Relay", FakeRelay)
    return RelayPool()


def refused_read(pool, url, *, challenge=CHALLENGE, filters=None):
    """A subscription on ``url``, which gives the connection a challenge
    and closes the subscription until it is signed in to."""
    sub = pool.subscribe([url], filters or [{"kinds": [31234]}])
    relay = pool.get_or_create(url)
    seen = {"closed": [], "events": []}
    sub.relay_closed.connect(lambda u, why: seen["closed"].append(why))
    sub.event.connect(seen["events"].append)
    if challenge:
        relay.says("AUTH", challenge)
    relay.says("CLOSED", sub.sub_id, "auth-required: sign in to read your drafts")
    return sub, relay, seen


def accept_sign_in(relay, ok=True, message=""):
    """The relay answers the last sign-in it was sent."""
    answer = relay.sent_of("AUTH")[-1][1]
    relay.says("OK", answer["id"], ok, message)
    return answer
