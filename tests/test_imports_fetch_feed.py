# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A feed check asks conditionally, so an unchanged feed costs nothing."""

from __future__ import annotations

from nostr.imports.fetch import SourceFetcher
from tests.blossom_fakes import FakeNam, FakeReply
from tests.test_imports_netguard import guard


def fetch(fetcher, **kw):
    out = {}
    fetcher.fetch_feed("https://blog.example/feed",
                       on_body=lambda text, meta: out.update(text=text, meta=dict(meta)),
                       on_not_modified=lambda: out.update(unchanged=True),
                       on_failure=lambda error: out.update(error=error), **kw)
    return out


def test_the_validators_of_the_last_answer_are_sent():
    reply = FakeReply(status=304)
    nam = FakeNam([reply])
    out = fetch(SourceFetcher(guard=guard(), nam=nam), etag='"v1"',
                last_modified="Mon, 01 Jan 2024 00:00:00 GMT")
    request = nam.calls[0][1]
    assert bytes(request.rawHeader("If-None-Match")) == b'"v1"'
    assert bytes(request.rawHeader("If-Modified-Since")) == b"Mon, 01 Jan 2024 00:00:00 GMT"
    reply.finish()
    assert out == {"unchanged": True}


def test_a_new_answer_brings_its_validators():
    reply = FakeReply(body=b"<rss></rss>", raw_headers={"ETag": b'"v2"',
                                                        "Last-Modified": b"Tue"})
    nam = FakeNam([reply])
    out = fetch(SourceFetcher(guard=guard(), nam=nam))
    assert not nam.calls[0][1].hasRawHeader("If-None-Match")
    reply.finish()
    assert out["text"] == "<rss></rss>"
    assert out["meta"] == {"etag": '"v2"', "last_modified": "Tue",
                           "final_url": "https://blog.example/feed"}
