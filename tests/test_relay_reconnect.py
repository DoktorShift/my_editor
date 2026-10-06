# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins reconnecting a relay that carries subscriptions.

What must hold: a relay a subscription needs reopens its socket after it
drops, waiting longer after each failed attempt (up to a minute) and
starting over once it connects; a relay nobody needs, or one closed on
purpose, is not reopened.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr import relay as relay_module  # noqa: E402
from nostr.relay import RECONNECT_FIRST_MS, RECONNECT_LONGEST_MS, Relay  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


class Recorder(Relay):
    """A relay whose socket is never opened; open() calls are counted."""

    def __init__(self):
        super().__init__("wss://relay.example")
        self.opens = 0

    def open(self):
        self._stopped = False
        self.opens += 1


def drop(relay):
    relay._connected = True
    relay._on_disconnected()


def test_a_needed_relay_reconnects_with_growing_waits():
    relay = Recorder()
    relay.hold()
    drop(relay)
    assert relay._retry.isActive() and relay._retry.interval() == RECONNECT_FIRST_MS
    relay._retry.stop()                      # as when the wait ends by itself
    relay._retry.timeout.emit()
    assert relay.opens == 1
    relay._on_error(None)                    # the attempt failed
    assert relay._retry.interval() == RECONNECT_FIRST_MS * 2
    for _ in range(10):
        relay._retry.stop()
        relay._on_error(None)
    assert relay._retry.interval() == RECONNECT_LONGEST_MS


def test_connecting_starts_the_waits_over():
    relay = Recorder()
    relay.hold()
    drop(relay)
    relay._retry.stop()
    relay._on_error(None)
    relay._on_connected()
    relay._retry.stop()
    drop(relay)
    assert relay._retry.interval() == RECONNECT_FIRST_MS


def test_a_relay_nobody_needs_is_not_reopened():
    relay = Recorder()
    drop(relay)
    assert not relay._retry.isActive()
    relay.hold()
    relay.release()
    drop(relay)
    assert not relay._retry.isActive()


def test_a_relay_closed_on_purpose_is_not_reopened():
    relay = Recorder()
    relay.hold()
    relay.close()
    drop(relay)
    assert not relay._retry.isActive()
