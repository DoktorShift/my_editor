# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins which version of a draft wins, across devices and lagging relays.

What must hold (found in a live test against real relays):

  Two saves of one draft within a second get different times, the later
  one later, so relays keep the newest text (NIP-01 keeps the lower id on
  a tie, which is as often the earlier text).

  Of two versions with the same time, the lower id wins, as on relays.

  A deletion removes only a draft it is newer than, and once a draft is
  deleted, an older copy a relay still holds does not bring it back.

  Only versions signed by the account count.

  Loading ends when every relay has ended, also when one cannot be
  reached.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QCoreApplication, QObject, Signal  # noqa: E402

from nostr import crypto, events  # noqa: E402
from nostr.draft_store import DraftStore  # noqa: E402
from nostr.draft_sync import DraftSync  # noqa: E402
from nostr.drafts import (  # noqa: E402
    DraftWrapMeta, build_draft_wrap, supersedes, wrap_time,
)

SK = bytes.fromhex("3c" * 32)
PK = crypto.get_public_key(SK).hex()


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QCoreApplication.instance() or QCoreApplication(sys.argv)


def meta(event_id, created_at, ciphertext="CT", ident="x"):
    return DraftWrapMeta(identifier=ident, inner_kind=1, event_id=event_id, pubkey=PK,
                         created_at=created_at, expiration=None, ciphertext=ciphertext)


# -- writing ------------------------------------------------------------------------------

def test_two_saves_within_a_second_get_later_and_later_times():
    first = wrap_time(PK, "same-second")
    second = wrap_time(PK, "same-second")
    assert second > first


def test_a_new_version_is_dated_after_the_newest_known_one():
    assert wrap_time(PK, "known", after=4_000_000_000) == 4_000_000_001


def test_built_wraps_of_one_draft_never_share_a_time():
    times = [build_draft_wrap(identifier="d1", inner_kind=1, encrypted_content="c",
                              pubkey_hex=PK, client_name="MyEditor")["created_at"]
             for _ in range(3)]
    assert times == sorted(set(times))


# -- which version wins --------------------------------------------------------------------

def test_newer_wins_and_on_a_tie_the_lower_id():
    assert supersedes(11, "ff", 10, "00")
    assert not supersedes(9, "00", 10, "ff")
    assert supersedes(10, "01", 10, "02")
    assert not supersedes(10, "02", 10, "01")


def test_a_same_second_version_with_the_lower_id_replaces_the_stored_one():
    store = DraftStore()
    store.upsert_skeleton(meta("bb", 100))
    store.upsert_skeleton(meta("aa", 100))
    assert store.get("x").event_id == "aa"
    store.upsert_skeleton(meta("cc", 100))
    assert store.get("x").event_id == "aa"


def test_an_older_deletion_from_a_lagging_relay_leaves_the_draft():
    store = DraftStore()
    store.upsert_skeleton(meta("e2", 200))
    store.upsert_skeleton(meta("d1", 100, ciphertext=""))     # deletion, older
    assert store.get("x") is not None


def test_a_deleted_draft_is_not_brought_back_by_an_older_copy():
    store = DraftStore()
    store.upsert_skeleton(meta("e1", 100))
    store.upsert_skeleton(meta("d2", 200, ciphertext=""))     # deleted
    assert store.get("x") is None
    store.upsert_skeleton(meta("e1", 100))                    # a relay that missed it
    assert store.get("x") is None
    store.upsert_skeleton(meta("e3", 300))                    # written again since
    assert store.get("x") is not None


# -- what is accepted from relays -----------------------------------------------------------

def make_sync():
    # The wraps below are dated in 1970, so the clock is too: they expire
    # 90 days after it (NIP-40), as every draft wrap does.
    sync = DraftSync(read_draft_list=lambda _profile, done: done([]), relay_pool=MagicMock(), relay_directory=MagicMock(),
                     session_pool=MagicMock(), store=DraftStore(clock=lambda: 1_000),
                     clock=lambda: 1_000)
    profile = MagicMock()
    profile.user_pubkey = PK
    sync._profile = profile
    sync._store.bind_profile(PK)
    sync._bunker = MagicMock()
    return sync


def signed_wrap(content="CT", created_at=100):
    unsigned = build_draft_wrap(identifier="x", inner_kind=1, encrypted_content=content,
                                pubkey_hex=PK, client_name="MyEditor", created_at=created_at)
    return events.sign_event(unsigned, SK)


def test_only_the_accounts_own_signature_counts():
    sync = make_sync()
    forged = dict(signed_wrap(content=""), sig="00" * 64)      # a forged deletion
    sync._store.upsert_skeleton(meta("e1", 50))
    sync._on_wrap_event(forged)
    assert sync._store.get("x") is not None
    sync._on_wrap_event(signed_wrap(content="", created_at=60))  # a real one
    assert sync._store.get("x") is None


class FakeSubscription(QObject):
    event = Signal(dict)
    eose = Signal()
    relay_eose = Signal(str)
    relay_closed = Signal(str, str)
    relay_failed = Signal(str, str)

    def close(self):
        pass


def test_loading_ends_when_a_relay_cannot_be_reached():
    sync = make_sync()
    subscription = FakeSubscription()
    sync._relay_pool.subscribe.return_value = subscription
    sync._read_relays = ["wss://up.example", "wss://down.example/"]
    sync._store.set_loading(True)
    sync._open_subscription()
    subscription.relay_eose.emit("wss://up.example")
    assert sync._store.is_loading
    subscription.relay_failed.emit("wss://down.example", "host not found")
    assert not sync._store.is_loading
