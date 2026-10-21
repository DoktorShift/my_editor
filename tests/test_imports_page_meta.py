# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The details a post's own page states (``nostr.imports.page_meta``)."""

from __future__ import annotations

import unittest

from nostr.imports.page_meta import PageMeta, fill_from_page, read_page_meta
from tests.imports_fakes import make_item

PAGE = "https://blog.example/2026/post"


def page(head: str) -> str:
    return f"<html><head>{head}</head><body><p>x</p></body></html>"


class ReadTests(unittest.TestCase):
    def test_open_graph_and_article_tags(self) -> None:
        meta = read_page_meta(page(
            '<meta property="og:title" content="The Title">'
            '<meta property="og:description" content="  What   it is about. ">'
            '<meta property="og:image" content="/img/cover.jpg">'
            '<meta property="article:published_time" content="2024-01-02T10:00:00+00:00">'
            '<meta property="article:tag" content="Bitcoin">'
            '<meta property="article:tag" content="Lightning">'
            '<meta property="article:author" content="https://blog.example/about">'
            '<meta name="author" content="Jane Doe">'), url=PAGE)
        self.assertEqual(meta, PageMeta(
            title="The Title", description="What it is about.",
            image="https://blog.example/img/cover.jpg", published_at=1704189600,
            author="Jane Doe", tags=("Bitcoin", "Lightning")))

    def test_json_ld_wins_and_is_found_in_a_graph(self) -> None:
        meta = read_page_meta(page(
            '<meta property="og:image" content="https://blog.example/og.jpg">'
            '<script type="application/ld+json">{"@context":"https://schema.org","@graph":['
            '{"@type":"WebSite","name":"Blog"},'
            '{"@type":["BlogPosting"],"headline":"Headline","description":"LD text",'
            '"image":[{"@type":"ImageObject","url":"https://blog.example/ld.jpg"}],'
            '"datePublished":"2024-01-02","keywords":"nostr, zaps",'
            '"author":[{"@type":"Person","name":"Jane"},{"@type":"Person","name":"Joe"}]}'
            ']}</script>'), url=PAGE)
        self.assertEqual(meta.title, "Headline")
        self.assertEqual(meta.image, "https://blog.example/ld.jpg")
        self.assertEqual(meta.published_at, 1704153600)
        self.assertEqual(meta.tags, ("nostr", "zaps"))
        self.assertEqual(meta.author, "Jane, Joe")

    def test_broken_json_ld_falls_back_to_the_meta_tags(self) -> None:
        meta = read_page_meta(page(
            '<script type="application/ld+json">{not json</script>'
            '<meta name="description" content="Plain description">'
            '<meta name="twitter:image" content="https://cdn.example/t.jpg">'), url=PAGE)
        self.assertEqual(meta.description, "Plain description")
        self.assertEqual(meta.image, "https://cdn.example/t.jpg")

    def test_a_cover_that_is_no_web_address_is_none(self) -> None:
        meta = read_page_meta(page('<meta property="og:image" content="data:image/png;base64,A">'),
                              url=PAGE)
        self.assertEqual(meta.image, "")

    def test_an_unreadable_page_states_nothing(self) -> None:
        self.assertEqual(read_page_meta("", url=PAGE), PageMeta())
        self.assertEqual(read_page_meta(None, url=PAGE), PageMeta())


class FillTests(unittest.TestCase):
    META = PageMeta(title="Page Title", description="Page text", image="https://b.example/c.jpg",
                    published_at=1, author="Jane", tags=("a",))

    def test_only_what_the_item_lacks_is_filled(self) -> None:
        item = make_item("Own Title", link=PAGE, summary="Own text", image="https://b.example/own.jpg",
                         published_at=5, author="Own", categories=("own",))
        self.assertIs(fill_from_page(item, self.META), item)

    def test_a_bare_item_takes_everything(self) -> None:
        item = fill_from_page(make_item("slug-title", link=PAGE, title_from_url=True), self.META)
        self.assertEqual((item.title, item.summary, item.image, item.published_at, item.author,
                          item.categories, item.title_from_url),
                         ("Page Title", "Page text", "https://b.example/c.jpg", 1, "Jane",
                          ("a",), False))

    def test_the_reader_title_stands_in_when_the_page_states_none(self) -> None:
        item = fill_from_page(make_item("slug", link=PAGE, title_from_url=True), PageMeta(),
                              page_title="Readable Title")
        self.assertEqual(item.title, "Readable Title")


if __name__ == "__main__":
    unittest.main()
