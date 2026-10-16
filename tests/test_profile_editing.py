# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the network half of Edit Profile against the real writer: the
fields changed are set, every other field of the profile is kept."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.outbox import writer as outbox_writer  # noqa: E402
from nostr.outbox.directory import RelayDirectory  # noqa: E402
from nostr.profile_editing import ProfileEditing  # noqa: E402
from tests.outbox_fakes import (  # noqa: E402
    ABSENT, NOW, FakeClient, FakePool, FakeQuery, FakeSessionPool, Profile, found,
    settle, signed,
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def editing(answer, client):
    pool = FakePool()
    query = FakeQuery({0: answer})
    directory = RelayDirectory(pool, query=query, store_path=None)
    return ProfileEditing(profile=Profile(), pool=pool, session_pool=FakeSessionPool(client),
                          directory=directory, query=query, clock=lambda: NOW), pool


def test_saving_keeps_every_field_the_window_does_not_show():
    existing = signed(0, [], json.dumps({"name": "alice", "bot": False,
                                         "lud16": "a@w.example", "custom": "kept"}))
    client = FakeClient()
    edit, pool = editing(found(existing), client)
    outcomes = []
    edit.save({"about": "Hello", "lud16": ""}, outcomes.append)
    settle(10)
    assert outcomes[0].status == outbox_writer.WRITTEN
    assert json.loads(client.requests[0]["content"]) == {
        "name": "alice", "bot": False, "custom": "kept", "about": "Hello"}


def test_an_account_without_a_profile_gets_one():
    client = FakeClient()
    edit, _pool = editing(ABSENT, client)
    outcomes = []
    edit.save({"display_name": "Alice"}, outcomes.append)
    settle(10)
    assert outcomes[0].status == outbox_writer.WRITTEN
    assert json.loads(client.requests[0]["content"]) == {"display_name": "Alice"}


def test_reading_asks_for_the_accounts_profile():
    client = FakeClient()
    edit, _pool = editing(ABSENT, client)
    results = []
    edit.read(results.append)
    assert results and results[0] is ABSENT
