# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Drafts go where the account chose to keep them (NIP-37, kind 10013).

With a private draft relay list, drafts are written only there, read
from there and from wherever private records live otherwise, and the
relay directory waits for the list while it is being read, so nothing
is saved elsewhere in the first moments after starting up.
"""

from unittest.mock import MagicMock

from nostr.draft_store import DraftStore
from nostr.draft_sync import DraftSync
from nostr.outbox import defaults, policy
from nostr.outbox.directory import RelayDirectory, ask_draft_relays
from nostr.outbox.policy import LookupState, RelayList
from tests.outbox_fakes import (
    OTHER_PK, PK, FakePool, FakeQuery, FakeRelayDirectory, Profile, settle,
)

MINE = "wss://drafts.mine.example"
HOME = "ws://192.168.1.20:7777"
MEMBER = "wss://members.example"


def own(write=("wss://w.example",), read=("wss://r.example",)) -> RelayList:
    return RelayList(write=list(write), read=list(read), state=LookupState.FOUND)


def directory(tmp_path=None, owners=(PK,)) -> RelayDirectory:
    return RelayDirectory(FakePool(), query=FakeQuery(), query_many=FakeQuery().many,
                          own_pubkeys=lambda: owners,
                          store_path=(tmp_path / "lists.json") if tmp_path else None)


def answers(d, *, reading, entitled=(), author=PK):
    got = []
    d.draft_relays(author, got.append, entitled=entitled, reading=reading)
    settle()
    return got


# -- the rule ------------------------------------------------------------------

def test_without_a_list_drafts_live_where_private_records_do():
    for reading in (False, True):
        assert policy.draft_relays(own(), [], entitled=[MEMBER], reading=reading) == \
            policy.private_relays(own(), entitled=[MEMBER], reading=reading)


def test_with_a_list_drafts_are_written_only_there():
    written = policy.draft_relays(own(), [MINE + "/", HOME], entitled=[MEMBER])
    assert written == [MINE, HOME]          # a home relay of their own is fine


def test_with_a_list_drafts_are_read_there_and_where_they_were_before():
    read = policy.draft_relays(own(), [MINE], entitled=[MEMBER], reading=True)
    assert read[0] == MINE
    assert set(policy.private_relays(own(), entitled=[MEMBER], reading=True)) <= set(read)


def test_a_long_list_is_capped():
    many = [f"wss://d{i}.example" for i in range(50)]
    assert len(policy.draft_relays(own(), many)) == defaults.PRIVATE_CAP


# -- the directory ---------------------------------------------------------------

def test_a_known_list_routes_drafts_and_announces_the_change():
    d = directory()
    changed = []
    d.changed.connect(changed.append)
    d.set_draft_relays(PK, [MINE])
    assert changed == [PK]
    assert answers(d, reading=False) == [[MINE]]
    d.set_draft_relays(PK, [MINE])
    assert changed == [PK], "the same list again changes nothing"


def test_a_list_that_could_not_be_read_keeps_what_was_known():
    # An unreachable signer must not move drafts to relays the person
    # did not choose.
    d = directory()
    d.set_draft_relays(PK, [MINE])
    d.set_draft_relays(PK, None)
    assert d.draft_relays_of(PK) == [MINE]
    assert answers(d, reading=False) == [[MINE]]


def test_no_list_at_all_is_remembered_as_none():
    d = directory()
    assert d.draft_relays_of(PK) is None
    d.set_draft_relays(PK, [])
    assert d.draft_relays_of(PK) == []
    assert answers(d, reading=False) == [policy.private_relays(d.cached(PK))]


def test_drafts_wait_while_the_list_is_being_read():
    d = directory()
    d.expect_draft_relays(PK)
    got = []
    d.draft_relays(PK, got.append)
    settle()
    assert got == [], "nothing is answered before the list is known"
    d.set_draft_relays(PK, [MINE])
    settle()
    assert got == [[MINE]]


def test_a_list_that_never_arrives_stops_the_wait():
    d = directory()
    d.expect_draft_relays(PK, timeout_ms=0)
    got = []
    d.draft_relays(PK, got.append)
    settle()
    assert got == [policy.private_relays(d.cached(PK))]


def test_an_accounts_list_is_remembered_across_launches(tmp_path):
    d = directory(tmp_path)
    d.set_draft_relays(PK, [MINE])
    d.set_draft_relays(OTHER_PK, ["wss://someone-else.example"])
    again = directory(tmp_path)
    assert again.draft_relays_of(PK) == [MINE]
    assert again.draft_relays_of(OTHER_PK) is None, "only the user's own lists are kept"


def test_a_damaged_remembered_list_is_ignored(tmp_path):
    (tmp_path / "lists.json").write_text('{"version": 1, "lists": {}, "drafts": {"%s": "x"}}'
                                         % PK, encoding="utf-8")
    assert directory(tmp_path).draft_relays_of(PK) is None


def test_ask_draft_relays_passes_the_membership_and_signer_relays():
    d = FakeRelayDirectory({PK: own()})
    profile = Profile(PK)
    profile.bunker_relays = ["wss://bunker.example"]
    got = []
    ask_draft_relays(d, profile, got.append, entitled=lambda: [MEMBER], reading=True)
    settle()
    assert d.asked("draft_relays") == [
        ("draft_relays", PK, (MEMBER,), ("wss://bunker.example",), True)]
    assert got and MEMBER in got[0]


# -- draft sync ------------------------------------------------------------------

def running_sync(d, read_draft_list):
    pool = MagicMock()
    session_pool = MagicMock()
    session_pool.get.side_effect = lambda profile, on_ready, on_error: on_ready(MagicMock())
    sync = DraftSync(relay_pool=pool, relay_directory=d, session_pool=session_pool,
                     store=DraftStore(), read_draft_list=read_draft_list)
    sync.start_for(Profile(PK))
    settle()
    return sync, pool


def subscribed(pool):
    return [call.args[0] for call in pool.subscribe.call_args_list]


def test_sync_reads_the_list_first_and_looks_for_drafts_there_too():
    d = FakeRelayDirectory({PK: own()})
    _sync, pool = running_sync(d, lambda profile, done: done([MINE]))
    assert d.asked("expect_draft_relays") == [("expect_draft_relays", PK)]
    assert subscribed(pool)[-1][0] == MINE
    assert set(policy.private_relays(own(), reading=True)) <= set(subscribed(pool)[-1])


def test_sync_without_a_list_reads_where_it_always_did():
    d = FakeRelayDirectory({PK: own()})
    _sync, pool = running_sync(d, lambda profile, done: done([]))
    assert MINE not in subscribed(pool)[-1]



# -- saving a draft --------------------------------------------------------------

def test_a_draft_is_saved_only_on_the_chosen_relays():
    from nostr.drafts import build_inner_event
    from nostr.publisher import DraftPublishJob

    d = FakeRelayDirectory({PK: own()})
    d.draft_lists[PK] = [MINE]
    job = DraftPublishJob(
        relay_pool=MagicMock(), relay_directory=d, session_pool=MagicMock(),
        profile=Profile(PK), inner_event=build_inner_event(kind=1, content="hi", pubkey_hex=PK),
        identifier="x")
    seen = []
    job._on_relays_ready = seen.append
    job.start()
    settle()
    assert seen == [[MINE]]


def test_a_directory_dropped_after_a_wait_goes_away_cleanly():
    # A wait used to keep a timer per account, connected to a closure over
    # the directory; dropping a directory then deleted that timer twice and
    # crashed a later test. Nothing may be left that outlives it.
    import gc

    d = directory()
    d.expect_draft_relays(PK)
    got = []
    d.draft_relays(PK, got.append)
    d.set_draft_relays(PK, [MINE])
    settle()
    assert got == [[MINE]]
    del d
    gc.collect()
    settle()
