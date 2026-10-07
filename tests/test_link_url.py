# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins what a typed link address becomes (link_url.py).

What must hold:

  Web addresses, email addresses, Nostr links and places in the same
  document can be links; a web address typed without https:// gets it.

  Anything that runs code or opens files (javascript:, data:, file:,
  vbscript:), and any other scheme, is refused with a reason to show.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from link_url import (  # noqa: E402
    display_href, is_bare_http_url, normalize_link_input, web_address_for,
)

NPUB = "npub1" + "q" * 58


@pytest.mark.parametrize("typed, address", [
    ("https://example.com/a?b=1", "https://example.com/a?b=1"),
    ("http://example.com", "http://example.com"),
    ("example.com", "https://example.com"),
    ("www.example.com/page", "https://www.example.com/page"),
    ("sub.example.co.uk:8080/x", "https://sub.example.co.uk:8080/x"),
    ("  example.com  ", "https://example.com"),
    ("ada@example.com", "mailto:ada@example.com"),
    ("mailto:ada@example.com", "mailto:ada@example.com"),
    (NPUB, "nostr:" + NPUB),
    ("nostr:" + NPUB, "nostr:" + NPUB),
    ("#fn-1", "#fn-1"),
])
def test_what_can_be_a_link(typed, address):
    assert normalize_link_input(typed) == (address, "")


@pytest.mark.parametrize("typed", [
    "javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,<b>x</b>",
    "file:///etc/passwd", "vbscript:msgbox", "ftp://example.com", "nostr:nonsense",
    "https://", "", "   ", "two words", "#", "just-text",
])
def test_what_cannot(typed):
    address, reason = normalize_link_input(typed)
    assert address is None and reason


@pytest.mark.parametrize("typed, says", [
    ("https://good.example@evil.example/", "name or password"),
    ("https://user:secret@example.com/", "name or password"),
    ("example.com:99999", "port"),
    ("https://example.com:70000/x", "port"),
])
def test_addresses_the_app_would_not_open_are_refused_with_a_reason(typed, says):
    # Review M3: they became links, and Command-click then refused them.
    address, reason = normalize_link_input(typed)
    assert address is None and says in reason
    assert not is_bare_http_url(typed)


def test_only_one_web_address_is_a_bare_url():
    assert is_bare_http_url("https://example.com/x")
    assert not is_bare_http_url("see https://example.com")
    assert not is_bare_http_url("example.com")
    assert not is_bare_http_url("javascript:alert(1)")


def test_long_addresses_are_cut_in_the_middle():
    href = "https://example.com/" + "a" * 100
    shown = display_href(href, limit=30)
    assert len(shown) <= 30 and shown.startswith("https://") and "…" in shown
    assert display_href("mailto:ada@example.com") == "ada@example.com"


def test_where_a_link_opens_in_the_browser():
    assert web_address_for("https://example.com") == "https://example.com"
    assert web_address_for("nostr:" + NPUB) == "https://njump.me/" + NPUB
    assert web_address_for("mailto:ada@example.com") is None
    assert web_address_for("javascript:alert(1)") is None



def test_the_reasons_mention_nostr_only_while_it_is_in_use():
    assert "Nostr" not in normalize_link_input("ftp://example.com")[1]
    assert "Nostr" not in normalize_link_input("")[1]
    assert "Nostr" in normalize_link_input("ftp://example.com", nostr=True)[1]
    # A Nostr link is a link either way.
    assert normalize_link_input(NPUB)[0] == "nostr:" + NPUB
