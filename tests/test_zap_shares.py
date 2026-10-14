# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins how an article's zaps are shared (nostr/zap_shares.py), the same
model as Standup's editor so an article split in one reads the same in
the other: the appendix's weight rules, whole percentages that add up to
100 with the author keeping the rest, shares held within what is left,
and the tags written author first, every one naming a relay."""

import pytest

from nostr import zap_shares as zs

ALICE, BOB, CAROL = "a" * 64, "b" * 64, "c" * 64
RELAY = "wss://relay.example"


def test_the_appendix_rules_for_weights():
    assert zs.read_zaps([("zap", ALICE, RELAY), ("zap", BOB)]) == [(ALICE, RELAY, 1.0), (BOB, "", 1.0)]
    assert zs.read_zaps([("zap", ALICE, "", "3"), ("zap", BOB, "")]) == [(ALICE, "", 3.0)]
    assert zs.read_zaps([("zap", ALICE, "", "0"), ("zap", BOB, "", "-1"),
                         ("zap", CAROL, "", "x"), ("zap", "npub1x", "", "1")]) == []
    assert zs.read_zaps([("zap", ALICE, RELAY, "1"), ("zap", ALICE.upper(), "", "2")]) == [
        (ALICE, RELAY, 3.0)]


@pytest.mark.parametrize("weights, percents", [
    ([1, 1], [50, 50]), ([2, 1, 1], [50, 25, 25]), ([1, 1, 1], [34, 33, 33]), ([], []), ([0], [0]),
])
def test_percentages_add_up_to_a_hundred(weights, percents):
    assert zs.percentages(weights) == percents


def test_shares_leave_the_author_to_the_rest():
    zaps = [("zap", ALICE, "", "2"), ("zap", BOB, RELAY, "1"), ("zap", CAROL, "", "1")]
    shares = zs.shares_of(zaps, ALICE)
    assert shares == {BOB: 25, CAROL: 25} and zs.author_share(shares) == 50
    assert zs.relays_of(zaps)[BOB] == RELAY


def test_a_share_is_held_within_what_the_others_leave():
    shares = {BOB: 30, CAROL: 50}
    assert zs.most_for(shares, BOB) == 50
    assert zs.with_share(shares, BOB, 90)[BOB] == 50
    assert zs.with_share(shares, BOB, -3)[BOB] == 0


def test_evenly_with_the_author_who_keeps_the_rest():
    shares = zs.evenly([BOB, CAROL])
    assert shares == {BOB: 33, CAROL: 33} and zs.author_share(shares) == 34


def test_tags_name_the_author_first_and_always_a_relay():
    assert zs.zap_tags({BOB: 30, CAROL: 0}, ALICE, {BOB: RELAY}, "wss://fallback") == (
        ("zap", ALICE, "wss://fallback", "70"), ("zap", BOB, RELAY, "30"))
    assert zs.zap_tags({BOB: 0}, ALICE, {}) == ()
    assert zs.zap_tags({BOB: 100}, ALICE, {BOB: RELAY}) == (("zap", BOB, RELAY, "100"),)


def test_tags_written_read_back_the_same():
    tags = zs.zap_tags({BOB: 40}, ALICE, {ALICE: RELAY, BOB: RELAY})
    assert zs.shares_of(tags, ALICE) == {BOB: 40}
