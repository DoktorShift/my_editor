# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a list row shows of a post that comes as Markdown (review M4).

Nostr authors, NostrHub and GitHub folders deliver Markdown, not HTML.
Their rows used to show the raw Markdown as the excerpt, no read time,
no image count and no cover.
"""

from __future__ import annotations

from nostr.imports import snapshots
from nostr.imports.workspace import post_from_item
from tests.imports_fakes import make_item

MARKDOWN = ("## Why self-custody matters\n\n"
            "![cover shot](https://img.example/cover.jpg)\n\n"
            "**Every cycle** brings a new reason to hand your keys to "
            "[someone else](https://example.com). " + "More words here. " * 500
            + "\n\n![second](https://img.example/two.png \"title\")\n")


def markdown_item():
    return make_item("A Nostr article", guid="g-md", content_html="",
                     content_markdown=MARKDOWN)


def test_the_excerpt_is_words_not_markdown():
    text = snapshots.excerpt(markdown_item())
    assert text.startswith("Why self-custody matters Every cycle brings a new reason")
    assert "![" not in text and "**" not in text and "](" not in text


def test_read_time_and_images_come_from_the_markdown():
    item = markdown_item()
    assert snapshots.minutes_of(item) == 7
    assert snapshots.images_in(item) == 2
    assert snapshots.cover(item) == "https://img.example/cover.jpg"


def test_the_row_of_a_markdown_post():
    post = post_from_item(markdown_item(), collection="m-x")
    assert (post.read_minutes, post.image_count) == (7, 2)
    assert post.image == "https://img.example/cover.jpg"
    assert not post.excerpt.startswith("##")


def test_html_posts_are_read_as_before():
    item = make_item("HTML", guid="g-h", content_html="<p>" + "word " * 450 + "</p>"
                     "<img src='https://img.example/a.png'>")
    assert snapshots.minutes_of(item) == 2
    assert snapshots.images_in(item) == 1
    assert snapshots.cover(item) == "https://img.example/a.png"
