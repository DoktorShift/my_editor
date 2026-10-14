# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A signed draft is kept, and sent again as it is.

An import checkpoints each draft between signing and sending. A retry
(after a crash, or relays that did not answer) sends that very event
again instead of signing a new one: no second signer prompt, and since
the event keeps its time, a draft the person edited meanwhile wins on
the relays (NIP-01), so a retry can never overwrite an edit.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nostr import events
from nostr.drafts import build_inner_event
from nostr.publisher import DraftPublishJob
from tests.outbox_fakes import OTHER_SK, PK, SK, FakeRelayDirectory, Profile, settle


def signed_wrap(identifier="rss-1", sk=SK):
    return events.sign_event({"kind": 31234, "content": "ct", "created_at": 42,
                              "tags": [["d", identifier], ["k", "30023"]]}, sk)


def resend_job(identifier="rss-1"):
    pool = MagicMock()
    pool.publish.return_value = MagicMock()
    sessions = MagicMock()
    job = DraftPublishJob(relay_pool=pool, relay_directory=FakeRelayDirectory(),
                          session_pool=sessions, profile=Profile(),
                          inner_event=None, identifier=identifier)
    return job, pool, sessions


def test_the_signed_wrap_is_handed_out_before_it_is_sent():
    inner = build_inner_event(kind=1, content="hi", pubkey_hex=PK)
    pool = MagicMock()
    order = []
    pool.publish.side_effect = lambda relays, event: order.append("sent") or MagicMock()
    job = DraftPublishJob(relay_pool=pool, relay_directory=MagicMock(),
                          session_pool=MagicMock(), profile=Profile(),
                          inner_event=inner, identifier="x")
    job.signed.connect(lambda event: order.append(("signed", event["id"])))
    job._on_signed({"id": "e1", "created_at": 1, "kind": 31234, "pubkey": PK,
                    "tags": [["d", "x"]], "content": "ct", "sig": "0" * 128},
                   publish_relays=["wss://r/"])
    assert order == [("signed", "e1"), "sent"]


def test_send_signed_sends_the_same_event_without_the_signer():
    job, pool, sessions = resend_job()
    stashed, kept = [], []
    job.stashed.connect(lambda d, eid, ts: stashed.append((d, eid, ts)))
    job.signed.connect(kept.append)
    wrap = signed_wrap()
    job.send_signed(wrap)
    settle()
    assert pool.publish.call_args[0][1] == wrap
    assert stashed == [("rss-1", wrap["id"], 42)]
    assert kept == []                       # nothing new to keep
    sessions.get.assert_not_called()


@pytest.mark.parametrize("bad", [
    signed_wrap(sk=OTHER_SK),               # another account's
    signed_wrap("rss-2"),                   # another draft
    {**signed_wrap(), "content": "changed"},  # not what was signed
])
def test_send_signed_refuses_what_is_not_this_draft(bad):
    job, pool, _sessions = resend_job()
    with pytest.raises(ValueError):
        job.send_signed(bad)
    pool.publish.assert_not_called()


def test_a_job_without_an_inner_event_cannot_stash():
    job, _pool, _sessions = resend_job()
    with pytest.raises(ValueError):
        job.start()
