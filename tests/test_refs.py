# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins references to notes and articles (nostr/refs.py) and the tags a
published note or article gets for what it quotes.

What must hold:

  A pasted link to a note or an article is read whatever form it takes:
  a nostr: link, a bare entity, or a reader's web address (njump, Primal,
  YakiHonne). A person's link, a damaged one, or anything else says in
  plain words why it is not one.

  References are found outside code in an article, anywhere in a note,
  each once. Each quoted note gets a q tag with its id, a relay hint and
  its author (NIP-18); each quoted article a q tag with its address; each
  quoted author a p tag, once, also when mentioned.

  Secret keys written in a text are found, plain and encrypted.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nostr import bech32, refs  # noqa: E402
from nostr.publisher import build_article, build_note  # noqa: E402

ALICE = "a1" * 32
BOB = "b0" * 32
EVENT = "e5" * 32
RELAY = "wss://relay.example.com"
NEVENT = bech32.encode_nevent(EVENT, [RELAY], ALICE, 1)
NOTE = bech32.encode_note("c4" * 32)
NADDR = bech32.encode_naddr("my-article", BOB, 30023, [RELAY])
NPUB = bech32.encode_npub(ALICE)


@pytest.mark.parametrize("pasted", [
    f"nostr:{NEVENT}", NEVENT, f"  {NEVENT}  ", f"https://njump.me/{NEVENT}",
    f"https://primal.net/e/{NEVENT}", f"https://example.com/thread?id={NEVENT}",
])
def test_a_note_is_read_in_every_form(pasted):
    reference, reason = refs.normalize_reference(pasted)
    assert reason == "" and reference.kind == refs.NOTE
    assert (reference.event_id, reference.pubkey, reference.relay) == (EVENT, ALICE, RELAY)
    assert reference.uri == f"nostr:{NEVENT}"


def test_an_article_is_read_with_its_address():
    reference, reason = refs.normalize_reference(f"https://yakihonne.com/article/{NADDR}")
    assert reason == "" and reference.kind == refs.ARTICLE
    assert reference.coordinate == f"30023:{BOB}:my-article"


@pytest.mark.parametrize("pasted, words", [
    ("", "Paste a link"),
    ("https://example.com/page", "not a link to a note or an article"),
    (f"nostr:{NPUB}", "a link to a person"),
    (NEVENT[:-3] + "qqq", "damaged"),
])
def test_what_is_no_note_or_article_says_why(pasted, words):
    reference, reason = refs.normalize_reference(pasted)
    assert reference is None and words in reason


def test_references_are_found_outside_code_each_once():
    markdown = (f"See nostr:{NEVENT} and nostr:{NADDR}.\n\n"
                f"`nostr:{NOTE}` is code.\n\n```\nnostr:{NOTE}\n```\n\nAgain nostr:{NEVENT}\n")
    found = refs.find_references(markdown)
    assert [r.entity for r in found] == [NEVENT, NADDR]
    in_a_note = refs.find_references(markdown, markdown=False)
    assert [r.entity for r in in_a_note] == [NEVENT, NADDR, NOTE]


def test_quoted_notes_and_articles_get_q_tags_and_their_authors_p_tags():
    found = refs.find_references(f"nostr:{NEVENT} nostr:{NOTE} nostr:{NADDR} nostr:{NEVENT}")
    assert refs.quote_tags(found) == [
        ["q", EVENT, RELAY, ALICE],
        ["q", "c4" * 32, ""],
        ["q", f"30023:{BOB}:my-article", RELAY],
        ["p", ALICE, RELAY],
        ["p", BOB, RELAY],
    ]
    # An author already tagged (mentioned) is not tagged twice.
    assert ["p", ALICE, RELAY] not in refs.quote_tags(found, known_p=[ALICE])


def test_a_published_article_and_note_carry_the_tags():
    body = f"Quoting nostr:{NEVENT} by nostr:{NPUB}, and nostr:{NADDR}.\n"
    article = build_article(body, "f0" * 32, "slug")
    tags = article["tags"]
    assert ["q", EVENT, RELAY, ALICE] in tags
    assert ["q", f"30023:{BOB}:my-article", RELAY] in tags
    assert [t for t in tags if t[0] == "p"] == [["p", ALICE], ["p", BOB, RELAY]]
    note = build_note(f"Look: nostr:{NEVENT}", "f0" * 32)
    assert ["q", EVENT, RELAY, ALICE] in note["tags"]


def test_a_reference_in_code_is_not_quoted():
    article = build_article(f"Write `nostr:{NEVENT}` to quote.\n", "f0" * 32, "slug")
    assert not [t for t in article["tags"] if t[0] == "q"]


def test_secret_keys_are_found_plain_and_encrypted():
    nsec = bech32.encode_nsec("11" * 32)
    text = f"oops {nsec} and NCRYPTSEC1{'q' * 40} and {nsec} again"
    assert refs.secret_keys_in(text) == [nsec, "NCRYPTSEC1" + "q" * 40]
    assert refs.secret_keys_in(f"nostr:{NPUB}") == []
