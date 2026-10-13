# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The network guard every import fetch passes.

A feed names addresses chosen by strangers. None of them may make this
app knock on the person's own network: not as written, not through a
name that resolves there, not through a redirect. The resolver is
faked, so nothing here asks the network anything.
"""

from __future__ import annotations

import pytest

from nostr.imports.errors import ERROR_CODES, friendly_message
from nostr.imports.fetch import SourceFetcher
from nostr.imports.netguard import (
    REFUSED_MESSAGE,
    UNRESOLVED_REASON,
    NetGuard,
    is_allowed_url,
    is_public_address,
)
from tests.blossom_fakes import FakeNam, FakeReply

PUBLIC = "93.184.216.34"


def guard(answers=None, asked=None):
    table = dict(answers or {})

    def resolve(host, on_done):
        if asked is not None:
            asked.append(host)
        on_done(table.get(host, [PUBLIC]))

    return NetGuard(resolver=resolve)


def verdict(net_guard, url):
    out = []
    net_guard.check(url, on_allowed=lambda: out.append("allowed"),
                    on_refused=lambda reason: out.append(reason))
    return out[0]


@pytest.mark.parametrize("address", [
    "10.1.2.3", "127.0.0.1", "169.254.169.254", "192.168.1.1", "172.16.0.1",
    "100.64.0.1", "0.0.0.0", "224.0.0.1", "::1", "fc00::1", "fd12::1", "fe80::1",
    "ff02::1", "::ffff:10.0.0.1",
])
def test_addresses_that_are_not_public(address):
    assert not is_public_address(address)


@pytest.mark.parametrize("address", [PUBLIC, "2606:2800:220:1:248:1893:25c8:1946",
                                     "::ffff:93.184.216.34"])
def test_public_addresses(address):
    assert is_public_address(address)


def test_a_public_name_is_allowed():
    assert verdict(guard(), "https://blog.example/feed") == "allowed"


@pytest.mark.parametrize("answer", [["10.0.0.5"], ["127.0.0.1"], ["::1"],
                                    [PUBLIC, "192.168.0.10"], ["fe80::1"]])
def test_a_name_resolving_into_the_local_network_is_refused(answer):
    assert verdict(guard({"rebind.example": answer}),
                   "https://rebind.example/feed") == REFUSED_MESSAGE


@pytest.mark.parametrize("url", [
    "http://localhost:8080/feed", "http://router/feed", "http://nas.local/feed",
    "http://192.168.1.1/feed", "http://[::1]/feed", "http://user:pw@blog.example/feed",
    "ftp://blog.example/feed", "https://blog.example/" + "x" * 2100,
])
def test_refused_without_asking_the_network(url):
    asked = []
    assert verdict(guard(asked=asked), url) == REFUSED_MESSAGE
    assert asked == []
    assert not is_allowed_url(url)


def test_a_public_address_literal_needs_no_lookup():
    asked = []
    assert verdict(guard(asked=asked), f"http://{PUBLIC}/feed") == "allowed"
    assert asked == []


def test_a_name_the_guard_cannot_resolve_is_not_fetched():
    """Review L10: it was let through, and Qt resolves again to connect,
    so a name answering 127.0.0.1 the second time was fetched."""
    net_guard = NetGuard(resolver=lambda host, on_done: on_done(None))
    assert verdict(net_guard, "https://nowhere.example/feed") == UNRESOLVED_REASON
    told = []
    net_guard.check("https://nowhere.example/feed", on_allowed=lambda: told.append("allowed"),
                    on_refused=told.append, on_unresolved=lambda: told.append("unresolved"))
    assert told == ["unresolved"]


class TestSourceFetcher:
    def fetch(self, fetcher, url):
        out = {}
        fetcher.fetch(url, on_success=lambda body: out.update(body=body),
                      on_failure=lambda error: out.update(error=error))
        return out

    def test_a_refused_address_is_never_requested(self):
        nam = FakeNam()
        fetcher = SourceFetcher(guard=guard({"rebind.example": ["10.0.0.1"]}), nam=nam)
        out = self.fetch(fetcher, "https://rebind.example/feed")
        assert nam.calls == []
        assert out["error"].code == ERROR_CODES.LOCAL_NETWORK
        assert friendly_message(out["error"]) == REFUSED_MESSAGE

    def test_a_redirect_is_checked_and_asked_anew(self):
        """Qt follows no redirect by itself: the guard checks the new
        address, resolves and pins it, and asks it (R-6)."""
        moved = FakeReply(status=301, raw_headers={"Location": b"https://www.blog.example/feed"})
        final = FakeReply(body=b"<rss></rss>")
        nam = FakeNam([moved, final])
        fetcher = SourceFetcher(guard=guard(), nam=nam)
        out = self.fetch(fetcher, "https://blog.example/feed")
        from PySide6.QtNetwork import QNetworkRequest
        request = nam.calls[0][1]
        assert request.attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute) == \
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy
        moved.finish()
        second = nam.calls[1][1]
        assert bytes(second.rawHeader("Host")) == b"www.blog.example"
        final.finish()
        assert out["body"] == "<rss></rss>"

    def test_a_redirect_into_the_local_network_fails_the_fetch(self):
        moved = FakeReply(status=302,
                          raw_headers={"Location": b"http://169.254.169.254/latest/meta-data/"})
        nam = FakeNam([moved])
        fetcher = SourceFetcher(guard=guard(), nam=nam)
        out = self.fetch(fetcher, "https://blog.example/feed")
        moved.finish()
        assert len(nam.calls) == 1          # never asked
        assert out["error"].code == ERROR_CODES.LOCAL_NETWORK
        assert "body" not in out
