# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deletion requests (NIP-09, nostr/deletion.py): building them, reading
them, and what they cover.

What must hold: a request names events by id and by address and says
their kinds; only a validly signed request by the author counts; an id
covers that one event, an address every version up to the request's
time and no later one; nobody's request covers somebody else's events.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr import events  # noqa: E402
from nostr.deletion import (  # noqa: E402
    DELETION_KIND, address, address_of, build_deletion, read_deletion,
)
from tests.outbox_fakes import NOW, OTHER_PK, OTHER_SK, PK, SK, signed  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication(sys.argv)


def article(d="my-post", *, created_at=NOW - 100, sk=SK, title="Hello"):
    return signed(30023, [["d", d], ["title", title]], "body", sk=sk, created_at=created_at)


def request(*, addresses=(), event_ids=(), kinds=(), created_at=NOW, sk=SK):
    pubkey = PK if sk is SK else OTHER_PK
    return events.sign_event(build_deletion(pubkey, addresses=addresses, event_ids=event_ids,
                                            kinds=kinds, created_at=created_at), sk)


def test_a_request_names_addresses_then_ids_then_kinds():
    unsigned = build_deletion(PK, addresses=[address(30023, PK, "a")],
                              event_ids=["e" * 64, "e" * 64], kinds=[30023, 30023],
                              created_at=NOW)
    assert unsigned["kind"] == DELETION_KIND and unsigned["content"] == ""
    assert unsigned["tags"] == [["a", f"30023:{PK}:a"], ["e", "e" * 64], ["k", "30023"]]
    assert unsigned["created_at"] == NOW and "sig" not in unsigned


def test_a_request_that_names_nothing_is_never_built():
    with pytest.raises(ValueError):
        build_deletion(PK, kinds=[1])


def test_only_a_validly_signed_request_by_the_author_counts():
    real = request(event_ids=["e" * 64])
    assert read_deletion(real, PK) is not None
    assert read_deletion(real, OTHER_PK) is None                 # someone else's request
    forged = dict(real, tags=[["e", "f" * 64]])                  # tags changed after signing
    assert read_deletion(forged, PK) is None
    assert read_deletion(signed(1, [["e", "e" * 64]]), PK) is None   # not a request
    loose_time = dict(real, created_at=str(real["created_at"]))  # signature still checks out
    assert read_deletion(loose_time, PK) is None


def test_reading_writes_ids_and_addresses_one_way():
    upper = address(30023, PK, "Mixed-Case").replace(PK, PK.upper())
    req = read_deletion(request(addresses=[upper, "not an address", "x:y:z"],
                                event_ids=["AB" * 32]), PK)
    assert req.event_ids == {"ab" * 32}
    assert req.addresses == {f"30023:{PK}:Mixed-Case"}
    assert req.identifiers(30023) == ["Mixed-Case"]
    assert req.identifiers(31234) == []


def test_an_id_covers_that_event_whenever_it_was_made():
    note = signed(1, [], "a note", created_at=NOW + 500)    # dated after the request
    req = read_deletion(request(event_ids=[note["id"]]), PK)
    assert req.covers(note)
    assert not req.covers(signed(1, [], "another note"))


def test_an_address_covers_every_version_up_to_the_request_and_no_later_one():
    older, current = article(created_at=NOW - 200), article(created_at=NOW, title="Edited")
    later = article(created_at=NOW + 60, title="Published again")
    req = read_deletion(request(addresses=[address(30023, PK, "my-post")]), PK)
    assert req.covers(older) and req.covers(current)
    assert not req.covers(later)
    assert not req.covers(article("another-post"))


def test_a_request_never_covers_somebody_elses_events():
    theirs = article(sk=OTHER_SK)
    req = read_deletion(request(addresses=[address(30023, OTHER_PK, "my-post")],
                                event_ids=[theirs["id"]]), PK)
    assert not req.covers(theirs)


def test_addresses_of_events():
    assert address_of(article("post")) == f"30023:{PK}:post"
    assert address_of(signed(10002, [["r", "wss://a.example"]])) == f"10002:{PK}:"
    assert address_of(signed(1, [], "a note")) is None
