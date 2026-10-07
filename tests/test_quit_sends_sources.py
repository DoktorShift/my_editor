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
from tests.outbox_fakes import FakeRelayDirectory, settle
from tests.test_imports_subscriptions import (
    PROFILE,
    FakeRelay,
    FakeScheduler,
    FakeSessionPool,
    FakeSigner,
)
import pytest
from tests.widget_lifetime import delete_new_windows


@pytest.fixture(autouse=True)
def _windows_deleted():
    """Every window and panel a test makes is deleted after it: left to
    the cycle collector, one without a parent can crash it."""
    yield from delete_new_windows()


class NextTurnSigner(FakeSigner):
    """Signs on the next turn of the event loop, as LocalSigner does."""

    def sign_event(self, unsigned, on_success, on_failure, **_kw):
        QTimer.singleShot(0, lambda: FakeSigner.sign_event(
            self, unsigned, on_success, on_failure))


def store_with(relay):
    store = FeedSubscriptionStore(
        session_pool=FakeSessionPool(NextTurnSigner()), relay_pool=None,
        relay_directory=FakeRelayDirectory(), cache_dir=tempfile.mkdtemp(prefix="quit-test-"),
        query=relay, publisher=relay, scheduler=FakeScheduler(),
        clock=lambda: 1_800_000_000)
    store.bind_profile(PROFILE)
    settle()
    return store


def test_a_source_added_just_before_quitting_is_sent():
    relay = FakeRelay()
    store = store_with(relay)
    store.add_feed("https://blog.example/feed")
    store.flush()
    assert store.is_busy                           # on its way
    assert store.wait_until_settled(QUIT_SYNC_WAIT_MS) is True
    assert [f["url"] for f in relay.payload()["feeds"]] == ["https://blog.example/feed"]


def test_nothing_to_send_means_no_wait():
    store = store_with(FakeRelay())
    store.flush()
    assert not store.is_busy


def test_quitting_waits_before_closing_the_signer():
    source = inspect.getsource(MainWindow.closeEvent)
    assert source.index("imports.flush()") < source.index("wait_until_settled")
    assert source.index("wait_until_settled") < source.index("_session_pool.close_all()")


def test_nothing_can_be_typed_while_quitting_waits(monkeypatch):
    """Engine review M2: while quitting waited for the list of sources,
    the window stayed editable and what was typed then was lost without
    a word. It takes no more input while it waits."""
    import types
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QMainWindow, QStatusBar, QTabWidget
    import main_window
    from main_window import MainWindow
    monkeypatch.setattr(main_window.workspace, "discard_workspace", lambda: None)
    host = QMainWindow()
    seen = {}

    class Subscriptions:
        is_busy = True

        def wait_until_settled(self, ms):
            seen["enabled while waiting"] = host.isEnabled()
            seen["status"] = host.status.currentMessage()
            return True

    host._imports = types.SimpleNamespace(flush=lambda: None, subscriptions=Subscriptions())
    host._resolve_unsaved_before_closing = lambda: True
    host._asset_manager = types.SimpleNamespace(flush=lambda: None)
    host._save_session = lambda: None
    host.tabs = QTabWidget()
    host.status = QStatusBar()
    host._session_pool = types.SimpleNamespace(close_all=lambda: None)
    host._relay_pool = types.SimpleNamespace(close_all=lambda: None)
    MainWindow.closeEvent(host, QCloseEvent())
    assert seen == {"enabled while waiting": False,
                    "status": "Saving your list of sources\u2026"}
