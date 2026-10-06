# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for ``nostr.rss.dtag``."""

from __future__ import annotations

import unittest
from hashlib import sha256

from nostr.rss.dtag import NoIdentifierError, derive_identifier


class DeriveIdentifierTests(unittest.TestCase):
    def test_guid_is_preferred_seed(self) -> None:
        ident = derive_identifier(
            guid="urn:item:42",
            link="https://example.com/post",
            title="Hello",
        )
        expected = sha256(b"urn:item:42").hexdigest()[:16]
        self.assertEqual(ident, expected)

    def test_falls_back_to_link_when_guid_missing(self) -> None:
        ident = derive_identifier(link="https://example.com/post", title="Hello")
        expected = sha256(b"https://example.com/post").hexdigest()[:16]
        self.assertEqual(ident, expected)

    def test_falls_back_to_title_when_guid_and_link_missing(self) -> None:
        ident = derive_identifier(title="Hello world")
        expected = sha256(b"Hello world").hexdigest()[:16]
        self.assertEqual(ident, expected)

    def test_empty_strings_are_treated_as_missing(self) -> None:
        ident = derive_identifier(guid="", link="", title="only this")
        expected = sha256(b"only this").hexdigest()[:16]
        self.assertEqual(ident, expected)

    def test_prefix_is_prepended_verbatim(self) -> None:
        ident = derive_identifier(guid="seed", prefix="rss-")
        expected = "rss-" + sha256(b"seed").hexdigest()[:16]
        self.assertEqual(ident, expected)

    def test_identifier_is_stable_across_runs(self) -> None:
        a = derive_identifier(guid="same input")
        b = derive_identifier(guid="same input")
        self.assertEqual(a, b)

    def test_different_seeds_produce_different_ids(self) -> None:
        a = derive_identifier(guid="seed-a")
        b = derive_identifier(guid="seed-b")
        self.assertNotEqual(a, b)

    def test_unicode_seed_is_utf8_hashed(self) -> None:
        ident = derive_identifier(title="café à Paris")
        expected = sha256("café à Paris".encode("utf-8")).hexdigest()[:16]
        self.assertEqual(ident, expected)

    def test_no_inputs_raises(self) -> None:
        with self.assertRaises(NoIdentifierError):
            derive_identifier()

    def test_only_empty_strings_raises(self) -> None:
        with self.assertRaises(NoIdentifierError):
            derive_identifier(guid="", link="", title="")


class StandupCompatibilityTests(unittest.TestCase):
    """Golden values from EINUNDZWANZIG STANDUP, so a post imported in
    either app gets one draft, not two.

    Each expected value was computed by running both of STANDUP's
    implementations on the same input: the server's ``_item``
    (backend/core/feed_parser.py, for sources it checks itself) and the
    browser's ``deriveIdentifier`` (src/services/rss-parser.service.js,
    for the rest). Where they agree, this app must agree with both.
    """

    def test_guid(self) -> None:
        self.assertEqual(
            derive_identifier(guid="urn:uuid:1225c695-cfb8-4ebb-aaaa-80da344efa6a",
                              link="https://blog.example.com/a", title="A", prefix="rss-"),
            "rss-3841e5cf232f5111")

    def test_guid_with_surrounding_white_space_is_trimmed(self) -> None:
        self.assertEqual(
            derive_identifier(guid="  tag:blog.example.com,2026:post-7 \n",
                              link="https://blog.example.com/b", title="B", prefix="rss-"),
            "rss-0d17601072554876")

    def test_absolute_link_without_guid(self) -> None:
        self.assertEqual(
            derive_identifier(link="https://blog.example.com/2026/10/hello-world/",
                              title="Hello", prefix="rss-"),
            "rss-8b036d9ebc4807d4")

    def test_title_only(self) -> None:
        self.assertEqual(derive_identifier(title="Only a title", prefix="rss-"),
                         "rss-66a34d0313f374f0")

    def test_atom_id(self) -> None:
        self.assertEqual(
            derive_identifier(guid="tag:example.org,2003:3.2397",
                              link="https://example.org/2003/12/13/atom03", title="Atom",
                              prefix="rss-"),
            "rss-bc451146b7edc3a8")

    def test_non_ascii_guid(self) -> None:
        self.assertEqual(
            derive_identifier(guid="https://blog.example.com/café-à-paris", title="Café",
                              prefix="rss-"),
            "rss-13e542b9c24f14e0")

    def test_numeric_json_feed_id_is_hashed_as_its_digits(self) -> None:
        # STANDUP's server: "123". Its browser code cannot read a number
        # (a STANDUP bug), so only the server value can be matched.
        self.assertEqual(derive_identifier(guid=123, link="https://blog.example.com/c",
                                           prefix="rss-"),
                         "rss-a665a45920422f9d")

    def test_white_space_guid_falls_through_to_the_link(self) -> None:
        self.assertEqual(
            derive_identifier(guid="   ", link="https://blog.example.com/2026/10/hello-world/",
                              prefix="rss-"),
            "rss-8b036d9ebc4807d4")

    def test_relative_link_matches_the_browser_not_the_server(self) -> None:
        # Documented difference: STANDUP's server makes the link absolute
        # first (rss-4499d17d56334ddc); its browser code, like this one,
        # hashes it as written.
        self.assertEqual(derive_identifier(link="/2026/10/relative/", title="Rel",
                                           prefix="rss-"),
                         "rss-48c75bebfd725c4d")


class ParsedFeedIdentifierTests(unittest.TestCase):
    """The identifier of a parsed item, end to end through the parser."""

    def test_json_feed_numeric_id(self) -> None:
        from nostr.rss.normalize import item_to_article
        from nostr.rss.parser import parse_feed

        feed = parse_feed(
            '{"version": "https://jsonfeed.org/version/1.1", "title": "T", '
            '"items": [{"id": 123, "url": "https://blog.example.com/c", '
            '"content_html": "<p>Body</p>"}]}')
        template = item_to_article(feed.items[0], identifier_prefix="rss-")
        self.assertEqual(template.slug, "rss-a665a45920422f9d")

    def test_rss_guid_with_white_space(self) -> None:
        from nostr.rss.normalize import item_to_article
        from nostr.rss.parser import parse_feed

        feed = parse_feed(
            '<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>'
            '<item><title>B</title><link>https://blog.example.com/b</link>'
            '<guid isPermaLink="false">\n  tag:blog.example.com,2026:post-7\n</guid>'
            '<description>Body</description></item></channel></rss>')
        template = item_to_article(feed.items[0], identifier_prefix="rss-")
        self.assertEqual(template.slug, "rss-0d17601072554876")


if __name__ == "__main__":
    unittest.main()
