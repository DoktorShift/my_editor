# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a post's own page adds (``nostr.imports.page_text``): the one rule
the import and the Imports window's article view share."""

from __future__ import annotations

import unittest
from dataclasses import replace

from nostr.imports.page_text import is_teaser, page_wanted, what_the_page_adds
from nostr.imports.sources.podcast import PodcastEpisode
from nostr.rss.normalize import item_to_article
from tests.imports_fakes import make_item

LINK = "https://blog.example/post"
PAGE = ("<html><head><title>T</title></head><body><article>" + "".join(
    f"<p>Paragraph {i} with plenty of ordinary prose words to satisfy readability "
    "scoring and beat the feed's text comfortably.</p>" for i in range(12))
    + "</article></body></html>")


class TeaserTests(unittest.TestCase):
    def test_a_body_of_links_is_a_teaser(self) -> None:
        # Hacker News (hnrss.org) and Stacker News feed bodies, as they are.
        hacker_news = ("Article URL: <https://eliastaylor.com/noexploit/>\n\n"
                       "Comments URL: <https://news.ycombinator.com/item?id=50188948>\n\n"
                       "Points: 10\n\n# Comments: 1")
        self.assertTrue(is_teaser(hacker_news))
        self.assertTrue(is_teaser("[Comments](https://stacker.news/items/1599572)"))

    def test_a_teaser_ending_is_a_teaser_whatever_its_length(self) -> None:
        self.assertTrue(is_teaser("Opening words of the post " * 12 + "[…]"))

    def test_a_post_is_no_teaser(self) -> None:
        self.assertFalse(is_teaser("A real paragraph of the post, long enough to be the "
                                   "post itself and not a pointer to it elsewhere."))


class WantedTests(unittest.TestCase):
    def wanted(self, item, full_text=True):
        return page_wanted(item, item_to_article(item), fetch_full_text=full_text)

    def test_a_teaser_is_read_for_its_text_and_a_post_without_cover_for_details(self) -> None:
        self.assertEqual(self.wanted(make_item("T", link=LINK, content_html="<p>x</p>")),
                         (True, True))
        whole = make_item("W", link=LINK, content_html="<p>" + "words " * 40 + "</p>",
                          image="https://blog.example/c.jpg")
        self.assertEqual(self.wanted(whole), (False, False))

    def test_the_option_off_reads_only_a_post_kept_in_part(self) -> None:
        self.assertEqual(self.wanted(make_item("T", link=LINK, content_html="<p>x</p>"),
                                     full_text=False), (False, False))
        self.assertEqual(self.wanted(make_item("P", link=LINK, content_html="<p>x</p>",
                                               partial=True), full_text=False), (True, False))

    def test_never_without_a_page_or_for_markdown_and_episodes(self) -> None:
        for item in (make_item("N", link=None, content_html="<p>x</p>"),
                     make_item("M", link=LINK, content_markdown="x"),
                     replace(make_item("E", link=LINK, content_html="<p>x</p>"),
                             podcast=PodcastEpisode(audio="https://cdn.example/e.mp3"))):
            self.assertEqual(self.wanted(item), (False, False), item.title)


class AddsTests(unittest.TestCase):
    def test_the_page_text_is_taken_when_it_clearly_beats_the_teaser(self) -> None:
        item = make_item("T", link=LINK, content_html="<p>[Comments](x)</p>")
        added_item, body = what_the_page_adds(item, "Comments", PAGE, wants_text=True)
        self.assertIn("Paragraph 11", body)

    def test_a_page_adding_nothing_is_none(self) -> None:
        item = make_item("T", link=LINK, content_html="<p>t</p>", image="https://c.example/c.png")
        self.assertIsNone(what_the_page_adds(item, "t" * 5000, PAGE, wants_text=True))

    def test_a_post_kept_in_part_needs_more_text_or_nothing(self) -> None:
        item = make_item("P", link=LINK, content_html="<p>t</p>", partial=True)
        self.assertIsNone(what_the_page_adds(item, "t", "<html><body></body></html>",
                                             wants_text=True))


if __name__ == "__main__":
    unittest.main()
