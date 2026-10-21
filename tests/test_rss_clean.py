# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A post's HTML made ready for Markdown (``nostr.rss.clean``), seen
through ``html_to_markdown``, the one way every import converts HTML."""

from __future__ import annotations

import unittest

from nostr.rss.normalize import html_to_markdown

PAGE = "https://blog.example/2026/post"


def md(html: str) -> str:
    return html_to_markdown(html, base_url=PAGE).strip()


class LazyImageTests(unittest.TestCase):
    def test_the_real_picture_replaces_a_placeholder(self) -> None:
        out = md('<img src="data:image/gif;base64,R0lGOD" data-src="/a.jpg" alt="A">')
        self.assertEqual(out, "![A](https://blog.example/a.jpg)")

    def test_a_lazy_load_placeholder_file_is_replaced(self) -> None:
        out = md('<img src="/img/lazy-placeholder.png" data-lazy-src="/b.jpg">')
        self.assertIn("https://blog.example/b.jpg", out)
        self.assertNotIn("placeholder", out)

    def test_the_widest_srcset_candidate_when_there_is_no_src(self) -> None:
        out = md('<img data-srcset="/s.jpg 300w, /m.jpg 768w, /l.jpg 1600w">')
        self.assertEqual(out, "![](https://blog.example/l.jpg)")

    def test_the_noscript_copy_is_kept_once(self) -> None:
        out = md('<img src="data:," data-src="/c.jpg"><noscript><img src="/c.jpg"></noscript>')
        self.assertEqual(out.count("c.jpg"), 1)

    def test_a_noscript_image_that_is_the_only_copy_is_kept(self) -> None:
        self.assertIn("https://blog.example/d.jpg", md('<noscript><img src="/d.jpg"></noscript>'))

    def test_an_image_with_nothing_to_show_is_dropped(self) -> None:
        self.assertEqual(md('<p>Text</p><img src="data:image/gif;base64,R0lGOD">'), "Text")


class EmbedTests(unittest.TestCase):
    def test_a_youtube_player_becomes_its_watch_page(self) -> None:
        for src in ("https://www.youtube.com/embed/abc123XYZ?rel=0",
                    "https://www.youtube-nocookie.com/embed/abc123XYZ",
                    "//www.youtube.com/embed/abc123XYZ"):
            self.assertIn("https://www.youtube.com/watch?v=abc123XYZ",
                          md(f'<iframe src="{src}"></iframe>'), src)

    def test_a_vimeo_player_becomes_its_page(self) -> None:
        self.assertIn("https://vimeo.com/76979871",
                      md('<iframe src="https://player.vimeo.com/video/76979871"></iframe>'))

    def test_another_known_player_keeps_its_address(self) -> None:
        src = "https://open.spotify.com/embed/episode/7makk4oTQel546B0PZlDM5"
        self.assertIn(src, md(f'<iframe src="{src}"></iframe>'))

    def test_a_youtube_playlist_is_its_playlist_page(self) -> None:
        self.assertIn("https://www.youtube.com/playlist?list=PL123",
                      md('<iframe src="https://www.youtube.com/embed/videoseries?list=PL123">'
                         '</iframe>'))

    def test_the_old_flash_player_is_its_watch_page(self) -> None:
        self.assertIn("https://www.youtube.com/watch?v=abc123XYZ",
                      md('<object data="https://www.youtube.com/v/abc123XYZ"></object>'))
        self.assertIn("https://www.youtube.com/watch?v=abc123XYZ",
                      md('<embed src="https://www.youtube.com/v/abc123XYZ">'))

    def test_a_peertube_player_is_its_watch_page(self) -> None:
        self.assertIn("https://videos.example/videos/watch/9c9de5e8",
                      md('<iframe src="https://videos.example/videos/embed/9c9de5e8?start=1">'
                         '</iframe>'))

    def test_an_embedded_tweet_is_its_page(self) -> None:
        self.assertIn("https://twitter.com/i/web/status/20",
                      md('<iframe src="https://platform.twitter.com/embed/Tweet.html?id=20">'
                         '</iframe>'))

    def test_an_unknown_frame_is_dropped(self) -> None:
        self.assertEqual(md('<p>Hi</p><iframe src="https://ads.example/x"></iframe>'), "Hi")

    def test_video_and_audio_become_a_line_with_their_address(self) -> None:
        out = md('<video src="/v.mp4" controls></video>'
                 '<audio controls><source src="https://cdn.example/e.mp3"></audio>')
        self.assertIn("https://blog.example/v.mp4", out)
        self.assertIn("https://cdn.example/e.mp3", out)
        self.assertNotIn("[](", out)


class FurnitureTests(unittest.TestCase):
    def test_page_furniture_leaves_no_words(self) -> None:
        out = md('<p>Body</p><form><input value="x"><button>Subscribe</button></form>'
                 '<svg><text>icon</text></svg><script>track()</script>'
                 '<style>p{}</style><!-- comment -->')
        self.assertEqual(out, "Body")

    def test_an_empty_link_is_dropped(self) -> None:
        self.assertEqual(md('<p>Hi<a href="/x"></a></p>'), "Hi")

    def test_a_linked_image_is_kept(self) -> None:
        self.assertIn("[![](https://blog.example/i.jpg)](https://blog.example/full.jpg)",
                      md('<a href="/full.jpg"><img src="/i.jpg"></a>'))

    def test_the_appeared_first_line_is_dropped(self) -> None:
        out = md('<p>Real ending.</p><p>The post <a href="/p">Post</a> appeared first on '
                 '<a href="/">Blog</a>.</p>')
        self.assertEqual(out, "Real ending.")
        out = md('<p>Ende.</p><p>Der Beitrag <a href="/p">X</a> erschien zuerst auf '
                 '<a href="/">Y</a>.</p>')
        self.assertEqual(out, "Ende.")

    def test_the_appeared_first_line_in_yoast_and_aioseo_translations(self) -> None:
        lines = (
            "The post <a>X</a> first appeared on <a>Y</a>.",
            "Het bericht <a>X</a> verscheen eerst op <a>Y</a>.",
            "Cet article <a>X</a> est apparu en premier sur <a>Y</a>.",
            "La entrada <a>X</a> se publicó primero en <a>Y</a>.",
            "L'articolo <a>X</a> proviene da <a>Y</a>.",
            "Artykuł <a>X</a> pochodzi z serwisu <a>Y</a>.",
            "O post <a>X</a> apareceu primeiro em <a>Y</a>.",
            "O conteúdo <a>X</a> aparece primeiro em <a>Y</a>.",
        )
        for line in lines:
            self.assertEqual(md(f"<p>Real ending.</p><p>{line}</p>"), "Real ending.", line)

    def test_a_sentence_like_it_in_the_middle_stays(self) -> None:
        out = md('<p>The post office appeared first on the map.</p><p>More.</p><p>End.</p>')
        self.assertIn("post office", out)


class AddressPolicyTests(unittest.TestCase):
    """feedparser's cleaning is off, so clean.py is where a link or a
    picture that is no web address goes."""

    def test_a_script_or_data_link_keeps_its_words_and_loses_the_link(self) -> None:
        out = md('<p>hi <a href="javascript:alert(1)">click</a> '
                 '<a href="data:text/html,x">d</a> <a href="vbscript:x">v</a></p>')
        self.assertEqual(out, "hi click d v")

    def test_web_mail_and_relative_links_stay(self) -> None:
        out = md('<p><a href="https://x.example">a</a> <a href="mailto:me@x.example">m</a> '
                 '<a href="/about">r</a></p>')
        self.assertIn("(https://x.example)", out)
        self.assertIn("(mailto:me@x.example)", out)
        self.assertIn("(https://blog.example/about)", out)

    def test_a_picture_or_player_that_is_no_web_address_is_dropped(self) -> None:
        out = md('<p>t</p><img src="javascript:x"><img src="file:///etc/x.png">'
                 '<video src="javascript:x"></video>')
        self.assertEqual(out, "t")

    def test_a_protocol_relative_pixel_is_dropped(self) -> None:
        self.assertEqual(md('<p>t</p><img src="//pixel.wp.com/b.gif?x=1">'), "t")


class CodeLanguageTests(unittest.TestCase):
    def test_the_fence_keeps_the_language(self) -> None:
        cases = {
            '<pre><code class="language-python">x = 1</code></pre>': "```python",
            '<pre class="lang-js"><code>x</code></pre>': "```js",
            '<pre data-lang="Rust"><code>x</code></pre>': "```rust",
            '<pre class="brush: php; title: ; notranslate">x</pre>': "```php",
        }
        for html, fence in cases.items():
            self.assertIn(fence, md(html), html)

    def test_a_class_that_names_no_language_gives_none(self) -> None:
        self.assertTrue(md('<pre class="highlight-text-html-basic"><code>x</code></pre>')
                        .startswith("```\n"))

    def test_a_block_without_a_language_has_a_plain_fence(self) -> None:
        self.assertTrue(md("<pre><code>x</code></pre>").startswith("```\n"))


if __name__ == "__main__":
    unittest.main()
