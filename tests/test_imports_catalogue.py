# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which posts are already imported, asked of the relays.

A post with a draft, an article or a deletion for its identifier is
imported, whichever app made it; so is one in this computer's ledger.
When no relay answers, the answer is "unavailable", never "nothing".
"""

from __future__ import annotations

from nostr import events
from nostr.draft_deletions import build_deletion_request
from nostr.draft_store import DraftStore
from nostr.drafts import DraftWrapMeta
from nostr.imports.catalogue import UNAVAILABLE, ExistingCatalogue
from tests.outbox_fakes import (
    OTHER_SK, PK, SK, FakeRelayDirectory, HandPool, Profile, settle,
)

D1, D2, D3, D4 = "rss-1", "rss-2", "rss-3", "rss-4"


def signed(kind, d_tag, content="x", sk=SK):
    return events.sign_event({"kind": kind, "content": content, "tags": [["d", d_tag]],
                              "created_at": 100}, sk)


class FakeQuery:
    """Answers each query with the events whose kind a filter asks for."""

    def __init__(self, stored, *, answered=1):
        self.stored = stored
        self.answered = answered
        self.calls = []

    def __call__(self, relays, filters, on_done):
        self.calls.append((list(relays), filters))
        kinds = {k for f in filters for k in f["kinds"]}
        on_done([e for e in self.stored if e["kind"] in kinds], self.answered)


def catalogue(query, store=None, ledger=None):
    return ExistingCatalogue(relay_pool=None, relay_directory=FakeRelayDirectory(),
                             draft_store=store, ledger=ledger, query=query)


def look_up(cat, tags):
    out = {}
    cat.look_up(Profile(), tags, on_ready=lambda found: out.update(found=found.states),
                on_unavailable=lambda reason: out.update(unavailable=reason))
    settle()
    return out


def test_drafts_articles_and_deletions_count():
    deletion = events.sign_event(build_deletion_request(
        pubkey_hex=PK, identifier=D3, created_at=100), SK)
    query = FakeQuery([signed(31234, D1), signed(30023, D2), deletion,
                       signed(31234, D4, content=""),
                       signed(30024, "rss-5")])
    out = look_up(catalogue(query), [D1, D2, D3, D4, "rss-5", "rss-6"])
    assert out["found"] == {D1: "drafted", D2: "published", D3: "removed",
                            D4: "removed", "rss-5": "drafted"}


def test_what_is_asked_and_where():
    query = FakeQuery([])
    look_up(catalogue(query), [f"rss-{i}" for i in range(25)])
    private, outbox = query.calls
    kinds = [f["kinds"] for f in private[1]]
    assert kinds == [[31234, 30024], [5], [31234, 30024], [5]]
    assert len(private[1][0]["#d"]) == 20 and len(private[1][2]["#d"]) == 5
    assert f"31234:{PK}:rss-0" in private[1][1]["#a"]
    assert f"30023:{PK}:rss-0" in private[1][1]["#a"]
    assert outbox[1] == [{"kinds": [30023], "authors": [PK], "#d": [f"rss-{i}" for i in
                                                                    range(20)]},
                         {"kinds": [30023], "authors": [PK],
                          "#d": [f"rss-{i}" for i in range(20, 25)]}]


def test_no_relay_answering_is_unavailable_never_nothing():
    out = look_up(catalogue(FakeQuery([], answered=0)), [D1])
    assert out == {"unavailable": UNAVAILABLE}


def test_some_relays_answering_is_enough():
    out = look_up(catalogue(FakeQuery([signed(31234, D1)], answered=1)), [D1, D2])
    assert out["found"] == {D1: "drafted"}


def test_events_not_signed_by_the_account_are_ignored():
    forged = dict(signed(30023, D1))
    forged["sig"] = "0" * 128
    stranger = signed(31234, D2, sk=OTHER_SK)
    out = look_up(catalogue(FakeQuery([forged, stranger])), [D1, D2])
    assert out["found"] == {}


def test_the_draft_store_and_the_ledger_count():
    store = DraftStore()
    store.bind_profile(PK)
    store.upsert_skeleton(DraftWrapMeta(D1, 30023, "e1", PK, 100, None, "ct"))
    store.upsert_skeleton(DraftWrapMeta(D2, 30023, "e2", PK, 100, None, ""))  # deleted
    cat = catalogue(FakeQuery([]), store=store,
                    ledger=lambda tags: {t: "published" for t in tags if t == D3})
    out = look_up(cat, [D1, D2, D3, D4])
    assert out["found"] == {D1: "drafted", D2: "removed", D3: "published"}
    assert cat.known_locally(D1) == "drafted"
    assert cat.known_locally(D4) is None


def test_the_relay_query_counts_answering_relays():
    pool = HandPool()
    cat = ExistingCatalogue(relay_pool=pool, relay_directory=FakeRelayDirectory())
    out = []
    cat._relay_query(["wss://a.example", "wss://b.example"], [{}],
                     lambda events_, answered: out.append((events_, answered)))
    sub = pool.subs[0]
    article = signed(30023, D1)
    sub.fail("wss://a.example", "down")
    assert out == []
    sub.answer("wss://b.example", article, {"id": "forged", "kind": 30023})
    assert out == [([article], 1)]
