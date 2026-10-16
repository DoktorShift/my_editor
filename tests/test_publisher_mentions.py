# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mention handling in build_note / build_article: pure-function tests."""

from __future__ import annotations

import pytest

from nostr.bech32 import encode_npub, encode_nprofile
from nostr.publisher import (
    build_article,
    build_note,
    extract_inline_mentions,
)


# Author key for the unsigned events
AUTHOR_PK = "79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798"

# Three distinct test recipients
ALICE_PK = "01" * 32
BOB_PK   = "02" * 32
CAROL_PK = "03" * 32


def _p_tags(event: dict) -> list[list[str]]:
    return [t for t in event["tags"] if t and t[0] == "p"]


# --------------------------------------------------------------------------- #
# extract_inline_mentions                                                     #
# --------------------------------------------------------------------------- #

def test_extract_finds_npub_uri():
    npub = encode_npub(ALICE_PK)
    found = extract_inline_mentions(f"Hello nostr:{npub} how are you")
    assert found == [(ALICE_PK, "")]


def test_extract_finds_nprofile_uri_with_relay_hint():
    nprofile = encode_nprofile(ALICE_PK, ["wss://r.example"])
    found = extract_inline_mentions(f"cc nostr:{nprofile}")
    assert found == [(ALICE_PK, "wss://r.example")]


def test_extract_preserves_document_order():
    a = encode_npub(ALICE_PK)
    b = encode_npub(BOB_PK)
    found = extract_inline_mentions(f"first nostr:{b} then nostr:{a}")
    assert found == [(BOB_PK, ""), (ALICE_PK, "")]


def test_a_person_written_in_an_articles_code_is_not_mentioned():
    """Code shows an address as an example; the person is not tagged, and
    so not notified, as a quote in code quotes nothing. A note has no
    code: there it is a mention."""
    npub = encode_npub(ALICE_PK)
    body = f"Write `nostr:{npub}` in a note.\n\n```\nnostr:{npub}\n```\n"
    assert _p_tags(build_article(body, AUTHOR_PK, "how-to")) == []
    assert extract_inline_mentions(body, markdown=True) == []
    assert _p_tags(build_note(body, AUTHOR_PK)) == [["p", ALICE_PK]]


def test_extract_silently_skips_garbage():
    """A near-miss URI (bad checksum) is left alone, not raised."""
    bad = "nostr:npub1invalidcharacters00000000000000000000000"
    found = extract_inline_mentions(f"see {bad} ok?")
    assert found == []


def test_extract_returns_empty_on_no_uris():
    assert extract_inline_mentions("just plain text, no mentions here") == []


# --------------------------------------------------------------------------- #
# build_note + mentions                                                       #
# --------------------------------------------------------------------------- #

def test_build_note_no_mentions_unchanged_body():
    event = build_note("hello", AUTHOR_PK)
    assert event["content"] == "hello"
    assert _p_tags(event) == []


def test_a_note_or_article_goes_out_as_written_and_takes_no_extra_people():
    """The publish sheets add nobody beside the text (the "+ add person"
    row is gone): the builders take no mentions of their own."""
    with pytest.raises(TypeError):
        build_note("hello", AUTHOR_PK, mentions=[(ALICE_PK, "")])
    with pytest.raises(TypeError):
        build_article("hello", AUTHOR_PK, "x", mentions=[(ALICE_PK, "")])


def test_a_person_mentioned_twice_is_tagged_once_with_the_first_hint():
    first = encode_nprofile(ALICE_PK, ["wss://inline.example"])
    second = encode_nprofile(ALICE_PK, ["wss://later.example"])
    body = f"As nostr:{first} pointed out, and nostr:{second} again."
    event = build_note(body, AUTHOR_PK)
    assert event["content"] == body
    assert _p_tags(event) == [["p", ALICE_PK, "wss://inline.example"]]


def test_build_note_inline_only_still_emits_p_tag():
    """Manual paste of a URI in the body must also produce a p-tag, even
    with no chip mentions at all."""
    npub = encode_npub(BOB_PK)
    event = build_note(f"shout-out to nostr:{npub}", AUTHOR_PK)
    assert _p_tags(event) == [["p", BOB_PK]]


# --------------------------------------------------------------------------- #
# build_article + mentions                                                    #
# --------------------------------------------------------------------------- #

def test_build_article_p_tags_appear_after_d_tag_before_metadata():
    """The d tag first (rnostr takes the first tag as the address), then
    the client, then the p tags, so the article's identity stays obvious
    at the top of the tag list."""
    event = build_article(
        f"body text for nostr:{encode_npub(ALICE_PK)}",
        AUTHOR_PK,
        slug="hello",
        title="Hello",
    )
    keys = [t[0] for t in event["tags"]]
    # Expect: d, client, p, title  (no other tags in this test)
    assert keys == ["d", "client", "p", "title"]


def test_build_article_no_mentions_no_p_tags():
    event = build_article("body", AUTHOR_PK, slug="x")
    assert _p_tags(event) == []
