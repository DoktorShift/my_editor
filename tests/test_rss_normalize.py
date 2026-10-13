# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for ``nostr.rss.normalize``."""

from __future__ import annotations

import unittest

from nostr.rss.normalize import (
    ArticleTemplate,
    html_to_markdown,
    item_to_article,
)
from nostr.rss.parser import FeedItem


def _item(**overrides) -> FeedItem:
    defaults: dict = dict(
        guid="g-1",
        title="Sample post",
        link="https://example.com/p/1",
        summary="A short summary.",
        content_html="<h1>Hello</h1><p>Body <em>here</em>.</p>",
        published_at=1741773600,
        categories=("tech", "nostr"),
        image="https://example.com/cover.jpg",
        author="Alice",
    )
    defaults.update(overrides)
    return FeedItem(**defaults)


class HtmlToMarkdownTests(unittest.TestCase):
    def test_atx_headings(self) -> None:
        self.assertEqual(html_to_markdown("<h2>Hi</h2>").strip(), "## Hi")

    def test_bullets_use_dash(self) -> None:
        md = html_to_markdown("<ul><li>a</li><li>b</li></ul>")
        self.assertIn("- a", md)
        self.assertIn("- b", md)

    def test_emphasis_uses_underscore(self) -> None:
        md = html_to_markdown("<p><em>foo</em></p>")
        self.assertIn("_foo_", md)

    def test_strong_uses_double_underscore(self) -> None:
        md = html_to_markdown("<p><strong>bar</strong></p>")
        self.assertIn("__bar__", md)

    def test_empty_input_returns_empty_string(self) -> None:
        self.assertEqual(html_to_markdown(""), "")


class ItemToArticleTests(unittest.TestCase):
    def test_returns_template_dataclass(self) -> None:
        template = item_to_article(_item())
        self.assertIsInstance(template, ArticleTemplate)
        self.assertEqual(template.title, "Sample post")
        self.assertEqual(template.summary, "A short summary.")
        self.assertEqual(template.image, "https://example.com/cover.jpg")
        self.assertEqual(template.published_at, 1741773600)

    def test_slug_is_deterministic_for_same_inputs(self) -> None:
        a = item_to_article(_item())
        b = item_to_article(_item())
        self.assertEqual(a.slug, b.slug)

    def test_slug_uses_prefix_when_provided(self) -> None:
        template = item_to_article(_item(), identifier_prefix="rss-")
        self.assertTrue(template.slug.startswith("rss-"))

    def test_categories_become_hashtags_lowercased(self) -> None:
        template = item_to_article(_item(categories=("Tech", "Nostr")))
        self.assertEqual(template.hashtags, ("tech", "nostr"))

    def test_extra_hashtags_merged_and_deduped(self) -> None:
        template = item_to_article(
            _item(categories=("tech",)),
            hashtags=("Tech", "Imported"),
        )
        self.assertEqual(template.hashtags, ("tech", "imported"))

    def test_hashtag_hash_prefix_stripped(self) -> None:
        template = item_to_article(_item(categories=("#bitcoin",)))
        self.assertEqual(template.hashtags, ("bitcoin",))

    def test_source_footer_appended_by_default(self) -> None:
        template = item_to_article(_item())
        self.assertIn("Originally published at", template.content)
        self.assertIn("https://example.com/p/1", template.content)

    def test_source_footer_skipped_when_opted_out(self) -> None:
        template = item_to_article(_item(), append_source_link=False)
        self.assertNotIn("Originally published at", template.content)

    def test_source_footer_skipped_when_no_link(self) -> None:
        template = item_to_article(_item(link=None))
        self.assertNotIn("Originally published at", template.content)

    def test_missing_summary_becomes_empty_string(self) -> None:
        template = item_to_article(_item(summary=None))
        self.assertEqual(template.summary, "")

    def test_missing_image_becomes_empty_string(self) -> None:
        template = item_to_article(_item(image=None))
        self.assertEqual(template.image, "")

    def test_empty_html_yields_just_the_footer(self) -> None:
        template = item_to_article(_item(content_html=""))
        self.assertTrue(template.content.startswith("---"))
        self.assertIn("Originally published at", template.content)



class RelativeAddressTests(unittest.TestCase):
    """CP1 review O3: relative links and images of a feed's body stayed
    relative, so they pointed nowhere in a draft or an article."""

    HTML = ("<p>See <a href='/about'>about</a>, <a href='#notes'>notes</a>, "
            "<a href='mailto:a@b.example'>mail</a> and <a href='//cdn.example/x'>cdn</a>."
            "</p><p><img src='../media/pic.jpg' alt='A pic'></p>")

    def test_they_are_made_whole_against_the_post_page(self) -> None:
        md = html_to_markdown(self.HTML, base_url="https://blog.example/2026/10/post")
        self.assertIn("[about](https://blog.example/about)", md)
        self.assertIn("![A pic](https://blog.example/2026/media/pic.jpg)", md)
        self.assertIn("[cdn](https://cdn.example/x)", md)
        self.assertIn("[notes](#notes)", md)                # a place in the post
        self.assertIn("[mail](mailto:a@b.example)", md)

    def test_without_a_page_they_stay(self) -> None:
        self.assertIn("[about](/about)", html_to_markdown(self.HTML))
        self.assertIn("[about](/about)", html_to_markdown(self.HTML, base_url="ftp://x/"))

    def test_a_draft_gets_whole_addresses(self) -> None:
        template = item_to_article(_item(link="https://blog.example/2026/10/post",
                                         content_html=self.HTML))
        self.assertIn("https://blog.example/2026/media/pic.jpg", template.content)

    def test_the_images_to_copy_are_whole(self) -> None:
        from nostr.imports.snapshots import images_to_copy
        item = _item(link="https://blog.example/2026/10/post", image=None,
                     content_html=self.HTML)
        self.assertEqual(images_to_copy([item]), ["https://blog.example/2026/media/pic.jpg"])


if __name__ == "__main__":
    unittest.main()
