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

    def test_a_sentence_like_it_in_the_middle_stays(self) -> None:
        out = md('<p>The post office appeared first on the map.</p><p>More.</p><p>End.</p>')
        self.assertIn("post office", out)


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

    def test_a_block_without_a_language_has_a_plain_fence(self) -> None:
        self.assertTrue(md("<pre><code>x</code></pre>").startswith("```\n"))


if __name__ == "__main__":
    unittest.main()
