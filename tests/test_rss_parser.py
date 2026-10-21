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
        # A description without markup is text, made the HTML it means.
        self.assertEqual(item.content_html, "<p>This post has no content:encoded.</p>")
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


def _rss(items: str, namespaces: str = "") -> str:
    return ('<?xml version="1.0"?><rss version="2.0" '
            'xmlns:content="http://purl.org/rss/1.0/modules/content/" '
            'xmlns:media="http://search.yahoo.com/mrss/" '
            'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
            f'{namespaces}><channel><title>T</title>{items}</channel></rss>')


class CoverTests(unittest.TestCase):
    def test_an_episode_audio_is_no_cover(self) -> None:
        item = parse_feed(_rss(
            '<item><title>Ep</title><guid>1</guid>'
            '<media:content url="https://cdn.example/ep1.mp3" type="audio/mpeg"/>'
            '<media:content url="https://cdn.example/ep1.mp4" medium="video"/>'
            '<itunes:image href="https://cdn.example/ep1.jpg"/></item>')).items[0]
        self.assertEqual(item.image, "https://cdn.example/ep1.jpg")

    def test_the_author_avatar_and_pixels_are_no_cover(self) -> None:
        item = parse_feed(_rss(
            '<item><title>P</title><guid>1</guid>'
            '<media:content url="https://0.gravatar.com/avatar/abc?s=96" medium="image"/>'
            '<content:encoded><![CDATA[<img src="https://pixel.wp.com/b.gif?x=1">'
            '<img src="https://s.w.org/images/core/emoji/15/svg/1f642.svg" class="wp-smiley">'
            '<img src="https://b.example/tiny.gif" width="1" height="1">'
            '<img src="data:image/gif;base64,R0lG" data-src="https://b.example/real.jpg">'
            ']]></content:encoded></item>')).items[0]
        self.assertEqual(item.image, "https://b.example/real.jpg")

    def test_an_atom_entry_takes_its_media_thumbnail(self) -> None:
        item = parse_feed(
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:media="http://search.yahoo.com/mrss/"><title>B</title>'
            '<entry><id>1</id><title>P</title><content type="html">&lt;p&gt;x&lt;/p&gt;</content>'
            '<media:thumbnail url="https://b.example/thumb.jpg"/></entry></feed>').items[0]
        self.assertEqual(item.image, "https://b.example/thumb.jpg")


class TextBodyTests(unittest.TestCase):
    def test_atom_text_content_keeps_its_paragraphs_and_characters(self) -> None:
        item = parse_feed(
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>B</title>'
            '<entry><id>1</id><title>P</title><content type="text">First line.\n'
            'Second &lt;tag&gt; line.\n\nNew paragraph.</content></entry></feed>').items[0]
        self.assertEqual(item.content_html,
                         "<p>First line.<br>Second &lt;tag&gt; line.</p><p>New paragraph.</p>")

    def test_json_feed_content_text_is_text(self) -> None:
        item = parse_feed('{"version":"https://jsonfeed.org/version/1.1","title":"T",'
                          '"items":[{"id":"1","content_text":"A <b> is no tag.\\n\\nTwo."}]}'
                          ).items[0]
        self.assertEqual(item.content_html, "<p>A &lt;b&gt; is no tag.</p><p>Two.</p>")

    def test_html_show_notes_win_over_the_itunes_summary(self) -> None:
        item = parse_feed(_rss(
            '<item><title>Ep</title><guid>1</guid>'
            '<description><![CDATA[<p>Notes with <a href="https://x.example">a link</a></p>]]>'
            '</description><itunes:summary>Plain trimmed summary</itunes:summary></item>'
        )).items[0]
        self.assertIn('<a href="https://x.example">a link</a>', item.content_html)


class TitleTests(unittest.TestCase):
    def test_a_title_written_as_html_is_plain_text(self) -> None:
        rss = parse_feed(_rss('<item><title><![CDATA[Joe&#8217;s <b>bold</b> post]]></title>'
                              '<guid>1</guid></item>')).items[0]
        self.assertEqual(rss.title, "Joe\u2019s bold post")
        atom = parse_feed(
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>B</title>'
            '<entry><id>1</id><title type="html">Tom &amp;amp; Jerry &lt;em&gt;live&lt;/em&gt;'
            '</title></entry><entry><id>2</id><title type="xhtml"><div '
            'xmlns="http://www.w3.org/1999/xhtml">X<em>html</em> title</div></title></entry>'
            '</feed>')
        self.assertEqual([i.title for i in atom.items], ["Tom & Jerry live", "Xhtml title"])

    def test_a_text_title_keeps_its_angle_brackets(self) -> None:
        item = parse_feed(_rss('<item><title>Using &lt;div&gt; tags</title><guid>1</guid>'
                               '</item>')).items[0]
        self.assertEqual(item.title, "Using <div> tags")


class YouTubeTests(unittest.TestCase):
    def test_a_channel_video_is_its_watch_page_and_description(self) -> None:
        item = parse_feed(
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:yt="http://www.youtube.com/xml/schemas/2015" '
            'xmlns:media="http://search.yahoo.com/mrss/"><title>Channel</title>'
            '<entry><id>yt:video:abc123XYZ</id><yt:videoId>abc123XYZ</yt:videoId>'
            '<title>Video</title><link rel="alternate" href="https://www.youtube.com/watch?v=abc123XYZ"/>'
            '<media:group><media:thumbnail url="https://i.ytimg.com/vi/abc123XYZ/hqdefault.jpg"/>'
            '<media:description>Line one\nLine two\n\nPara two</media:description></media:group>'
            '</entry></feed>').items[0]
        self.assertTrue(item.content_html.startswith(
            '<p><a href="https://www.youtube.com/watch?v=abc123XYZ">'))
        self.assertIn("<p>Line one<br>Line two</p><p>Para two</p>", item.content_html)
        self.assertEqual(item.image, "https://i.ytimg.com/vi/abc123XYZ/hqdefault.jpg")


if __name__ == "__main__":
    unittest.main()
