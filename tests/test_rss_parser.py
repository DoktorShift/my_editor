# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for ``nostr.rss.parser``.

Samples are kept inline (small, readable, no fixture directory) and shaped
after real-world feeds from WordPress, Ghost, Hugo, and JSON Feed
publishers.
"""

from __future__ import annotations

import textwrap
import unittest

from nostr.rss.parser import RssError, parse_feed, with_whole_links


def _wp_rss() -> str:
    return textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"
             xmlns:content="http://purl.org/rss/1.0/modules/content/"
             xmlns:dc="http://purl.org/dc/elements/1.1/"
             xmlns:media="http://search.yahoo.com/mrss/">
        <channel>
            <title>Alice's Blog</title>
            <link>https://alice.example.com/</link>
            <description>Long-form posts</description>
            <item>
                <title>Bitcoin and Nostr</title>
                <link>https://alice.example.com/btc-nostr</link>
                <guid isPermaLink="false">post-1001</guid>
                <pubDate>Wed, 12 Mar 2025 10:00:00 GMT</pubDate>
                <dc:creator>Alice</dc:creator>
                <category>bitcoin</category>
                <category>nostr</category>
                <description>A teaser summary.</description>
                <content:encoded><![CDATA[<p>The full body of the post with <img src="https://alice.example.com/cover.jpg"/> an image.</p>]]></content:encoded>
                <enclosure url="https://alice.example.com/enc.jpg" type="image/jpeg" length="2000"/>
            </item>
            <item>
                <title>Just a summary</title>
                <link>https://alice.example.com/sum-only</link>
                <guid>https://alice.example.com/sum-only</guid>
                <pubDate>Mon, 10 Mar 2025 09:00:00 GMT</pubDate>
                <description>This post has no content:encoded.</description>
            </item>
        </channel>
        </rss>
    """)


def _atom_feed() -> str:
    return textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <title>Hugo Blog</title>
            <link href="https://hugo.example.com/"/>
            <subtitle>Notes from a static site</subtitle>
            <entry>
                <title>Atom post</title>
                <id>tag:hugo.example.com,2025:1</id>
                <link rel="alternate" href="https://hugo.example.com/p1"/>
                <published>2025-03-12T10:00:00Z</published>
                <updated>2025-03-13T10:00:00Z</updated>
                <author><name>Bob</name></author>
                <category term="meta"/>
                <summary type="html">A short summary.</summary>
                <content type="html">&lt;p&gt;Body with &lt;img src="https://hugo.example.com/i.png"/&gt;&lt;/p&gt;</content>
            </entry>
        </feed>
    """)


def _json_feed() -> str:
    return textwrap.dedent("""\
        {
          "version": "https://jsonfeed.org/version/1.1",
          "title": "JSON Blog",
          "home_page_url": "https://json.example.com/",
          "description": "Modern blog",
          "items": [
            {
              "id": "post-42",
              "url": "https://json.example.com/p/42",
              "title": "JSON Feed post",
              "summary": "Short summary",
              "content_html": "<p>Hello with <img src=\\"https://json.example.com/img.png\\"/> image.</p>",
              "date_published": "2025-03-12T10:00:00Z",
              "tags": ["a", "b"],
              "image": "https://json.example.com/cover.jpg",
              "author": {"name": "Carol"}
            }
          ]
        }
    """)


class RssParseTests(unittest.TestCase):
    def test_parses_wordpress_rss(self) -> None:
        feed = parse_feed(_wp_rss())
        self.assertEqual(feed.format, "rss")
        self.assertEqual(feed.title, "Alice's Blog")
        self.assertEqual(feed.link, "https://alice.example.com/")
        self.assertEqual(len(feed.items), 2)

    def test_rss_prefers_content_encoded_over_description(self) -> None:
        item = parse_feed(_wp_rss()).items[0]
        self.assertIn("full body", item.content_html.lower())
        self.assertEqual(item.summary, "A teaser summary.")

    def test_rss_falls_back_to_description_when_no_content(self) -> None:
        item = parse_feed(_wp_rss()).items[1]
        self.assertEqual(item.content_html, "This post has no content:encoded.")
        self.assertIsNone(item.summary)

    def test_rss_image_priority_enclosure(self) -> None:
        item = parse_feed(_wp_rss()).items[0]
        self.assertEqual(item.image, "https://alice.example.com/enc.jpg")

    def test_rss_image_falls_back_to_body_image_when_no_enclosure(self) -> None:
        rss = _wp_rss().replace(
            '<enclosure url="https://alice.example.com/enc.jpg" type="image/jpeg" length="2000"/>',
            "",
        )
        item = parse_feed(rss).items[0]
        self.assertEqual(item.image, "https://alice.example.com/cover.jpg")

    def test_rss_categories_preserved(self) -> None:
        item = parse_feed(_wp_rss()).items[0]
        self.assertEqual(item.categories, ("bitcoin", "nostr"))

    def test_rss_pubdate_to_unix_seconds(self) -> None:
        item = parse_feed(_wp_rss()).items[0]
        # Wed, 12 Mar 2025 10:00:00 GMT = 1741773600
        self.assertEqual(item.published_at, 1741773600)

    def test_rss_dc_creator_becomes_author(self) -> None:
        item = parse_feed(_wp_rss()).items[0]
        self.assertEqual(item.author, "Alice")

    def test_rss_guid_preserved(self) -> None:
        item = parse_feed(_wp_rss()).items[0]
        self.assertEqual(item.guid, "post-1001")


class AtomParseTests(unittest.TestCase):
    def test_format_and_envelope(self) -> None:
        feed = parse_feed(_atom_feed())
        self.assertEqual(feed.format, "atom")
        self.assertEqual(feed.title, "Hugo Blog")
        self.assertEqual(feed.description, "Notes from a static site")

    def test_content_prefers_content_over_summary(self) -> None:
        item = parse_feed(_atom_feed()).items[0]
        self.assertIn("Body with", item.content_html)
        self.assertEqual(item.summary, "A short summary.")

    def test_image_from_body_only(self) -> None:
        item = parse_feed(_atom_feed()).items[0]
        self.assertEqual(item.image, "https://hugo.example.com/i.png")

    def test_published_prefers_published_over_updated(self) -> None:
        item = parse_feed(_atom_feed()).items[0]
        self.assertEqual(item.published_at, 1741773600)  # 2025-03-12T10:00:00Z

    def test_link_resolves_to_alternate_href(self) -> None:
        item = parse_feed(_atom_feed()).items[0]
        self.assertEqual(item.link, "https://hugo.example.com/p1")

    def test_author_from_author_element(self) -> None:
        item = parse_feed(_atom_feed()).items[0]
        self.assertEqual(item.author, "Bob")


class JsonFeedParseTests(unittest.TestCase):
    def test_format_and_envelope(self) -> None:
        feed = parse_feed(_json_feed())
        self.assertEqual(feed.format, "jsonfeed")
        self.assertEqual(feed.title, "JSON Blog")
        self.assertEqual(feed.link, "https://json.example.com/")

    def test_content_html_preferred(self) -> None:
        item = parse_feed(_json_feed()).items[0]
        self.assertIn("Hello with", item.content_html)

    def test_image_field_wins_over_body_image(self) -> None:
        item = parse_feed(_json_feed()).items[0]
        self.assertEqual(item.image, "https://json.example.com/cover.jpg")

    def test_falls_back_to_body_image_when_image_field_missing(self) -> None:
        payload = _json_feed().replace(
            '"image": "https://json.example.com/cover.jpg",', ""
        )
        item = parse_feed(payload).items[0]
        self.assertEqual(item.image, "https://json.example.com/img.png")

    def test_date_published_parsed(self) -> None:
        item = parse_feed(_json_feed()).items[0]
        self.assertEqual(item.published_at, 1741773600)

    def test_author_from_author_object(self) -> None:
        item = parse_feed(_json_feed()).items[0]
        self.assertEqual(item.author, "Carol")

    def test_tags_become_categories(self) -> None:
        item = parse_feed(_json_feed()).items[0]
        self.assertEqual(item.categories, ("a", "b"))


class AutoDetectTests(unittest.TestCase):
    def test_empty_input_raises(self) -> None:
        with self.assertRaises(RssError):
            parse_feed("")

    def test_garbage_input_raises(self) -> None:
        with self.assertRaises(RssError):
            parse_feed("not a feed at all")

    def test_invalid_json_feed_raises(self) -> None:
        with self.assertRaises(RssError):
            parse_feed('{"not": "a feed"}')

    def test_leading_whitespace_does_not_confuse_detection(self) -> None:
        feed = parse_feed("\n\n  " + _json_feed())
        self.assertEqual(feed.format, "jsonfeed")


class WholeLinkTests(unittest.TestCase):
    """CP3 review L8: an item whose own link was relative kept its body's
    relative links and images, and its footer, pointing nowhere."""

    FEED = textwrap.dedent("""\
        <?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
        <link>https://blog.example/</link>
        <item><title>Post</title><link>/2026/10/post</link></item>
        <item><title>Whole</title><link>https://other.example/p</link></item>
        <item><title>Place</title><link>#top</link></item>
        </channel></rss>""")

    def test_a_relative_link_is_made_whole_against_where_the_feed_was_read(self) -> None:
        feed = with_whole_links(parse_feed(self.FEED), "https://feeds.example/blog.xml")
        post, whole, place = feed.items
        self.assertEqual(post.link, "https://feeds.example/2026/10/post")
        self.assertEqual(post.guid, "/2026/10/post")    # the identifier's seed, as written
        self.assertEqual(whole.link, "https://other.example/p")
        self.assertEqual(place.link, "#top")

    def test_without_an_address_the_site_the_feed_names(self) -> None:
        feed = with_whole_links(parse_feed(self.FEED), "")
        self.assertEqual(feed.items[0].link, "https://blog.example/2026/10/post")


class StreamTests(unittest.TestCase):
    """feedparser takes a string that reads as an address or a file name
    for something to open. A body must only ever be parsed as a body."""

    def setUp(self) -> None:
        import feedparser.http
        self._calls = []
        original = feedparser.http.get
        feedparser.http.get = lambda url, *a, **k: self._calls.append(url) or b""
        self.addCleanup(setattr, feedparser.http, "get", original)

    def test_a_body_that_is_an_address_is_never_fetched(self) -> None:
        for body in ("http://127.0.0.1:9/feed", "https://169.254.169.254/latest",
                     "file:///etc/hosts", "feed://example.com/rss"):
            with self.assertRaises(RssError):
                parse_feed(body)
        self.assertEqual(self._calls, [])

    def test_a_body_that_is_a_file_name_is_never_read(self) -> None:
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as handle:
            handle.write('<rss version="2.0"><channel><title>Secret</title>'
                         '<item><title>Private</title></item></channel></rss>')
        self.addCleanup(__import__("os").unlink, handle.name)
        with self.assertRaises(RssError):
            parse_feed(handle.name)


class EncodingTests(unittest.TestCase):
    """The text arrives decoded; the declaration names what the bytes were."""

    def _feed(self, encoding: str) -> str:
        return (f'<?xml version="1.0" encoding="{encoding}"?><rss version="2.0">'
                '<channel><title>Grüße</title><item><title>Über uns – Café</title>'
                '<guid>1</guid></item></channel></rss>')

    def test_a_declared_single_byte_encoding_does_not_garble_the_text(self) -> None:
        for encoding in ("windows-1252", "ISO-8859-1", "iso-8859-15", "UTF-8"):
            feed = parse_feed(self._feed(encoding))
            self.assertEqual(feed.title, "Grüße", encoding)
            self.assertEqual(feed.items[0].title, "Über uns – Café", encoding)

    def test_a_byte_order_mark_is_harmless(self) -> None:
        self.assertEqual(parse_feed("\ufeff" + self._feed("windows-1252")).title, "Grüße")


class DateTests(unittest.TestCase):
    def test_an_rss_item_dated_only_by_dc_date_keeps_its_date(self) -> None:
        feed = parse_feed(
            '<?xml version="1.0"?><rss version="2.0" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>T</title>'
            '<item><title>P</title><guid>1</guid><dc:date>2024-01-02T10:00:00Z</dc:date>'
            '</item></channel></rss>')
        self.assertEqual(feed.items[0].published_at, 1704189600)

    def test_a_json_feed_item_dated_only_by_its_change_keeps_a_date(self) -> None:
        feed = parse_feed('{"version":"https://jsonfeed.org/version/1.1","title":"T",'
                          '"items":[{"id":"1","content_text":"x",'
                          '"date_modified":"2024-01-02T10:00:00Z"}]}')
        self.assertEqual(feed.items[0].published_at, 1704189600)


class EmbedTests(unittest.TestCase):
    def test_a_video_embed_survives_parsing(self) -> None:
        feed = parse_feed(
            '<?xml version="1.0"?><rss version="2.0" '
            'xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>'
            '<title>T</title><item><title>P</title><guid>1</guid><content:encoded>'
            '<![CDATA[<p>Watch:</p><iframe src="https://www.youtube.com/embed/abc123XYZ">'
            '</iframe>]]></content:encoded></item></channel></rss>')
        self.assertIn("youtube.com/embed/abc123XYZ", feed.items[0].content_html)


class WholeCoverTests(unittest.TestCase):
    def test_a_relative_cover_is_made_whole_against_the_post(self) -> None:
        feed = with_whole_links(parse_feed(
            '<?xml version="1.0"?><rss version="2.0" '
            'xmlns:media="http://search.yahoo.com/mrss/"><channel><title>T</title>'
            '<link>https://blog.example/</link><item><title>P</title>'
            '<link>https://blog.example/2026/post</link>'
            '<media:content url="/media/cover.jpg" medium="image"/></item>'
            '<item><title>Q</title><link>https://blog.example/q</link>'
            '<media:content url="//cdn.example/q.jpg" medium="image"/></item>'
            '</channel></rss>'), "https://blog.example/feed")
        self.assertEqual(feed.items[0].image, "https://blog.example/media/cover.jpg")
        self.assertEqual(feed.items[1].image, "https://cdn.example/q.jpg")


if __name__ == "__main__":
    unittest.main()
