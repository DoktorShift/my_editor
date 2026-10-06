# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A source followed just before quitting still reaches the relays.

The list of sources is published five seconds after the last change. On
quit it is flushed, but a key kept on this computer signs on the next
turn of the event loop, and the window closed the signer right after
the flush: the change never left. Quitting now waits for it, a few
seconds at most, before the signer and the relay sockets close.
"""

from __future__ import annotations

import inspect
import tempfile

from PySide6.QtCore import QTimer

from main_window import QUIT_SYNC_WAIT_MS, MainWindow
from nostr.imports.subscriptions import FeedSubscriptionStore
from nostr.ui.feeds_panel import FeedsPanel
from tests.outbox_fakes import FakeRelayDirectory, settle
from tests.test_imports_subscriptions import (
    PROFILE,
    FakeRelay,
    FakeScheduler,
    FakeSessionPool,
    FakeSigner,
)


class NextTurnSigner(FakeSigner):
    """Signs on the next turn of the event loop, as LocalSigner does."""

    def sign_event(self, unsigned, on_success, on_failure, **_kw):
        QTimer.singleShot(0, lambda: FakeSigner.sign_event(
            self, unsigned, on_success, on_failure))


def panel_with(relay):
    def factory(**kwargs):
        kwargs.update(session_pool=FakeSessionPool(NextTurnSigner()), relay_pool=None,
                      relay_directory=FakeRelayDirectory(),
                      cache_dir=tempfile.mkdtemp(prefix="quit-test-"),
                      query=relay, publisher=relay, scheduler=FakeScheduler(),
                      clock=lambda: 1_800_000_000)
        return FeedSubscriptionStore(**kwargs)

    panel = FeedsPanel(subscription_store_factory=factory)
    panel.bind_runtime(relay_pool=object(), relay_directory=FakeRelayDirectory(),
                       session_pool=object())
    panel.set_active_profile(PROFILE)
    settle()
    return panel


def test_a_source_added_just_before_quitting_is_sent():
    relay = FakeRelay()
    panel = panel_with(relay)
    panel._subscriptions.add_feed("https://blog.example/feed")
    assert panel.flush_subscriptions() is False      # on its way
    assert panel.wait_for_subscriptions(QUIT_SYNC_WAIT_MS) is True
    assert [f["url"] for f in relay.payload()["feeds"]] == ["https://blog.example/feed"]


def test_nothing_to_send_means_no_wait():
    panel = panel_with(FakeRelay())
    assert panel.flush_subscriptions() is True


def test_quitting_waits_before_closing_the_signer():
    source = inspect.getsource(MainWindow.closeEvent)
    assert source.index("wait_for_subscriptions") < source.index("_session_pool.close_all()")
