# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which posts are already imported, asked of the relays.

A post with a draft, an article or a deletion for its identifier is
imported, whichever app made it; so is one in this computer's ledger.
When no relay answers, the answer is "unavailable", never "nothing".
"""

from __future__ import annotations

from nostr import events
from nostr.deletion import build_deletion
from nostr.draft_deletions import build_deletion_request
from nostr.draft_store import DraftStore
from nostr.drafts import DraftWrapMeta
from nostr.imports.catalogue import UNAVAILABLE, ExistingCatalogue
from tests.outbox_fakes import (
    OTHER_SK, PK, SK, FakeRelayDirectory, HandPool, Profile, settle,
)

D1, D2, D3, D4 = "rss-1", "rss-2", "rss-3", "rss-4"


def signed(kind, d_tag, content="x", sk=SK, created_at=100):
    return events.sign_event({"kind": kind, "content": content, "tags": [["d", d_tag]],
                              "created_at": created_at}, sk)


class FakeQuery:
    """Answers each query with the events whose kind a filter asks for.
    ``answered``: how many of the relays asked answered (the first ones),
    or a function of (relays, filters) giving the set that did."""

    def __init__(self, stored, *, answered=1):
        self.stored = stored
        self.answered = answered
        self.calls = []

    def __call__(self, relays, filters, on_done):
        self.calls.append((list(relays), filters))
        kinds = {k for f in filters for k in f["kinds"]}
        if callable(self.answered):
            answered = set(self.answered(list(relays), filters))
        else:
            answered = set(relays[:self.answered])
        on_done([e for e in self.stored if e["kind"] in kinds], answered)


def catalogue(query, store=None, ledger=None, directory=None):
    return ExistingCatalogue(relay_pool=None, relay_directory=directory or FakeRelayDirectory(),
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


def private_calls(query):
    return [c for c in query.calls if [5] in [f["kinds"] for f in c[1]]]


def outbox_calls(query):
    return [c for c in query.calls if [f["kinds"] for f in c[1]] == [[30023]]]


def test_what_is_asked_and_where():
    query = FakeQuery([])
    look_up(catalogue(query), [f"rss-{i}" for i in range(25)])
    (private,) = private_calls(query)
    (outbox,) = outbox_calls(query)
    assert [f["kinds"] for f in private[1]] == [[31234, 30024], [5]]
    assert private[1][0]["#d"] == [f"rss-{i}" for i in range(25)]
    assert f"31234:{PK}:rss-0" in private[1][1]["#a"]
    assert f"30023:{PK}:rss-0" in private[1][1]["#a"]
    assert outbox[1] == [{"kinds": [30023], "authors": [PK],
                          "#d": [f"rss-{i}" for i in range(25)]}]


def test_many_posts_are_asked_in_requests_relays_accept():
    """Review H3: 120 posts in one request made twelve filters, a relay
    allowing ten refused it, and the answer was taken for "nothing"."""
    query = FakeQuery([])
    out = look_up(catalogue(query), [f"rss-{i}" for i in range(250)])
    assert out["found"] == {}
    private, outbox = private_calls(query), outbox_calls(query)
    assert [len(c[1][0]["#d"]) for c in private] == [100, 100, 50]
    assert [len(c[1][0]["#d"]) for c in outbox] == [100, 100, 50]
    assert all(len(c[1]) <= 2 for c in query.calls)


def test_one_unanswered_request_makes_it_unavailable():
    asked = []

    def answered(relays, filters):
        asked.append(1)
        return [] if len(asked) == 2 else relays
    out = look_up(catalogue(FakeQuery([], answered=answered)),
                  [f"rss-{i}" for i in range(250)])
    assert out == {"unavailable": UNAVAILABLE}


def test_only_the_relays_drafts_go_to_count_for_drafts():
    """The fallback relays answering with nothing prove nothing about
    drafts kept on the account's own relays: unavailable, not "none"."""
    own = "wss://own.example"
    directory = FakeRelayDirectory({PK: [own]})

    def answered(relays, filters):
        kinds = [f["kinds"] for f in filters]
        if [5] in kinds:            # the drafts' request: the own relay refused it
            return [u for u in relays if "own.example" not in u]
        return relays
    out = look_up(catalogue(FakeQuery([], answered=answered), directory=directory), [D1])
    assert out == {"unavailable": UNAVAILABLE}
    # The own relay answering is enough.
    out = look_up(catalogue(FakeQuery([signed(31234, D1)],
                                      answered=lambda relays, filters: [own]),
                            directory=directory), [D1])
    assert out["found"] == {D1: "drafted"}


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


def test_the_relay_query_says_which_relays_answered():
    """Review H3: a relay that refuses the request (more filters than it
    allows) or cannot be reached is not an answer; only a relay that sent
    all it keeps is. Read through queries.fetch_events, so a forged event
    does not count either."""
    pool = HandPool()
    cat = ExistingCatalogue(relay_pool=pool, relay_directory=FakeRelayDirectory())
    out = []
    cat._relay_query(["wss://a.example", "wss://b.example", "wss://c.example"], [{}],
                     lambda events_, answered: out.append((events_, answered)))
    sub = pool.subs[0]
    article = signed(30023, D1)
    sub.fail("wss://a.example", "down")
    sub.refuse("wss://c.example", "error: too many filters")
    assert out == []
    sub.answer("wss://b.example", article, {"id": "forged", "kind": 30023})
    assert out == [([article], {"wss://b.example"})]


def test_a_deletion_newer_than_the_draft_reports_removed():
    """Review L2: rnostr and nostr-rs-relay keep a draft an address request
    deleted, and the catalogue said "drafted" for a draft that is gone.
    An article the request covers, found in the outbox, is removed too."""
    draft_deleted = events.sign_event(build_deletion_request(
        pubkey_hex=PK, identifier=D1, created_at=200), SK)
    article_deleted = events.sign_event(build_deletion(
        PK, addresses=[f"30023:{PK}:{D2}"], kinds=[30023], created_at=200), SK)
    query = FakeQuery([signed(31234, D1), draft_deleted, signed(30023, D2), article_deleted])
    out = look_up(catalogue(query), [D1, D2])
    assert out["found"] == {D1: "removed", D2: "removed"}


def test_a_version_newer_than_the_deletion_counts_as_what_it_is():
    deletion = events.sign_event(build_deletion_request(
        pubkey_hex=PK, identifier=D1, created_at=200), SK)
    # The same second is covered (NIP-09: up to the request's time).
    same_second = look_up(catalogue(FakeQuery([signed(31234, D1, created_at=200),
                                               deletion])), [D1])
    assert same_second["found"] == {D1: "removed"}
    later = look_up(catalogue(FakeQuery([signed(31234, D1, created_at=100), deletion,
                                         signed(31234, D1, created_at=300)])), [D1])
    assert later["found"] == {D1: "drafted"}
