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
    DELETION_KIND, DeletionJob, address, address_of, build_deletion, read_deletion,
)
from tests.outbox_fakes import (  # noqa: E402
    NOW, OTHER_PK, OTHER_SK, PK, SK, FakeClient, FakeJob, FakePool, FakeRelayDirectory,
    FakeSessionPool, Profile, settle, signed,
)


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


# -- the job ------------------------------------------------------------------------------

WRITE = ["wss://w1.example", "wss://w2.example"]
FOUND = "wss://found.example"
BOB = "b" * 64
BOB_INBOX = "wss://bob-inbox.example"


def deletion_job(target, *, pool=None, client=None, found_on=(FOUND,), mentioned=(),
                 entitled=(), lists=None):
    directory = FakeRelayDirectory(lists if lists is not None else {PK: WRITE})
    job = DeletionJob(relay_pool=pool or FakePool(), relay_directory=directory,
                      session_pool=FakeSessionPool(client or FakeClient()), profile=Profile(),
                      event=target, found_on=found_on, mentioned=mentioned,
                      entitled_relays=entitled, clock=lambda: NOW)
    seen = {"signed": [], "results": [], "first": [], "completed": [], "failed": []}
    job.signed.connect(seen["signed"].append)
    job.relay_result.connect(lambda *result: seen["results"].append(result))
    job.first_accept.connect(seen["first"].append)
    job.completed.connect(seen["completed"].append)
    job.failed.connect(seen["failed"].append)
    return job, seen


def test_an_article_is_asked_back_by_id_and_by_address_with_its_kind():
    target = article("my-post")
    pool = FakePool()
    job, seen = deletion_job(target, pool=pool)
    job.start()
    settle()
    (relays, sent), = pool.published
    assert sent["kind"] == DELETION_KIND and sent["pubkey"] == PK and sent["content"] == ""
    assert sent["tags"] == [["a", f"30023:{PK}:my-post"], ["e", target["id"]], ["k", "30023"]]
    assert sent["created_at"] == NOW and events.verify_event(sent)
    assert seen["signed"] == [sent] and job.request == sent
    assert relays == [FOUND] + WRITE              # where it was found, then where it goes
    assert seen["first"] == [FOUND]
    assert [r[0] for r in seen["results"]] == relays
    assert seen["completed"] == [[(url, True, "") for url in relays]]


def test_a_note_is_asked_back_by_id_only():
    note = signed(1, [["client", "MyEditor"]], "a note")
    pool = FakePool()
    job, _seen = deletion_job(note, pool=pool)
    job.start()
    settle()
    sent = pool.published[0][1]
    assert sent["tags"] == [["e", note["id"]], ["k", "1"]]


def test_it_also_goes_where_the_publish_went():
    note = signed(1, [["p", BOB]], "hi Bob")
    lists = {PK: WRITE, BOB: [BOB_INBOX]}
    pool = FakePool()
    job, _seen = deletion_job(note, pool=pool, lists=lists, mentioned=[(BOB, "")],
                              entitled=lambda: ["wss://members.example"])
    job.start()
    settle()
    assert pool.published[0][0] == [FOUND] + WRITE + ["wss://members.example", BOB_INBOX]


def test_no_relay_taking_it_still_completes_and_try_again_sends_the_same_request():
    target = article()
    pool = FakePool(refuse=set([FOUND] + WRITE))
    client = FakeClient()
    job, seen = deletion_job(target, pool=pool, client=client)
    job.start()
    settle()
    assert seen["first"] == [] and len(seen["completed"]) == 1
    assert not any(ok for _url, ok, _message in seen["completed"][0])
    pool.refuse = set()
    assert job.send_again() is True
    settle()
    assert len(client.requests) == 1               # signed once
    assert pool.published[1] == pool.published[0]  # the same request, to the same relays
    assert seen["first"] == [FOUND]


def test_try_again_waits_until_the_relays_answered_the_last_sending():
    class SilentPool(FakePool):
        def publish(self, urls, event):
            self.published.append((list(urls), event))
            return FakeJob()                       # no relay has answered yet

    pool = SilentPool()
    job, seen = deletion_job(article(), pool=pool)
    job.start()
    settle()
    assert len(pool.published) == 1 and seen["completed"] == []
    assert job.send_again() is False and len(pool.published) == 1


def test_a_signer_that_does_not_sign_fails_the_job_and_nothing_is_sent():
    pool = FakePool()
    job, seen = deletion_job(article(), pool=pool, client=FakeClient(fail="user rejected"))
    job.start()
    settle()
    assert seen["failed"] == ["user rejected"] and pool.published == []
    assert job.send_again() is False


def test_a_signer_that_returns_something_else_is_not_trusted():
    def other_tags(event):
        return events.sign_event(dict(event, tags=[["e", "f" * 64]]), SK)
    pool = FakePool()
    job, seen = deletion_job(article(), pool=pool, client=FakeClient(tamper=other_tags))
    job.start()
    settle()
    assert len(seen["failed"]) == 1 and pool.published == []


def test_only_the_accounts_own_events_can_be_asked_back():
    with pytest.raises(ValueError):
        deletion_job(article(sk=OTHER_SK))
    with pytest.raises(ValueError):
        deletion_job({"kind": 1, "pubkey": PK, "id": "not an id"})


class ParkedClient(FakeClient):
    """Signs only when the test says so: the approval is still on the phone."""

    def __init__(self):
        super().__init__()
        self.parked = []

    def sign_event(self, unsigned, on_success, on_failure, **_kw):
        self.parked.append(lambda: FakeClient.sign_event(self, unsigned, on_success,
                                                          on_failure))


def test_a_cancelled_job_sends_nothing_and_says_nothing_more():
    pool = FakePool()
    client = ParkedClient()
    job, seen = deletion_job(article(), pool=pool, client=client)
    job.start()
    job.cancel()
    client.parked.pop()()                          # approved after all
    settle()
    assert pool.published == []
    assert seen == {"signed": [], "results": [], "first": [], "completed": [], "failed": []}
